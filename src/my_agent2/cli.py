from __future__ import annotations

from .loop import AgentApp


HELP = """可用命令：
  /help     显示这份帮助
  /tools    列出已注册工具
  /todos    查看当前任务列表
  /memory   查看 Memory OS（结构化长期记忆）
  /context  查看最近 ContextObject
  /mcp      查看 MCP server 和工具状态
  /compact  立即压缩当前会话上下文
  /team     查看持久队友
  /inbox    读取 lead inbox
  /tree [--filter default|no-tools|user-only|labeled-only|all]  查看会话树
  /jump ID  跳转到已有会话树节点
  /fork ID  从已有节点分叉；下一条输入会创建兄弟分支
  /clone    克隆当前 active branch 到新 session 并切换过去
  /label ID LABEL  给会话树节点打标签
  /exit     退出
"""


def main() -> None:
    app = AgentApp()
    try:
        print(f"my_agent2 已就绪。provider={app.provider} model={app.model} workspace={app.workspace}")
        print("输入 /help 查看命令。\n")

        while True:
            try:
                user_input = input("你> ").strip()
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
                print(app.memory.render_memory() + "\n")
                continue
            if user_input == "/context":
                results = app.memory.list_context(limit=20)
                if not results:
                    print("(No context objects.)\n")
                else:
                    for r in results:
                        print(f"- {r['uri']} [{r.get('context_type', '?')}] {r.get('title', '?')} "
                              f"trust={r.get('trust_score', 0):.1f}")
                    print()
                continue
            if user_input == "/mcp":
                print(app.mcp.report() + "\n")
                continue
            if user_input == "/compact":
                print(("已压缩当前上下文。" if app.compact_now() else "当前没有需要压缩的内容。") + "\n")
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
                print("已更新 active leaf。\n")
                continue
            if user_input.startswith("/fork "):
                app.fork_from_entry(user_input.split(maxsplit=1)[1].strip())
                print("已选择分叉点。下一条输入会创建新分支。\n")
                continue
            if user_input == "/clone":
                new_session_id = app.clone_active_branch()
                print(f"已将当前 active branch 克隆到 session {new_session_id}。\n")
                continue
            if user_input.startswith("/label "):
                _, entry_id, label = user_input.split(maxsplit=2)
                app.label_entry(entry_id, label)
                print("标签已保存。\n")
                continue

            print("助手> ", end="", flush=True)
            app.ask(user_input, on_text_delta=lambda text: print(text, end="", flush=True))
            print("\n")
    finally:
        app.close()
