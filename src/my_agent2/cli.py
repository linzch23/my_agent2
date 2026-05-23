from __future__ import annotations

from .loop import AgentApp


HELP = """Commands:
  /help     show this message
  /tools    list available tools
  /todos    show current todos
  /memory   show long-term memory
  /compact  compact conversation history now
  /team     show persistent teammates
  /inbox    read lead inbox
  /exit     quit
"""


def main() -> None:
    app = AgentApp()
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

        print("Agent> ", end="", flush=True)
        app.ask(user_input, on_text_delta=lambda text: print(text, end="", flush=True))
        print("\n")
