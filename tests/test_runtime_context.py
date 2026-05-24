from __future__ import annotations

import unittest
from pathlib import Path
from helpers import make_temp_dir, make_contextfs_root, make_memory_object

from my_agent2.context_backend import LocalContextBackend
from my_agent2.contextfs import ContextFS, ContextObject


class FakeMemoryStore:
    def __init__(self, cfs):
        self._cfs = cfs
    def search_memory(self, query, limit=6):
        return self._cfs.search_objects(query, limit=limit)
    def read_context(self, uri, layer="auto"):
        try:
            r = self._cfs.read_object(uri, layer=layer)
            return r.get("content", "")
        except KeyError:
            return f"Error: URI not found: {uri}"
    def list_context(self, prefix="mem://", limit=50):
        return self._cfs.list_objects(prefix=prefix, limit=limit)
    def graph_neighbors(self, uri, limit=5):
        return []


class LocalContextBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        root = make_contextfs_root(self.tmp)
        self.cfs = ContextFS(self.tmp / "memory")
        self.cfs.write_object(ContextObject(
            uri="mem://user/prefs/editor", context_type="memory", title="Editor Preference",
            abstract="Uses VS Code.", overview="User prefers VS Code for all projects.",
            content_path="mem/user/prefs/editor.md",
            source="manual", trust_score=0.9, sensitivity="public", status="active",
            tags=["preference"], metadata={}, digest="x", created_at="", updated_at="",
        ), "User always uses VS Code with the Dark+ theme and vim keybindings.")
        self.backend = LocalContextBackend(FakeMemoryStore(self.cfs))

    def test_search_finds_memory(self):
        results = self.backend.search("VS Code", limit=5)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["uri"], "mem://user/prefs/editor")

    def test_read_auto_returns_l1(self):
        content = self.backend.read("mem://user/prefs/editor", layer="auto")
        self.assertIn("VS Code", content)

    def test_read_full_returns_l2(self):
        content = self.backend.read("mem://user/prefs/editor", layer="full")
        self.assertIn("vim keybindings", content)

    def test_list_by_prefix(self):
        results = self.backend.list("mem://user/prefs/", limit=10)
        self.assertEqual(len(results), 1)


class RuntimeContextBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        root = make_contextfs_root(self.tmp)
        self.cfs = ContextFS(self.tmp / "memory")
        for i in range(10):
            self.cfs.write_object(ContextObject(
                uri=f"mem://test/item{i}", context_type="memory",
                title=f"Item {i}", abstract=f"Abstract {i}",
                overview=f"Overview {i}",
                content_path=f"mem/test/item{i}.md",
                source="test", trust_score=0.5, sensitivity="public",
                status="active", tags=["test"], metadata={},
                digest="x", created_at="", updated_at="",
            ), f"Content {i}")
        from my_agent2.context_backend import LocalContextBackend
        from my_agent2.context import RuntimeContextBuilder
        self.builder = RuntimeContextBuilder(
            LocalContextBackend(FakeMemoryStore(self.cfs)),
            limit=6, max_chars=3000,
        )

    def test_build_returns_markdown(self):
        result = self.builder.build("Item 1")
        self.assertIn("## Runtime Context", result)
        self.assertIn("URI:", result)

    def test_build_empty_result(self):
        result = self.builder.build("nonexistent_keyword_xyz")
        self.assertEqual(result, "(No runtime context recalled.)")
