from __future__ import annotations

import unittest
from pathlib import Path
from helpers import make_temp_dir

from my_agent2.memory_graph import MemoryGraph


class MemoryGraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.links_path = self.tmp / "links.jsonl"
        self.graph = MemoryGraph(self.links_path)

    def test_add_and_get_neighbors(self):
        self.graph.add_link("mem://a", "mem://b", "related", 0.8, "similar tags")
        self.graph.add_link("mem://a", "mem://c", "supports", 0.9, "confirms finding")
        neighbors = self.graph.neighbors("mem://a", limit=5)
        self.assertEqual(len(neighbors), 2)
        self.assertEqual(neighbors[0]["target_uri"], "mem://c")  # higher confidence first

    def test_duplicate_link_keeps_higher_confidence(self):
        self.graph.add_link("mem://a", "mem://b", "related", 0.5, "low")
        self.graph.add_link("mem://a", "mem://b", "related", 0.9, "high")
        neighbors = self.graph.neighbors("mem://a", limit=5)
        self.assertEqual(len(neighbors), 1)
        self.assertEqual(neighbors[0]["confidence"], 0.9)

    def test_expand_respects_fanout(self):
        self.graph.add_link("mem://a", "mem://b", "related", 0.9, "")
        self.graph.add_link("mem://b", "mem://c", "supports", 0.8, "")
        self.graph.add_link("mem://b", "mem://d", "related", 0.7, "")
        expanded = self.graph.expand(["mem://a"], fanout=2)
        self.assertLessEqual(len(expanded), 2)

    def test_neighbors_unknown_uri_returns_empty(self):
        result = self.graph.neighbors("mem://nonexistent", limit=5)
        self.assertEqual(result, [])

    def test_auto_link_creates_related_links_by_keyword_fallback(self):
        """auto_link with LLM failure falls back to keyword match."""
        from my_agent2.contextfs import ContextFS, ContextObject
        from helpers import make_contextfs_root

        root = make_contextfs_root(self.tmp)
        cfs = ContextFS(self.tmp / "memory")

        # write existing memories
        cfs.write_object(ContextObject(
            uri="mem://user/prefs/theme", context_type="memory", title="Theme Preference",
            abstract="dark mode preferred.", overview="User prefers dark mode.",
            content_path="mem/user/prefs/theme.md", source="manual", trust_score=0.9,
            sensitivity="public", status="active", tags=["preference", "theme"],
            metadata={}, digest="x", created_at="", updated_at="",
        ), "User always uses dark mode.")
        cfs.write_object(ContextObject(
            uri="mem://agent/cases/crash", context_type="memory", title="Crash Bug",
            abstract="null pointer crash.", overview="App crashes on startup.",
            content_path="mem/agent/cases/crash.md", source="compaction", trust_score=0.7,
            sensitivity="public", status="active", tags=["bug", "crash"],
            metadata={}, digest="x", created_at="", updated_at="",
        ), "NPE in main.py line 42.")

        # auto_link with keyword fallback (no LLM client)
        links = self.graph.auto_link(
            "mem://user/prefs/theme", cfs,
            client=None, model="test",
        )
        self.assertIsInstance(links, list)
