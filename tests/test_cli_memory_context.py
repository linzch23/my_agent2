from __future__ import annotations

import unittest
from pathlib import Path
from helpers import make_temp_dir

from my_agent2.memory import MemoryStore


class CLIOutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.store = MemoryStore(self.tmp / "memory")

    def test_render_memory_shows_categories(self):
        self.store.remember_note("User prefers tabs", category="preferences", title="Tab Style")
        self.store.remember_note("Team standup at 10am", category="events", title="Standup Time")
        output = self.store.render_memory()
        self.assertIn("Preferences", output)
        self.assertIn("Tab Style", output)
        self.assertIn("Events", output)
        self.assertIn("Standup Time", output)

    def test_render_memory_empty_does_not_crash(self):
        output = self.store.render_memory()
        self.assertIn("Memory OS", output)

    def test_list_context_finds_objects(self):
        self.store.remember_note("Test event", category="events")
        results = self.store.list_context(prefix="mem://user/events/", limit=10)
        self.assertGreaterEqual(len(results), 1)
