from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from my_agent2.loop import AgentApp
from my_agent2.model_client import AgentMessage, TextBlock, ToolUseBlock, _to_openai_messages
from my_agent2.tree_session import MessageEntry


class FakeClient:
    def __init__(self, responses: list[AgentMessage]) -> None:
        self.responses = responses
        self.requests: list[dict] = []

    def create_message(self, **kwargs) -> AgentMessage:
        self.requests.append(copy.deepcopy(kwargs))
        if not self.responses:
            return AgentMessage(content=[TextBlock("fallback")], stop_reason="stop")
        return self.responses.pop(0)

    def stream_message(self, **kwargs) -> AgentMessage:
        on_text_delta = kwargs.pop("on_text_delta", None)
        message = self.create_message(**kwargs)
        if on_text_delta:
            for block in message.content:
                if isinstance(block, TextBlock):
                    on_text_delta(block.text)
        return message


class AgentTreeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "templates").mkdir()
        (self.root / "templates" / "system.md").write_text(
            "workspace={{ workspace }}\n{{ memory }}\n{{ user_profile }}\n{{ skills_summary }}",
            encoding="utf-8",
        )
        (self.root / "sample.txt").write_text("secret\n", encoding="utf-8")
        self.patchers = [
            patch.dict(
                os.environ,
                {
                    "MY_AGENT_WORKSPACE": str(self.root),
                    "MY_AGENT_PROVIDER": "deepseek",
                    "MY_AGENT_MODEL": "fake-model",
                    "MY_AGENT_SESSION_ID": "",
                },
                clear=False,
            )
        ]

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp.cleanup()

    def make_app(self, responses: list[AgentMessage]) -> tuple[AgentApp, FakeClient]:
        client = FakeClient(responses)
        build_patch = patch("my_agent2.loop.build_model_client", return_value=client)
        self.patchers.append(build_patch)
        for patcher in self.patchers:
            patcher.start()
        app = AgentApp(root=self.root)
        return app, client

    def session_rows(self, app: AgentApp) -> list[dict]:
        path = app.tree.getSessionFilePath(app.session_id)
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_ask_writes_user_and_assistant_entries_to_jsonl(self) -> None:
        app, client = self.make_app([AgentMessage([TextBlock("assistant reply")], "stop")])

        reply = app.ask("hello")

        self.assertEqual(reply, "assistant reply")
        self.assertEqual(client.requests[0]["messages"], [{"role": "user", "content": "hello"}])
        messages = [row for row in self.session_rows(app) if row["type"] == "message"]
        self.assertEqual([row["message"]["role"] for row in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["message"]["content"], "hello")
        self.assertEqual(messages[1]["message"]["content"][0]["text"], "assistant reply")

    def test_jump_then_ask_creates_sibling_branch_and_context_uses_active_path(self) -> None:
        app, client = self.make_app(
            [
                AgentMessage([TextBlock("old answer")], "stop"),
                AgentMessage([TextBlock("new answer")], "stop"),
            ]
        )

        app.ask("root question")
        session = app.tree.sessions[app.session_id]
        root_id = next(
            entry.id
            for entry in session.entries
            if isinstance(entry, MessageEntry) and entry.message["content"] == "root question"
        )
        old_assistant_id = session.activeLeafId

        app.jump_to_entry(root_id)
        app.ask("new branch question")

        session = app.tree.sessions[app.session_id]
        branch_user_id = next(
            entry.id
            for entry in session.entries
            if isinstance(entry, MessageEntry) and entry.message["content"] == "new branch question"
        )
        self.assertIn(old_assistant_id, session.childrenByParent[root_id])
        self.assertIn(branch_user_id, session.childrenByParent[root_id])

        second_prompt = json.dumps(client.requests[1]["messages"], ensure_ascii=False)
        self.assertIn("new branch question", second_prompt)
        self.assertNotIn("old answer", second_prompt)
        debug = app.tree.debugBuildModelContext(app.session_id)
        self.assertIn(old_assistant_id, debug["siblingBranchEntryIds"])

    def test_tool_call_and_result_are_persisted_on_active_path(self) -> None:
        app, client = self.make_app(
            [
                AgentMessage(
                    [
                        TextBlock("checking"),
                        ToolUseBlock(
                            id="call_1",
                            name="read_file",
                            input={"path": "sample.txt", "limit": 1},
                        ),
                    ],
                    "tool_use",
                ),
                AgentMessage([TextBlock("done")], "stop"),
            ]
        )

        reply = app.ask("read sample")

        self.assertEqual(reply, "done")
        rows = self.session_rows(app)
        self.assertIn("tool_call", [row["type"] for row in rows])
        self.assertIn("tool_result", [row["type"] for row in rows])

        second_prompt = json.dumps(client.requests[1]["messages"], ensure_ascii=False)
        self.assertIn('"type": "tool_use"', second_prompt)
        self.assertIn("secret", second_prompt)
        active_context = json.dumps(app.tree.buildModelContext(app.session_id), ensure_ascii=False)
        self.assertIn("tool_result", active_context)

    def test_persisted_reasoning_and_tool_use_blocks_convert_back_for_deepseek(self) -> None:
        converted = _to_openai_messages(
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "reasoning", "text": "think"},
                        {"type": "text", "text": "answer"},
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "read_file",
                            "input": {"path": "sample.txt"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}
                    ],
                },
            ]
        )

        self.assertEqual(converted[0]["content"], "answer")
        self.assertEqual(converted[0]["reasoning_content"], "think")
        self.assertEqual(converted[0]["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(converted[1], {"role": "tool", "tool_call_id": "call_1", "content": "ok"})


if __name__ == "__main__":
    unittest.main()
