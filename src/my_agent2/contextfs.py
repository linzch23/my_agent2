from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ContextObject:
    uri: str
    context_type: str  # session|memory|resource|skill
    title: str
    abstract: str      # L0
    overview: str      # L1
    content_path: str  # L2 relative path from context root
    source: str
    trust_score: float
    sensitivity: str   # public|internal|sensitive
    status: str        # active|quarantine|archived
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    digest: str = ""
    created_at: str = ""
    updated_at: str = ""
    ttl: str | None = None


def _compute_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _uri_to_path(uri: str) -> str:
    """mem://user/profile -> mem/user/profile"""
    return re.sub(r"^(\w+)://", r"\1/", uri)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ContextFS:
    def __init__(self, memory_dir: Path) -> None:
        self.memory_dir = Path(memory_dir)
        self.root = self.memory_dir / "context"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.jsonl"
        self.diffs_path = self.root / "diffs.jsonl"
        if not self.index_path.exists():
            self.index_path.write_text("", encoding="utf-8")
        if not self.diffs_path.exists():
            self.diffs_path.write_text("", encoding="utf-8")
        self._index_cache: list[dict[str, Any]] = self._load_index()

    def _load_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        lines = self.index_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    # ---- write ----

    def write_object(self, obj: ContextObject, content: str) -> str:
        now = _now_iso()
        if not obj.created_at:
            obj.created_at = now
        obj.updated_at = now
        obj.digest = _compute_digest(content)
        if not obj.content_path:
            obj.content_path = _uri_to_path(obj.uri) + ".md"

        # write L2
        l2_path = self.root / obj.content_path
        l2_path.parent.mkdir(parents=True, exist_ok=True)
        l2_path.write_text(content, encoding="utf-8")

        # upsert index
        self._upsert_index(obj)
        return obj.uri

    def _upsert_index(self, obj: ContextObject) -> None:
        data = asdict(obj)
        found = False
        for i, entry in enumerate(self._index_cache):
            if entry.get("uri") == obj.uri:
                self._index_cache[i] = data
                found = True
                break
        if not found:
            self._index_cache.append(data)
        # write through to disk
        self.index_path.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in self._index_cache) + "\n",
            encoding="utf-8",
        )

    # ---- read ----

    def read_object(self, uri: str, layer: str = "auto") -> dict[str, Any]:
        entry = self._find_index_entry(uri)
        if entry is None:
            raise KeyError(f"URI not found: {uri}")
        if layer == "auto":
            content = entry.get("overview", "") or entry.get("abstract", "")
        elif layer == "full":
            l2_path = self.root / entry.get("content_path", "")
            content = l2_path.read_text(encoding="utf-8") if l2_path.exists() else ""
        else:
            content = entry.get(layer, "")
        return {"uri": uri, "content": content, **entry}

    # ---- list ----

    def list_objects(self, prefix: str = "", limit: int = 50) -> list[dict[str, Any]]:
        results = []
        for line in self._read_index_lines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if prefix and not entry.get("uri", "").startswith(prefix):
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    # ---- search ----

    def search_objects(self, query: str, limit: int = 5, *, include_sensitive: bool = False) -> list[dict[str, Any]]:
        tokens = _tokenize_query(query.lower())
        scored = []
        for line in self._read_index_lines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if not include_sensitive:
                if entry.get("sensitivity") == "sensitive":
                    continue
                if entry.get("status") == "quarantine":
                    continue
                ttl = entry.get("ttl")
                if ttl and _is_expired(ttl):
                    continue

            score = 0.0
            title = (entry.get("title") or "").lower()
            abstract = (entry.get("abstract") or "").lower()
            overview = (entry.get("overview") or "").lower()
            uri = (entry.get("uri") or "").lower()
            tags = " ".join(entry.get("tags") or []).lower()

            for token in tokens:
                if token in title:
                    score += 5
                elif token in tags:
                    score += 4
                elif token in abstract:
                    score += 3
                elif token in overview:
                    score += 2
                elif token in uri:
                    score += 1

            # L2 full-text search
            l2_path = self.root / (entry.get("content_path") or "")
            if l2_path.exists():
                l2_text = l2_path.read_text(encoding="utf-8").lower()
                for token in tokens:
                    if token in l2_text:
                        score += 1

            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: (-x[0], -(x[1].get("trust_score") or 0)))
        return [entry for _, entry in scored[:limit]]

    # ---- diff ----

    def append_diff(self, entry: dict[str, Any]) -> None:
        entry.setdefault("ts", _now_iso())
        with self.diffs_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ---- internals ----

    def _read_index_lines(self) -> list[str]:
        return [json.dumps(e, ensure_ascii=False) for e in self._index_cache]

    def _find_index_entry(self, uri: str) -> dict[str, Any] | None:
        for entry in self._index_cache:
            if entry.get("uri") == uri:
                return entry
        return None


def _is_expired(ttl: str) -> bool:
    try:
        expiry = datetime.fromisoformat(ttl)
        return datetime.now(timezone.utc) > expiry
    except ValueError:
        return False


def _has_cjk(text: str) -> bool:
    """Check if text contains CJK characters."""
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0xF900 <= cp <= 0xFAFF:
            return True
    return False


def _tokenize_query(query: str) -> list[str]:
    """Split query into search tokens with CJK bigram expansion.

    Whitespace-separated tokens pass through unchanged.
    Long CJK tokens (>3 chars) are expanded into character bigrams
    so "我喜欢暗色主题" also matches stored "暗色主题".
    """
    tokens = query.split()
    expanded = list(tokens)
    for token in tokens:
        if len(token) > 3 and _has_cjk(token):
            for i in range(len(token) - 1):
                expanded.append(token[i:i + 2])
    return expanded
