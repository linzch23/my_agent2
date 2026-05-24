from __future__ import annotations

import json
import unittest
from pathlib import Path
from helpers import make_temp_dir, make_fake_model_client

from my_agent2.session_memory_committer import SessionMemoryCommitter, LlmMemoryExtractor


class FakeTree:
    """Minimal fake TreeSessionManager for committer tests."""
    def __init__(self, compaction_entry, debug_info):
        self._entry = compaction_entry
        self._debug = debug_info

    def getBranch(self, session_id, leaf_id=None):
        return [self._entry]

    def debugBuildModelContext(self, session_id):
        return self._debug


class FakeMemoryStore:
    def __init__(self):
        self.archive_calls = []

    def commit_session_archive(self, session_uri, summary, operations, metadata):
        self.archive_calls.append({
            "session_uri": session_uri, "summary": summary,
            "operations": operations, "metadata": metadata,
        })
        return session_uri


class SessionMemoryCommitterTests(unittest.TestCase):
    def test_commit_compaction_writes_archive(self):
        fake_tree = FakeTree(
            compaction_entry={
                "id": "c1", "type": "compaction",
                "summary": "User asked about auth. Decided to use JWT.",
                "compactedEntryIds": ["e1", "e2", "e3"],
                "firstKeptEntryId": "e4",
                "tokenEstimateBefore": 8000,
                "tokenEstimateAfter": 1200,
            },
            debug_info={"activeLeafId": "e4", "sessionTitle": "Test Session"},
        )
        fake_model = make_fake_model_client([[
            type("Block", (), {"text": json.dumps({"operations": [
                {"action": "upsert", "category": "decisions", "key": "use-jwt",
                 "title": "Use JWT", "abstract": "Decided to use JWT for auth.",
                 "overview": "Use JWT with RS256.", "content": "Full JWT decision.",
                 "reason": "Architecture decision", "trust_score": 0.8, "tags": ["auth"],
                 "links": []},
            ]})})(),
        ]])
        fake_memory = FakeMemoryStore()
        extractor = LlmMemoryExtractor(fake_model, "test-model")
        committer = SessionMemoryCommitter(
            tree=fake_tree, memory_store=fake_memory, extractor=extractor,
        )

        archive_uri = committer.commit_compaction("s1", "c1")

        self.assertIn("ctx://sessions/archives", archive_uri)
        self.assertEqual(len(fake_memory.archive_calls), 1)
        call = fake_memory.archive_calls[0]
        self.assertEqual(len(call["operations"]), 1)
        self.assertEqual(call["operations"][0]["key"], "use-jwt")
        self.assertIn("c1", call["metadata"]["compaction_id"])

    def test_extraction_failure_still_writes_archive(self):
        fake_tree = FakeTree(
            compaction_entry={
                "id": "c2", "type": "compaction",
                "summary": "Some summary.",
                "compactedEntryIds": ["e1"],
                "firstKeptEntryId": "e2",
                "tokenEstimateBefore": 1000,
                "tokenEstimateAfter": 500,
            },
            debug_info={"activeLeafId": "e2"},
        )
        # Model returns invalid JSON
        fake_model = make_fake_model_client([[
            type("Block", (), {"text": "not valid json at all"})(),
        ]])
        fake_memory = FakeMemoryStore()
        extractor = LlmMemoryExtractor(fake_model, "test-model")
        committer = SessionMemoryCommitter(
            tree=fake_tree, memory_store=fake_memory, extractor=extractor,
        )

        archive_uri = committer.commit_compaction("s1", "c2")

        self.assertIn("ctx://sessions/archives", archive_uri)
        # Still wrote archive with empty operations
        self.assertEqual(len(fake_memory.archive_calls), 1)
        self.assertEqual(fake_memory.archive_calls[0]["operations"], [])
