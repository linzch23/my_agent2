from __future__ import annotations

import unittest
from pathlib import Path
from helpers import make_temp_dir, make_contextfs_root

from my_agent2.contextfs import ContextFS, ContextObject


class ContextFSBasicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.root = make_contextfs_root(self.tmp)
        self.cfs = ContextFS(self.tmp / "memory")

    def test_write_and_read_object_auto_returns_l1(self):
        obj = ContextObject(
            uri="mem://test/item",
            context_type="memory",
            title="Test Item",
            abstract="One line abstract.",
            overview="Multi-line overview content.",
            content_path="mem/test/item.md",
            source="test",
            trust_score=0.8,
            sensitivity="public",
            status="active",
            tags=["test"],
            metadata={},
            digest="abc123",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        self.cfs.write_object(obj, "Full L2 body text.")
        result = self.cfs.read_object("mem://test/item", layer="auto")
        self.assertEqual(result["uri"], "mem://test/item")
        self.assertIn("Multi-line overview", result["content"])

    def test_read_object_full_returns_l2(self):
        obj = ContextObject(
            uri="mem://test/full",
            context_type="memory",
            title="Full Test",
            abstract="Abstract.",
            overview="Overview.",
            content_path="mem/test/full.md",
            source="test",
            trust_score=0.7,
            sensitivity="public",
            status="active",
            tags=[],
            metadata={},
            digest="def456",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        self.cfs.write_object(obj, "Complete L2 content here.")
        result = self.cfs.read_object("mem://test/full", layer="full")
        self.assertIn("Complete L2 content", result["content"])

    def test_search_finds_by_title_tag_and_l2(self):
        self.cfs.write_object(ContextObject(
            uri="mem://alpha/one", context_type="memory", title="Alpha Brava",
            abstract="abstract.", overview="overview.", content_path="mem/alpha/one.md",
            source="test", trust_score=0.8, sensitivity="public", status="active",
            tags=["important"], metadata={}, digest="x", created_at="", updated_at="",
        ), "This L2 content has the keyword 'squirrel' buried in it.")
        self.cfs.write_object(ContextObject(
            uri="mem://beta/two", context_type="memory", title="Beta Charlie",
            abstract="abstract beta.", overview="overview.", content_path="mem/beta/two.md",
            source="test", trust_score=0.6, sensitivity="public", status="active",
            tags=[], metadata={}, digest="x", created_at="", updated_at="",
        ), "Generic content.")

        results = self.cfs.search_objects("squirrel", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["uri"], "mem://alpha/one")

        results2 = self.cfs.search_objects("beta", limit=5)
        self.assertEqual(len(results2), 1)
        self.assertEqual(results2[0]["uri"], "mem://beta/two")

    def test_search_skips_sensitive_and_quarantine(self):
        self.cfs.write_object(ContextObject(
            uri="mem://secret/x", context_type="memory", title="Secret",
            abstract="secret abstract.", overview="overview.", content_path="mem/secret/x.md",
            source="test", trust_score=0.5, sensitivity="sensitive", status="active",
            tags=[], metadata={}, digest="x", created_at="", updated_at="",
        ), "sensitive content")
        self.cfs.write_object(ContextObject(
            uri="mem://bad/y", context_type="memory", title="Bad",
            abstract="bad abstract.", overview="overview.", content_path="mem/bad/y.md",
            source="test", trust_score=0.5, sensitivity="public", status="quarantine",
            tags=[], metadata={}, digest="x", created_at="", updated_at="",
        ), "quarantine content")

        results = self.cfs.search_objects("secret", limit=5)
        self.assertEqual(len(results), 0)

        results2 = self.cfs.search_objects("bad", limit=5)
        self.assertEqual(len(results2), 0)

    def test_list_objects_by_prefix(self):
        self.cfs.write_object(ContextObject(
            uri="mem://user/prefs/theme", context_type="memory", title="Theme",
            abstract="dark mode.", overview="User prefers dark mode.", content_path="mem/user/prefs/theme.md",
            source="test", trust_score=0.9, sensitivity="public", status="active",
            tags=["preference"], metadata={}, digest="x", created_at="", updated_at="",
        ), "User always uses dark mode theme.")
        self.cfs.write_object(ContextObject(
            uri="mem://agent/cases/bug1", context_type="memory", title="Bug 1",
            abstract="null pointer.", overview="NPE in auth.", content_path="mem/agent/cases/bug1.md",
            source="test", trust_score=0.7, sensitivity="public", status="active",
            tags=["case"], metadata={}, digest="x", created_at="", updated_at="",
        ), "Fixed NPE in AuthService.login().")

        prefs = self.cfs.list_objects(prefix="mem://user/", limit=10)
        self.assertEqual(len(prefs), 1)
        self.assertEqual(prefs[0]["uri"], "mem://user/prefs/theme")

        all_objs = self.cfs.list_objects(limit=50)
        self.assertGreaterEqual(len(all_objs), 2)
