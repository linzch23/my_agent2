from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .contextfs import ContextFS, ContextObject
from .memory_graph import MemoryGraph


UTC8 = timezone(timedelta(hours=8))


class MemoryStore:
    def __init__(self, memory_dir: Path, user_file: Path | None = None) -> None:
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.memory_dir / "history.jsonl"
        self.memory_path = self.memory_dir / "MEMORY.md"
        self.compactions_path = self.memory_dir / "compactions.md"
        self.user_file = user_file or self.memory_dir / "USER.md"
        if not self.memory_path.exists():
            self.memory_path.write_text("# Long-term Memory\n\n", encoding="utf-8")
        if not self.compactions_path.exists():
            self.compactions_path.write_text("# Conversation Compactions\n\n", encoding="utf-8")
        if not self.user_file.exists():
            self.user_file.parent.mkdir(parents=True, exist_ok=True)
            self.user_file.write_text("# User Profile\n\n", encoding="utf-8")

        # Memory OS: ContextFS + MemoryGraph
        self._cfs = ContextFS(self.memory_dir)
        self._graph = MemoryGraph(self.memory_dir / "context" / "links.jsonl")
        self._auto_link_client = None  # set externally by AgentApp
        self._auto_link_model = ""

    def set_auto_link_client(self, client: Any, model: str) -> None:
        """Set the LLM client for MemoryGraph auto_link. Called by AgentApp."""
        self._auto_link_client = client
        self._auto_link_model = model

    def append_history(self, role: str, content: Any) -> None:
        record = {
            "ts": datetime.now(UTC8).isoformat(timespec="seconds"),
            "role": role,
            "content": content if isinstance(content, str) else repr(content),
        }
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_memory(self) -> str:
        return self.memory_path.read_text(encoding="utf-8").strip()

    def write_memory(self, content: str) -> None:
        self.memory_path.write_text(content.strip() + "\n", encoding="utf-8")

    def append_memory(self, note: str) -> None:
        note = note.strip()
        if not note:
            return
        with self.memory_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n- {note}\n")

    def read_user(self) -> str:
        return self.user_file.read_text(encoding="utf-8").strip()

    def write_user(self, content: str) -> None:
        self.user_file.write_text(content.strip() + "\n", encoding="utf-8")

    def today_episode_path(self) -> Path:
        return self.memory_dir / f"{datetime.now(UTC8).strftime('%Y-%m-%d')}.md"

    def read_today_episode(self) -> str:
        path = self.today_episode_path()
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def append_episode(self, content: str) -> None:
        content = content.strip()
        if not content:
            return
        path = self.today_episode_path()
        existing = path.read_text(encoding="utf-8") if path.exists() else f"# {path.stem} Episode Memory\n"
        path.write_text(existing.rstrip() + "\n\n" + content + "\n", encoding="utf-8")

    def append_compaction(
        self,
        *,
        stamp: str,
        summary: str,
        old_count: int,
        append_to_memory: bool = False,
    ) -> None:
        summary = summary.strip()
        with self.compactions_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n## {stamp} ({old_count} messages)\n\n{summary}\n")
        if append_to_memory:
            with self.memory_path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n## Compressed Context: {stamp}\n\n{summary}\n")

    def append_compact_marker(self) -> None:
        record = {"ts": datetime.now(UTC8).isoformat(timespec="seconds"), "type": "compact_event"}
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load_unarchived_history(self) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        rows = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        last_marker = -1
        for index, row in enumerate(rows):
            if row.get("type") == "compact_event":
                last_marker = index
        return [
            {"role": row["role"], "content": row["content"]}
            for row in rows[last_marker + 1 :]
            if "role" in row and "content" in row
        ]

    # ---- Memory OS new API ----

    def remember_note(self, note: str, category: str = "events", title: str | None = None) -> str:
        import re
        note = note.strip()
        if not note:
            return ""
        title = title or (note[:60] + "..." if len(note) > 60 else note)
        slug = re.sub(r"[^\w\s一-鿿-]", "", title.lower().strip())
        slug = re.sub(r"\s+", "-", slug)[:80]
        now = datetime.now(UTC8)
        uri = _operation_to_uri(category, slug, now)

        # 搜索同 category 的已有记忆，按 bigram 重叠判断是否需要更新
        old_uri = None
        prefix = _category_prefix(category)
        existing = self._cfs.list_objects(prefix=prefix, limit=100)
        best_overlap = 0
        for e in existing:
            if e.get("uri") == uri:
                old_uri = uri
                break
            if e.get("status") != "active":
                continue
            e_title = e.get("title", "")
            overlap = _count_bigram_overlap(title.lower(), e_title.lower())
            if overlap > best_overlap:
                best_overlap = overlap
                old_uri = e.get("uri")

        # 如果有相似的旧记忆（bigram 重叠 ≥2），标记为 archived
        if old_uri and old_uri != uri and best_overlap >= 2:
            try:
                old_entry = self._cfs.read_object(old_uri, layer="auto")
                old_obj = ContextObject(
                    uri=old_uri, context_type="memory",
                    title=old_entry.get("title", ""),
                    abstract=old_entry.get("abstract", ""),
                    overview=old_entry.get("overview", ""),
                    content_path=old_entry.get("content_path", ""),
                    source=old_entry.get("source", "manual"),
                    trust_score=old_entry.get("trust_score", 0.5),
                    sensitivity=old_entry.get("sensitivity", "public"),
                    status="archived",
                    tags=old_entry.get("tags", []),
                    metadata={**old_entry.get("metadata", {}), "superseded_by": uri},
                    digest="",
                    created_at=old_entry.get("created_at", ""),
                    updated_at=now.isoformat(),
                )
                old_content = self._cfs.read_object(old_uri, layer="full")
                self._cfs.write_object(old_obj, old_content.get("content", ""))
            except Exception:
                pass

        content_rel = uri.replace("://", "/") + ".md"
        obj = ContextObject(
            uri=uri, context_type="memory", title=title,
            abstract=note[:200], overview=note,
            content_path=content_rel,
            source="manual", trust_score=0.8, sensitivity="public",
            status="active", tags=[category],
            metadata={"written_by": "remember_tool"}, digest="",
            created_at=now.isoformat(), updated_at="",
        )
        self._cfs.write_object(obj, note)
        self._cfs.append_diff({"action": "remember", "uri": uri, "reason": "manual remember"})
        if old_uri and old_uri != uri:
            self._graph.add_link(uri, old_uri, "updates", 0.9,
                                 f"bigram_overlap={best_overlap}")
        if self._auto_link_client:
            self._graph.auto_link(uri, self._cfs, self._auto_link_client, self._auto_link_model)
        self.append_memory(f"[{category}] {note}")
        return uri

    def commit_session_archive(
        self, session_uri: str, summary: str, operations: list[dict[str, Any]], metadata: dict[str, Any]
    ) -> str:
        import json
        now = datetime.now(UTC8)
        date_part = now.strftime("%Y/%m/%d")
        slug = session_uri.split("/")[-1]

        # write session archive
        archive_obj = ContextObject(
            uri=session_uri, context_type="session", title=f"Session Archive {slug}",
            abstract=summary[:200], overview=summary,
            content_path=f"sessions/archives/{date_part}/{slug}.md",
            source="compaction", trust_score=0.7, sensitivity="internal",
            status="archived", tags=["session-archive"],
            metadata=metadata, digest="",
            created_at=now.isoformat(), updated_at="",
        )
        archive_content = _build_archive_content(summary, metadata)
        self._cfs.write_object(archive_obj, archive_content)

        # process operations
        valid_categories = {"profile", "preferences", "entities", "events",
                            "decisions", "constraints", "open_tasks",
                            "cases", "patterns", "tools", "skills"}
        for op in operations:
            action = op.get("action", "")
            category = op.get("category", "")
            key = op.get("key", "")
            if action not in ("upsert", "append", "quarantine"):
                _write_quarantine(self._cfs, op, f"Invalid action: {action}")
                self._cfs.append_diff({"action": "quarantine", "reason": f"invalid action: {action}", "operation": op})
                continue
            if category not in valid_categories:
                _write_quarantine(self._cfs, op, f"Invalid category: {category}")
                self._cfs.append_diff({"action": "quarantine", "reason": f"invalid category: {category}", "operation": op})
                continue

            if action == "quarantine":
                _write_quarantine(self._cfs, op, op.get("reason", "manual quarantine"))
                continue

            uri = _operation_to_uri(category, key, now)
            mem_obj = ContextObject(
                uri=uri, context_type="memory",
                title=op.get("title", key),
                abstract=op.get("abstract", ""),
                overview=op.get("overview", ""),
                content_path=f"mem/{category}/{key}.md",
                source="compaction", trust_score=float(op.get("trust_score", 0.6)),
                sensitivity="public", status="active",
                tags=op.get("tags", []) + [category],
                metadata={"source_session": session_uri, "reason": op.get("reason", "")},
                digest="", created_at=now.isoformat(), updated_at="",
            )
            self._cfs.write_object(mem_obj, op.get("content", op.get("overview", "")))

            # derived_from link
            self._graph.add_link(uri, session_uri, "derived_from", 0.95,
                                 f"extracted from {session_uri}")

            # explicit links from operations
            for link in op.get("links", []):
                self._graph.add_link(uri, link["target_uri"], link["relation"],
                                     link.get("confidence", 0.5), link.get("reason", ""))

            # auto_link
            if self._auto_link_client:
                self._graph.auto_link(uri, self._cfs, self._auto_link_client, self._auto_link_model)

            self._cfs.append_diff({"action": action, "uri": uri, "category": category,
                                   "reason": op.get("reason", ""), "session_uri": session_uri})

        return session_uri

    def search_memory(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        return self._cfs.search_objects(query, limit=limit)

    def read_context(self, uri: str, layer: str = "auto") -> str:
        try:
            result = self._cfs.read_object(uri, layer=layer)
            return result.get("content", "")
        except KeyError:
            return f"Error: URI not found: {uri}"

    def list_context(self, prefix: str = "mem://", limit: int = 50) -> list[dict[str, Any]]:
        return self._cfs.list_objects(prefix=prefix, limit=limit)

    def graph_neighbors(self, uri: str, limit: int = 5) -> list[dict[str, Any]]:
        return self._graph.neighbors(uri, limit=limit)

    def render_memory(self) -> str:
        lines = ["# Memory OS（结构化长期记忆）"]
        categories = {
            "profile": "mem://user/profile",
            "preferences": "mem://user/preferences/",
            "entities": "mem://user/entities/",
            "events": "mem://user/events/",
            "decisions": "mem://project/decisions/",
            "constraints": "mem://project/constraints/",
            "open_tasks": "mem://project/open_tasks/",
            "cases": "mem://agent/cases/",
            "patterns": "mem://agent/patterns/",
            "tools": "mem://agent/tools/",
            "skills": "mem://agent/skills/",
        }
        has_items = False
        for name, prefix in categories.items():
            items = self._cfs.list_objects(prefix=prefix, limit=20)
            if items:
                has_items = True
                lines.append(f"\n## {name.title()}")
                for item in items:
                    lines.append(f"- [{item.get('title', '?')}]({item.get('uri', '')}) "
                                 f"trust={item.get('trust_score', 0):.1f}")
        if not has_items:
            lines.append("\n(暂无结构化记忆，通过对话中的 remember 或 /compact 来创建)")
        # legacy fallback
        if self.memory_path.exists():
            legacy = self.read_memory()
            if legacy.strip() != "# Long-term Memory":
                lines.append(f"\n---\n## Legacy（旧版 MEMORY.md 兼容保留）\n{legacy}")
        return "\n".join(lines)


class TokenLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.last_input_tokens: int | None = None

    def record(self, model: str, usage: Any) -> None:
        input_tokens = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None) or getattr(
            usage, "completion_tokens", None
        )
        self.last_input_tokens = input_tokens
        data = {
            "ts": datetime.now(UTC8).isoformat(timespec="seconds"),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False) + "\n")

    def should_compact(self, max_context_tokens: int, threshold: float) -> bool:
        if self.last_input_tokens is None:
            return False
        return self.last_input_tokens >= int(max_context_tokens * threshold)

    def _iter_rows(self):
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

    def stats_by_date(self) -> dict[str, dict[str, int]]:
        stats: dict[str, dict[str, int]] = {}
        for row in self._iter_rows() or []:
            date = row.get("ts", "")[:10] or "unknown"
            bucket = stats.setdefault(date, {"input_tokens": 0, "output_tokens": 0})
            bucket["input_tokens"] += row.get("input_tokens") or 0
            bucket["output_tokens"] += row.get("output_tokens") or 0
        return stats

    def stats_by_model(self) -> dict[str, dict[str, int]]:
        stats: dict[str, dict[str, int]] = {}
        for row in self._iter_rows() or []:
            model = row.get("model") or "unknown"
            bucket = stats.setdefault(model, {"input_tokens": 0, "output_tokens": 0})
            bucket["input_tokens"] += row.get("input_tokens") or 0
            bucket["output_tokens"] += row.get("output_tokens") or 0
        return stats


def _slugify(text: str) -> str:
    import re
    text = text.lower().strip()
    text = re.sub(r"[^\w\s一-鿿-]", "", text)
    text = re.sub(r"\s+", "-", text)
    return text[:80] if len(text) > 80 else text


def _operation_to_uri(category: str, key: str, now: Any) -> str:
    date_part = now.strftime("%Y/%m/%d")
    slug = _slugify(key)
    if category in ("profile",):
        return "mem://user/profile"
    if category in ("preferences", "entities"):
        return f"mem://user/{category}/{slug}"
    if category in ("events",):
        return f"mem://user/{category}/{date_part}/{slug}"
    if category in ("decisions", "constraints", "open_tasks"):
        return f"mem://project/{category}/{slug}"
    if category in ("cases", "patterns", "tools", "skills"):
        return f"mem://agent/{category}/{slug}"
    return f"mem://user/events/{date_part}/{slug}"


def _category_prefix(category: str) -> str:
    """Return the URI prefix for a given memory category."""
    if category in ("profile",):
        return "mem://user/profile"
    if category in ("preferences", "entities", "events"):
        return f"mem://user/{category}/"
    if category in ("decisions", "constraints", "open_tasks"):
        return f"mem://project/{category}/"
    if category in ("cases", "patterns", "tools", "skills"):
        return f"mem://agent/{category}/"
    return f"mem://user/events/"


def _count_bigram_overlap(title_a: str, title_b: str) -> int:
    """Count overlapping character bigrams between two titles."""
    a_bigrams = {title_a[i:i + 2] for i in range(len(title_a) - 1)} if len(title_a) >= 2 else set()
    b_bigrams = {title_b[i:i + 2] for i in range(len(title_b) - 1)} if len(title_b) >= 2 else set()
    return len(a_bigrams & b_bigrams)


def _write_quarantine(cfs: Any, op: dict[str, Any], reason: str) -> None:
    import json
    key = op.get("key", "unknown")
    slug = _slugify(f"{key}-{reason[:20]}")
    obj = ContextObject(
        uri=f"mem://quarantine/{slug}", context_type="memory",
        title=op.get("title", key), abstract=reason,
        overview=str(op), content_path=f"mem/quarantine/{slug}.md",
        source="compaction", trust_score=0.1, sensitivity="internal",
        status="quarantine", tags=["quarantine"],
        metadata={"original_operation": op}, digest="",
        created_at="", updated_at="",
    )
    cfs.write_object(obj, json.dumps(op, ensure_ascii=False, indent=2))


def _build_archive_content(summary: str, metadata: dict[str, Any]) -> str:
    import json
    parts = [
        "# Session Archive",
        "",
        "## Compaction Summary",
        summary,
        "",
        "## Metadata",
        "```json",
        json.dumps(metadata, ensure_ascii=False, indent=2),
        "```",
    ]
    return "\n".join(parts)
