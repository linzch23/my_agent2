from __future__ import annotations

import unittest
from helpers import make_temp_dir

from my_agent2.tree_session import TreeSessionManager, FakeSummarizer


class FakeMemoryStore:
    def __init__(self):
        self.archive_calls = []
    def commit_session_archive(self, session_uri, summary, operations, metadata):
        self.archive_calls.append({"session_uri": session_uri, "operations": operations})
        return session_uri
    # legacy stubs
    def read_memory(self): return ""
    def read_user(self): return ""
    def load_unarchived_history(self): return []
    def append_history(self, *a): pass


class FakeCommitter:
    def __init__(self):
        self.commits = []
    def commit_compaction(self, session_id, compaction_id):
        self.commits.append((session_id, compaction_id))
        return f"ctx://sessions/archives/2026/05/24/{session_id}-{compaction_id}"


class CompactMemoryCommitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        session_dir = self.tmp / "sessions"
        session_dir.mkdir()
        self.tree = TreeSessionManager(
            session_dir=session_dir,
            summarizer=FakeSummarizer(),
            compact_keep_messages=2,
        )
        self.session_id = self.tree.listSessions()[0]
        self.fake_memory = FakeMemoryStore()
        self.fake_committer = FakeCommitter()

    def _fill_messages(self, n: int):
        for i in range(n):
            self.tree.append_message(self.session_id, {"role": "user", "content": f"msg {i}"})
            self.tree.append_message(self.session_id, {"role": "assistant", "content": f"reply {i}"})

    def test_compact_active_branch_returns_compaction_id(self):
        self._fill_messages(8)
        cid = self.tree.compactActiveBranch(self.session_id, maxContextTokens=10000, keepRecentTokens=50,
                                            summarizer=FakeSummarizer())
        self.assertIsNotNone(cid)
        self.assertTrue(len(cid) > 0)

    def test_compact_now_triggers_committer(self):
        self._fill_messages(10)
        cid = self.tree.compactActiveBranch(self.session_id, maxContextTokens=10000, keepRecentTokens=50,
                                            summarizer=FakeSummarizer())
        self.assertIsNotNone(cid)
        self.fake_committer.commit_compaction(self.session_id, cid)
        self.assertEqual(len(self.fake_committer.commits), 1)
        self.assertEqual(self.fake_committer.commits[0], (self.session_id, cid))
