from __future__ import annotations

from .loop import AgentApp


HELP = """Commands:
  /help     show this message
  /tools    list available tools
  /todos    show current todos
  /memory   show long-term memory
  /mcp      show MCP server and tool status
  /compact  compact conversation history now
  /team     show persistent teammates
  /inbox    read lead inbox
  /tree [--filter default|no-tools|user-only|labeled-only|all]
  /jump ID  move active leaf to an existing entry
  /fork ID  move active leaf to an existing entry; next input creates a sibling branch
  /clone    clone active branch into a new session file and switch to it
  /label ID LABEL
  /exit     quit
"""


def main() -> None:
    app = AgentApp()
    try:
        print(f"my_agent2 ready. provider={app.provider} model={app.model} workspace={app.workspace}")
        print("Type /help for commands.\n")

        while True:
            try:
                user_input = input("You> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return

            if not user_input:
                continue
            if user_input in {"/exit", "/quit"}:
                return
            if user_input == "/help":
                print(HELP)
                continue
            if user_input == "/tools":
                print("\n".join(app.registry.names()))
                print()
                continue
            if user_input == "/todos":
                print(app.todos.render() + "\n")
                continue
            if user_input == "/memory":
                print(app.memory.read_memory() + "\n")
                continue
            if user_input == "/mcp":
                print(app.mcp.report() + "\n")
                continue
            if user_input == "/compact":
                print(("Compacted." if app.compact_now() else "Nothing to compact.") + "\n")
                continue
            if user_input == "/team":
                print(app.team.list_all() + "\n")
                continue
            if user_input == "/inbox":
                import json

                print(json.dumps(app.team_bus.read_inbox("lead"), ensure_ascii=False, indent=2) + "\n")
                continue
            if user_input.startswith("/tree"):
                parts = user_input.split()
                filter_mode = "default"
                if len(parts) == 3 and parts[1] == "--filter":
                    filter_mode = parts[2]
                print(app.tree_view(filter_mode) + "\n")
                continue
            if user_input.startswith("/jump "):
                app.jump_to_entry(user_input.split(maxsplit=1)[1].strip())
                print("Active leaf updated.\n")
                continue
            if user_input.startswith("/fork "):
                app.fork_from_entry(user_input.split(maxsplit=1)[1].strip())
                print("Fork point selected. Your next message will create a new branch.\n")
                continue
            if user_input == "/clone":
                new_session_id = app.clone_active_branch()
                print(f"Cloned active branch into session {new_session_id}.\n")
                continue
            if user_input.startswith("/label "):
                _, entry_id, label = user_input.split(maxsplit=2)
                app.label_entry(entry_id, label)
                print("Label saved.\n")
                continue

            print("Agent> ", end="", flush=True)
            app.ask(user_input, on_text_delta=lambda text: print(text, end="", flush=True))
            print("\n")
    finally:
        app.close()
