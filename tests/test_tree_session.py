from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from my_agent2.tree_session import (
    BranchSummaryEntry,
    CompactionEntry,
    ContextLayer,
    FakeSummarizer,
    TreeSessionManager,
)


class TreeSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session_dir = Path(self.tmp.name)
        self.manager = TreeSessionManager(
            session_dir=self.session_dir,
            summarizer=FakeSummarizer(),
            compact_keep_messages=2,
        )
        self.session_id = self.manager.listSessions()[0]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def lines(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.manager.getSessionFilePath(self.session_id).read_text().splitlines()
            if line.strip()
        ]

    def append_user(self, text: str) -> str:
        return self.manager.append_message(self.session_id, {"role": "user", "content": text})

    def append_assistant(self, text: str) -> str:
        return self.manager.append_message(self.session_id, {"role": "assistant", "content": text})

    def test_append_jsonl_only_appends_and_header_first(self) -> None:
        before = self.manager.getSessionFilePath(self.session_id).read_text()
        entry_id = self.append_user("hello")
        after = self.manager.getSessionFilePath(self.session_id).read_text()

        self.assertTrue(after.startswith(before))
        rows = self.lines()
        self.assertEqual(rows[0]["type"], "session_info")
        self.assertTrue(any(row["id"] == entry_id for row in rows))
        self.assertTrue(any(row["type"] == "session_state" for row in rows))

    def test_reload_restores_tree_active_leaf_labels_summary_and_compaction(self) -> None:
        root = self.append_user("root")
        base = self.append_assistant("base")
        old = self.append_user("old path")
        old_leaf = self.append_assistant("old answer")
        summary = self.manager.createBranchSummary(self.session_id, old_leaf, base)
        self.manager.addLabel(self.session_id, root, "checkpoint")
        self.append_user("new path")
        compaction = self.manager.compactActiveBranch(
            self.session_id,
            maxContextTokens=1,
            keepRecentTokens=1,
            summarizer=FakeSummarizer(),
        )

        reloaded = TreeSessionManager(session_dir=self.session_dir, summarizer=FakeSummarizer())
        reloaded.loadSession(self.session_id)
        session = reloaded.sessions[self.session_id]

        self.assertEqual(session.activeLeafId, compaction)
        self.assertEqual(reloaded.get_label(self.session_id, root), "checkpoint")
        self.assertIsInstance(session.entriesById[summary], BranchSummaryEntry)
        self.assertIsInstance(session.entriesById[compaction], CompactionEntry)
        self.assertTrue(reloaded.buildModelContext(self.session_id))

    def test_context_uses_parent_path_not_jsonl_tail(self) -> None:
        self.append_user("root")
        base = self.append_assistant("base")
        self.append_user("sibling A")
        self.append_assistant("A answer")
        self.manager.forkFromEntry(self.session_id, base)
        self.append_user("sibling B")
        self.append_assistant("B answer")

        context_text = "\n".join(str(message["content"]) for message in self.manager.buildModelContext(self.session_id))
        self.assertIn("sibling B", context_text)
        self.assertNotIn("sibling A", context_text)

        rows = self.lines()
        self.assertIn("A answer", "\n".join(json.dumps(row) for row in rows))
        self.assertIn("B answer", "\n".join(json.dumps(row) for row in rows))

    def test_fork_and_clone(self) -> None:
        self.append_user("root")
        base = self.append_assistant("base")
        a = self.append_user("branch A")
        self.append_assistant("A done")
        self.manager.forkFromEntry(self.session_id, base)
        b = self.append_user("branch B")

        children = self.manager.sessions[self.session_id].childrenByParent[base]
        self.assertIn(a, children)
        self.assertIn(b, children)

        clone_id = self.manager.cloneActiveBranch(self.session_id)
        clone_context = self.manager.buildModelContext(clone_id)
        original_context = self.manager.buildModelContext(self.session_id)
        self.assertEqual([m["content"] for m in clone_context], [m["content"] for m in original_context])

        clone_rows = self.manager.storage.read_lines(clone_id)
        self.assertNotIn("branch A", "\n".join(json.dumps(row) for row in clone_rows))
        reloaded = TreeSessionManager(session_dir=self.session_dir, summarizer=FakeSummarizer())
        reloaded.loadSession(clone_id)
        self.assertEqual(
            [m["content"] for m in reloaded.buildModelContext(clone_id)],
            [m["content"] for m in original_context],
        )

    def test_branch_summary_reloads_and_traces_summarized_ids(self) -> None:
        self.append_user("root")
        base = self.append_assistant("base")
        old = self.append_user("old branch decision")
        old_leaf = self.append_assistant("old branch result")
        summary_id = self.manager.createBranchSummary(self.session_id, old_leaf, base)
        self.append_user("new branch question")

        debug = self.manager.debugBuildModelContext(self.session_id)
        context_text = "\n".join(str(message["content"]) for message in self.manager.buildModelContext(self.session_id))
        self.assertIn("BRANCH_SUMMARY SUMMARY", context_text)
        self.assertIn(summary_id, debug["includedEntryIds"])
        self.assertNotIn(old, debug["includedEntryIds"])
        self.assertNotIn(old_leaf, debug["includedEntryIds"])
        self.assertEqual(debug["excludedReason"][old], "sibling_branch")
        self.assertEqual(debug["excludedReason"][old_leaf], "sibling_branch")
        summary = self.manager.sessions[self.session_id].entriesById[summary_id]
        self.assertEqual(summary.summarizedEntryIds, [old, old_leaf])

        reloaded = TreeSessionManager(session_dir=self.session_dir, summarizer=FakeSummarizer())
        reloaded.loadSession(self.session_id)
        self.assertIn(
            "BRANCH_SUMMARY SUMMARY",
            "\n".join(str(message["content"]) for message in reloaded.buildModelContext(self.session_id)),
        )

    def test_token_aware_compaction_rules_survive_reload(self) -> None:
        old1 = self.append_user("old 1")
        old2 = self.append_assistant("old 2")
        self.append_user("recent 1")
        self.append_assistant("recent 2")
        before_lines = len(self.lines())
        compaction_id = self.manager.compactActiveBranch(
            self.session_id,
            maxContextTokens=1,
            keepRecentTokens=1,
            summarizer=FakeSummarizer(),
        )
        after_lines = len(self.lines())

        self.assertGreater(after_lines, before_lines)
        compaction = self.manager.sessions[self.session_id].entriesById[compaction_id]
        self.assertIn(old1, compaction.compactedEntryIds)
        self.assertIn(old2, compaction.compactedEntryIds)

        context = self.manager.buildModelContext(self.session_id)
        direct_contents = [message["content"] for message in context[1:]]
        self.assertTrue(str(context[0]["content"]).startswith("Compaction summary"))
        self.assertNotIn("old 1", direct_contents)

        reloaded = TreeSessionManager(session_dir=self.session_dir, summarizer=FakeSummarizer())
        reloaded.loadSession(self.session_id)
        reloaded_text = "\n".join(str(message["content"]) for message in reloaded.buildModelContext(self.session_id))
        self.assertIn("Compaction summary", reloaded_text)
        self.assertNotIn("\nold 1\n", reloaded_text)

    def test_context_ladder_and_raw_entries(self) -> None:
        msg = self.append_user("normal")
        raw = self.manager.appendEntry(
            self.session_id,
            self.manager.sessions[self.session_id].activeLeafId,
            {
                "type": "raw",
                "rawRef": "/tmp/raw.log",
                "metadata": {"contextLayer": ContextLayer.L4_RAW_FILE_OR_LOG.name},
            },
        )
        summary = self.manager.createBranchSummary(self.session_id, msg, msg)
        compaction = self.manager.compactActiveBranch(
            self.session_id,
            maxContextTokens=1,
            keepRecentTokens=1,
            summarizer=FakeSummarizer(),
        )

        self.assertEqual(self.manager.getContextLayer(self.session_id, raw), ContextLayer.L4_RAW_FILE_OR_LOG)
        self.assertEqual(self.manager.getContextLayer(self.session_id, summary), ContextLayer.L1_SUMMARY)
        self.assertEqual(self.manager.getContextLayer(self.session_id, compaction), ContextLayer.L1_SUMMARY)

        reloaded = TreeSessionManager(session_dir=self.session_dir, summarizer=FakeSummarizer())
        reloaded.loadSession(self.session_id)
        self.assertEqual(reloaded.getContextLayer(self.session_id, raw), ContextLayer.L4_RAW_FILE_OR_LOG)
        ladder_text = "\n".join(str(message["content"]) for message in reloaded.buildContextByLadder(self.session_id, ContextLayer.L2_SELECTED_MESSAGES))
        self.assertNotIn("/tmp/raw.log", ladder_text)

    def test_set_context_layer_persists_and_l3_excluded_from_l2_context(self) -> None:
        msg = self.append_user("normal")
        self.manager.setContextLayer(self.session_id, msg, ContextLayer.L3_TOOL_EVIDENCE)
        self.assertEqual(self.manager.getContextLayer(self.session_id, msg), ContextLayer.L3_TOOL_EVIDENCE)

        reloaded = TreeSessionManager(session_dir=self.session_dir, summarizer=FakeSummarizer())
        reloaded.loadSession(self.session_id)
        self.assertEqual(reloaded.getContextLayer(self.session_id, msg), ContextLayer.L3_TOOL_EVIDENCE)
        self.assertEqual(reloaded.buildContextByLadder(self.session_id, ContextLayer.L2_SELECTED_MESSAGES), [])

    def test_debug_context(self) -> None:
        self.append_user("root")
        base = self.append_assistant("base")
        sibling = self.append_user("sibling")
        self.manager.forkFromEntry(self.session_id, base)
        label = self.manager.addLabel(self.session_id, base, "base")
        self.append_user("active")
        compaction = self.manager.compactActiveBranch(
            self.session_id,
            maxContextTokens=1,
            keepRecentTokens=1,
            summarizer=FakeSummarizer(),
        )

        debug = self.manager.debugBuildModelContext(self.session_id)
        self.assertIn("includedEntryIds", debug)
        self.assertIn(sibling, debug["siblingBranchEntryIds"])
        self.assertEqual(debug["excludedReason"][label], "label_not_in_context")
        self.assertTrue(debug["compactionApplied"])
        self.assertIn(compaction, debug["includedEntryIds"])

    def test_tree_filter_does_not_mutate_data(self) -> None:
        self.append_user("root")
        self.append_assistant("base")
        before = self.manager.getSessionFilePath(self.session_id).read_text()
        self.assertIn("root", self.manager.render_tree(self.session_id, filter_mode="user-only"))
        self.assertEqual(before, self.manager.getSessionFilePath(self.session_id).read_text())


if __name__ == "__main__":
    unittest.main()
