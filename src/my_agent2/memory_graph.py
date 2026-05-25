from __future__ import annotations

import json
from pathlib import Path
from typing import Any


VALID_RELATIONS = {"supports", "contradicts", "updates", "related", "derived_from", "uses_tool"}
SYMMETRIC_RELATIONS = {"related", "supports", "contradicts"}
INVERSE_RELATIONS = {
    "updates": "updated_by",
    "updated_by": "updates",
    "derived_from": "parent_of",
    "parent_of": "derived_from",
    "uses_tool": "used_by",
    "used_by": "uses_tool",
}
ALL_RELATIONS = VALID_RELATIONS | {"updated_by", "parent_of", "used_by"}


class MemoryGraph:
    def __init__(self, links_path: Path) -> None:
        self.links_path = Path(links_path)
        if not self.links_path.exists():
            self.links_path.write_text("", encoding="utf-8")
        self._links_cache: list[dict[str, Any]] = self._load_links()

    def _load_links(self) -> list[dict[str, Any]]:
        if not self.links_path.exists():
            return []
        text = self.links_path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def add_link(self, source: str, target: str, relation: str, confidence: float, reason: str) -> None:
        if relation not in ALL_RELATIONS:
            raise ValueError(f"Invalid relation: {relation}")
        self._add_directed_link(source, target, relation, confidence, reason)
        # 双向连接：对称关系用相同 relation，方向性关系用反向 relation
        if relation in SYMMETRIC_RELATIONS:
            self._add_directed_link(target, source, relation, confidence, reason)
        elif relation in INVERSE_RELATIONS:
            self._add_directed_link(target, source, INVERSE_RELATIONS[relation], confidence, reason)

    def _add_directed_link(
        self, source: str, target: str, relation: str, confidence: float, reason: str
    ) -> None:
        for link in self._links_cache:
            if link["source_uri"] == source and link["target_uri"] == target and link["relation"] == relation:
                if confidence > link["confidence"]:
                    link["confidence"] = confidence
                    link["reason"] = reason
                self._write_links()
                return
        self._links_cache.append({
            "source_uri": source,
            "target_uri": target,
            "relation": relation,
            "confidence": confidence,
            "reason": reason,
        })
        self._write_links()

    def neighbors(self, uri: str, limit: int = 5) -> list[dict[str, Any]]:
        result = [
            link for link in self._links_cache
            if link["source_uri"] == uri or link["target_uri"] == uri
        ]
        result.sort(key=lambda x: -x["confidence"])
        return result[:limit]

    def expand(self, uris: list[str], fanout: int = 3) -> list[dict[str, Any]]:
        all_links: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for uri in uris:
            for link in self.neighbors(uri, limit=fanout):
                key = (link["source_uri"], link["target_uri"], link["relation"])
                if key not in seen:
                    seen.add(key)
                    all_links.append(link)
        all_links.sort(key=lambda x: -x["confidence"])
        return all_links

    def auto_link(
        self,
        uri: str,
        contextfs: Any,
        client: Any = None,
        model: str = "",
        *,
        fanout: int = 5,
        min_confidence: float = 0.3,
    ) -> list[dict[str, Any]]:
        """Auto-link a memory to existing related memories.

        Uses LLM if client is provided; falls back to keyword matching.
        """
        try:
            obj = contextfs.read_object(uri, layer="auto")
        except KeyError:
            return []

        candidates = contextfs.search_objects(
            obj.get("title", ""), limit=fanout, include_sensitive=False
        )
        # Remove self
        candidates = [c for c in candidates if c.get("uri") != uri]

        if client is not None and model:
            return self._llm_auto_link(uri, obj, candidates, client, model, min_confidence)
        return self._keyword_auto_link(uri, obj, candidates)

    def _keyword_auto_link(
        self, uri: str, obj: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        my_tags = set(obj.get("tags") or [])
        my_title = (obj.get("title") or "").lower()
        for cand in candidates:
            cand_uri = cand.get("uri", "")
            cand_tags = set((cand.get("tags") or []))
            cand_title = (cand.get("title") or "").lower()
            tag_overlap = my_tags & cand_tags
            title_bigram_overlap = _bigram_overlap(my_title, cand_title)
            if tag_overlap or title_bigram_overlap >= 2:
                confidence = 0.3 + 0.1 * len(tag_overlap) + 0.05 * title_bigram_overlap
                self.add_link(uri, cand_uri, "related", min(confidence, 0.7),
                              f"keyword overlap: tags={tag_overlap}, bigrams={title_bigram_overlap}")
                created.append({
                    "source_uri": uri, "target_uri": cand_uri,
                    "relation": "related", "confidence": min(confidence, 0.7),
                })
        return created

    def _llm_auto_link(
        self, uri: str, obj: dict[str, Any], candidates: list[dict[str, Any]],
        client: Any, model: str, min_confidence: float,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        prompt = _auto_link_prompt(obj, candidates)
        try:
            response = client.create_message(
                model=model, max_tokens=600,
                system="分析记忆之间的关系，判断支持/矛盾/更新/相关。只输出合法 JSON。",
                messages=[{"role": "user", "content": prompt}], tools=[],
            )
            text = "\n".join(
                getattr(block, "text", "")
                for block in (response.content if hasattr(response, "content") else [])
            )
            operations = _parse_auto_link_json(text)
        except Exception:
            return self._keyword_auto_link(uri, obj, candidates)

        created: list[dict[str, Any]] = []
        for op in operations:
            rel = op.get("relation", "related")
            conf = float(op.get("confidence", 0.5))
            if rel in ALL_RELATIONS and conf >= min_confidence:
                self.add_link(uri, op["target_uri"], rel, conf, op.get("reason", ""))
                created.append({
                    "source_uri": uri, "target_uri": op["target_uri"],
                    "relation": rel, "confidence": conf,
                })
        return created

    def _read_links(self) -> list[dict[str, Any]]:
        return self._links_cache

    def _write_links(self) -> None:
        self.links_path.write_text(
            "\n".join(json.dumps(link, ensure_ascii=False) for link in self._links_cache) + "\n",
            encoding="utf-8",
        )


def _bigram_overlap(title_a: str, title_b: str) -> int:
    """Count overlapping character bigrams between two titles (CJK-aware)."""
    def bigrams(s: str) -> set[str]:
        bg = set()
        s = s.strip()
        for i in range(len(s) - 1):
            bg.add(s[i:i + 2])
        return bg
    ba = bigrams(title_a)
    bb = bigrams(title_b)
    return len(ba & bb)


def _auto_link_prompt(obj: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    cand_text = "\n".join(
        f"- URI: {c.get('uri')}\n  Title: {c.get('title')}\n  Abstract: {c.get('abstract')}"
        for c in candidates
    )
    return f"""分析这条新记忆与已有记忆的关系，输出链接。

新记忆:
- URI: {obj.get('uri')}
- Title: {obj.get('title')}
- Abstract: {obj.get('abstract')}
- Tags: {obj.get('tags')}

已有记忆:
{cand_text}

输出 JSON 数组（无关系则空数组），每条:
[{{"target_uri": "...", "relation": "supports|contradicts|updates|related", "confidence": 0.0-1.0, "reason": "关系依据"}}]

只输出 JSON 数组，不要其他文字。"""


def _parse_auto_link_json(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if "[" in text and "]" in text:
        start = text.index("[")
        end = text.rindex("]") + 1
        text = text[start:end]
    return json.loads(text)
