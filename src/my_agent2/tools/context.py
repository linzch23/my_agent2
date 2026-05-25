from __future__ import annotations

from typing import Any

from .base import Tool, object_schema


class SearchContextTool(Tool):
    name = "search_context"
    description = "Search structured context and memory by keyword (searches L0/L1/L2)."
    read_only = True

    def __init__(self, memory_store: Any) -> None:
        self.memory_store = memory_store

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema({
            "query": {"type": "string", "minLength": 1},
            "limit": {"type": "integer"},
        }, required=["query"])

    def execute(self, query: str, limit: int = 5) -> str:
        results = self.memory_store.search_memory(query, limit=limit)
        if not results:
            return "(No results.)"
        lines = []
        for r in results:
            overview = r.get('overview', '') or r.get('abstract', '')
            lines.append(
                f"- {r['uri']} | trust={r.get('trust_score', '?')} | type={r.get('context_type', '?')}\n"
                f"  {r.get('title', '?')}: {overview}"
            )
        return "\n".join(lines)


class ReadContextTool(Tool):
    name = "read_context"
    description = "Read a context object by URI. Use layer='auto' for overview, 'full' for complete body."
    read_only = True

    def __init__(self, memory_store: Any) -> None:
        self.memory_store = memory_store

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema({
            "uri": {"type": "string", "minLength": 1},
            "layer": {"type": "string", "enum": ["auto", "full"]},
        }, required=["uri"])

    def execute(self, uri: str, layer: str = "auto") -> str:
        return self.memory_store.read_context(uri, layer=layer)


class ListContextTool(Tool):
    name = "list_context"
    description = "List context objects under a URI prefix."
    read_only = True

    def __init__(self, memory_store: Any) -> None:
        self.memory_store = memory_store

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema({
            "prefix": {"type": "string"},
            "limit": {"type": "integer"},
        }, required=[])

    def execute(self, prefix: str = "mem://", limit: int = 50) -> str:
        results = self.memory_store.list_context(prefix=prefix, limit=limit)
        if not results:
            return "(No context objects found.)"
        lines = [f"{len(results)} objects under {prefix}:"]
        for r in results:
            lines.append(f"- {r['uri']} [{r.get('context_type', '?')}] {r.get('title', '?')}")
        return "\n".join(lines)


class ShowContextLinksTool(Tool):
    name = "show_context_links"
    description = "Show memory graph links for a context object URI."
    read_only = True

    def __init__(self, memory_store: Any) -> None:
        self.memory_store = memory_store

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema({
            "uri": {"type": "string", "minLength": 1},
            "limit": {"type": "integer"},
        }, required=["uri"])

    def execute(self, uri: str, limit: int = 5) -> str:
        neighbors = self.memory_store.graph_neighbors(uri, limit=limit)
        if not neighbors:
            return f"(No links for {uri}.)"
        lines = [f"Links for {uri}:"]
        for n in neighbors:
            if n["source_uri"] == uri:
                lines.append(
                    f"- {n['relation']} -> {n['target_uri']} "
                    f"(confidence={n.get('confidence', '?')})"
                )
            else:
                lines.append(
                    f"- {n['source_uri']} -> {n['relation']} "
                    f"(confidence={n.get('confidence', '?')})"
                )
        return "\n".join(lines)
