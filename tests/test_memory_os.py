from __future__ import annotations

import unittest
from pathlib import Path
from helpers import make_temp_dir

from my_agent2.memory import MemoryStore


class MemoryOSLegacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.mem_dir = self.tmp / "memory"
        self.store = MemoryStore(self.mem_dir)

    def test_read_write_append_memory_still_works(self):
        self.store.write_memory("# Test\n- item 1")
        content = self.store.read_memory()
        self.assertIn("item 1", content)
        self.store.append_memory("item 2")
        content2 = self.store.read_memory()
        self.assertIn("item 2", content2)

    def test_append_history_and_load_unarchived(self):
        self.store.append_history("user", "hello")
        self.store.append_history("assistant", "hi there")
        unarchived = self.store.load_unarchived_history()
        self.assertEqual(len(unarchived), 2)
        self.assertEqual(unarchived[0]["role"], "user")
        self.assertEqual(unarchived[1]["role"], "assistant")

    def test_read_write_user(self):
        self.store.write_user("Name: Test User")
        self.assertEqual(self.store.read_user(), "Name: Test User")

    def test_append_compaction(self):
        self.store.append_compaction(stamp="2026-01-01T00:00:00Z", summary="Test compaction.", old_count=10)
        compactions = (self.mem_dir / "compactions.md").read_text()
        self.assertIn("Test compaction", compactions)


class MemoryOSNewAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.mem_dir = self.tmp / "memory"
        self.store = MemoryStore(self.mem_dir)

    def test_remember_note_creates_structured_memory_object(self):
        uri = self.store.remember_note("User prefers tabs over spaces", category="preferences", title="Tab Preference")
        self.assertTrue(uri.startswith("mem://"), f"Expected mem:// URI, got {uri}")
        result = self.store.read_context(uri, layer="auto")
        self.assertIn("tabs over spaces", result)

    def test_remember_note_also_writes_legacy_memory(self):
        self.store.remember_note("Important project fact", category="events")
        legacy = (self.mem_dir / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("Important project fact", legacy)

    def test_commit_session_archive_writes_archive_and_memory_objects(self):
        ops = [{
            "action": "upsert", "category": "decisions",
            "key": "use-sqlite", "title": "Use SQLite",
            "abstract": "Decided to use SQLite for storage.",
            "overview": "Team decided to use SQLite for local storage needs.",
            "content": "Full decision record: use SQLite as embedded DB.",
            "reason": "Architecture decision captured from session.",
            "trust_score": 0.8, "tags": ["architecture"],
            "links": [],
        }]
        archive_uri = self.store.commit_session_archive(
            session_uri="ctx://sessions/archives/2026/05/24/s1-c1",
            summary="Compaction summary text.",
            operations=ops,
            metadata={"session_id": "s1", "compaction_id": "c1"},
        )
        self.assertIn("ctx://sessions/archives", archive_uri)

        mem_results = self.store.search_memory("SQLite", limit=5)
        self.assertEqual(len(mem_results), 1)
        self.assertEqual(mem_results[0]["title"], "Use SQLite")

    def test_invalid_operation_goes_to_quarantine(self):
        ops = [{"action": "invalid_action", "category": "events", "key": "bad"}]
        self.store.commit_session_archive(
            session_uri="ctx://sessions/archives/2026/05/24/s2-c1",
            summary="Test.",
            operations=ops,
            metadata={},
        )
        results = self.store.list_context(prefix="mem://quarantine/", limit=10)
        self.assertGreaterEqual(len(results), 1)

    def test_no_current_messages_jsonl_created(self):
        current = self.mem_dir / "context" / "sessions" / "current"
        self.assertFalse(current.exists(), "sessions/current/messages.jsonl must not exist")
