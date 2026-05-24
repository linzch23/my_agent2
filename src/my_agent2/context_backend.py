from __future__ import annotations

from typing import Any, Protocol


class ContextBackend(Protocol):
    def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]: ...
    def read(self, uri: str, layer: str = "auto") -> str: ...
    def list(self, prefix: str = "mem://", limit: int = 50) -> list[dict[str, Any]]: ...
    def remember(self, note: str, category: str = "events", title: str | None = None) -> str: ...
    def neighbors(self, uri: str, limit: int = 5) -> list[dict[str, Any]]: ...


class LocalContextBackend:
    def __init__(self, memory_store: Any) -> None:
        self._store = memory_store

    def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        return self._store.search_memory(query, limit=limit)

    def read(self, uri: str, layer: str = "auto") -> str:
        return self._store.read_context(uri, layer=layer)

    def list(self, prefix: str = "mem://", limit: int = 50) -> list[dict[str, Any]]:
        return self._store.list_context(prefix=prefix, limit=limit)

    def remember(self, note: str, category: str = "events", title: str | None = None) -> str:
        return self._store.remember_note(note, category=category, title=title)

    def neighbors(self, uri: str, limit: int = 5) -> list[dict[str, Any]]:
        return self._store.graph_neighbors(uri, limit=limit)
