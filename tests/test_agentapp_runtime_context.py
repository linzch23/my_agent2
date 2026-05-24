from __future__ import annotations

import unittest
from pathlib import Path
from helpers import make_temp_dir, make_fake_model_client

from my_agent2.tree_session import TreeSessionManager, FakeSummarizer
from my_agent2.context import ContextBuilder, RuntimeContextBuilder
from my_agent2.context_backend import LocalContextBackend


class FakeMemoryStore:
    def __init__(self):
        self.search_calls = []
        self._fake_results = []
    def set_results(self, results):
        self._fake_results = results
    def search_memory(self, query, limit=6):
        self.search_calls.append(query)
        return self._fake_results
    def read_memory(self): return ""
    def read_user(self): return ""
    def load_unarchived_history(self): return []
    def append_history(self, *a): pass
    def read_context(self, uri, layer="auto"): return ""
    def graph_neighbors(self, uri, limit=5): return []
    def set_auto_link_client(self, *a): pass


class FakeSkills:
    def active_context(self): return ""
    def always_skills(self): return []
    def summary(self, exclude=None): return "(None)"


class RuntimeContextInjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        (self.tmp / "templates").mkdir()
        (self.tmp / "templates" / "system.md").write_text(
            "Workspace: {{ workspace }}\n\n"
            "{{ runtime_context or \"(None)\" }}\n\n"
            "Memory: {{ memory }}\n",
            encoding="utf-8",
        )
        self.fake_memory = FakeMemoryStore()
        self.ctx_builder = ContextBuilder(
            self.tmp / "templates", FakeSkills(), self.fake_memory,
        )

    def test_system_prompt_includes_runtime_context_when_hits(self):
        self.fake_memory.set_results([{
            "uri": "mem://user/prefs/theme",
            "title": "Theme Preference",
            "abstract": "User prefers dark mode.",
            "overview": "User prefers dark mode for all apps.",
            "trust_score": 0.9,
            "updated_at": "2026-05-24T10:00:00Z",
        }])
        backend = LocalContextBackend(self.fake_memory)
        rtc = RuntimeContextBuilder(backend, limit=6, max_chars=3000)
        runtime_context = rtc.build("theme")
        prompt = self.ctx_builder.build(workspace=self.tmp, runtime_context=runtime_context)

        self.assertIn("## Runtime Context", prompt)
        self.assertIn("mem://user/prefs/theme", prompt)
        self.assertIn("dark mode", prompt)

    def test_system_prompt_no_runtime_context_when_miss(self):
        self.fake_memory.set_results([])
        backend = LocalContextBackend(self.fake_memory)
        rtc = RuntimeContextBuilder(backend, limit=6, max_chars=3000)
        runtime_context = rtc.build("nothing")
        prompt = self.ctx_builder.build(workspace=self.tmp, runtime_context=runtime_context)

        self.assertIn("(No runtime context recalled.)", prompt)
