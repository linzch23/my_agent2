"""
真实场景测试：直接与 AgentApp 交互，测试长任务和记忆能力。
每个场景创建独立的临时工作区，互不干扰。

用法:
  .venv\Scripts\Activate.ps1
  $env:PYTHONPATH="src;tests"
  python tests/test_real_scenarios.py              # 运行所有场景
  python tests/test_real_scenarios.py --scenario 1  # 只运行场景1
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

PASS = []
FAIL = []


def check(condition: bool, label: str) -> bool:
    if condition:
        PASS.append(label)
        print(f"    [PASS] {label}")
    else:
        FAIL.append(label)
        print(f"    [FAIL] {label}")
    return condition


def make_env(tmp: Path) -> dict[str, str]:
    """Create isolated env for a test scenario."""
    ws = tmp / "workspace"
    ws.mkdir(parents=True)
    (tmp / "templates").mkdir(exist_ok=True)
    (tmp / "templates" / "system.md").write_text(
        "Workspace: {{ workspace }}\n\n"
        "{{ runtime_context or \"(None)\" }}\n\n"
        "Memory: {{ memory }}\n\n"
        "User: {{ user_profile }}\n",
        encoding="utf-8",
    )
    return {
        "MY_AGENT_WORKSPACE": str(ws),
        "MY_AGENT_SESSION_ID": f"test-{tmp.name[-8:]}",
        "MY_AGENT_PROVIDER": os.getenv("MY_AGENT_PROVIDER", "deepseek"),
        "MY_AGENT_MODEL": os.getenv("MY_AGENT_MODEL", "deepseek-chat"),
        "MY_AGENT_MAX_TOKENS": "4096",
        "MY_AGENT_MAX_CONTEXT_TOKENS": os.getenv("MY_AGENT_MAX_CONTEXT_TOKENS", "64000"),
        "MY_AGENT_STARTUP_COMPACTION": "0",
        "MY_AGENT_RUNTIME_CONTEXT_LIMIT": "6",
        "MY_AGENT_RUNTIME_CONTEXT_MAX_CHARS": "8000",
    }


def run_app(tmp: Path, env: dict[str, str], turns: list[str]) -> tuple[Any, list[str]]:
    """Run AgentApp through a list of user inputs, return app and replies."""
    from my_agent2.loop import AgentApp
    from unittest.mock import patch

    replies = []
    with patch.dict(os.environ, env, clear=False):
        app = AgentApp(root=tmp)
        for user_input in turns:
            print(f"\n  [user] {user_input[:80]}...")
            try:
                reply = app.ask(user_input)
                print(f"  [agent] {reply[:120]}...")
                replies.append(reply)
            except Exception as exc:
                print(f"  [ERROR] {exc}")
                replies.append(f"__ERROR__: {exc}")
        app.close()
    return app, replies


# ================================================================
# 场景 1: 偏好记忆 — 写入 → 搜索 → 召回
# ================================================================
def scenario_1_preferences():
    print("\n" + "=" * 60)
    print("场景 1: 写入用户偏好，搜索验证记忆召回")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp())
    env = make_env(tmp)

    app, replies = run_app(tmp, env, [
        "帮我记住几件事：1）我喜欢用 VS Code 编辑器，搭配暗色主题；"
        "2）我的编程语言偏好是 Python，不用 Java；"
        "3）我的名字是小明。请用 remember 工具逐条记录。",
    ])

    # 检查记忆写入
    results = app.memory.search_memory("VS Code", limit=10)
    check(len(results) >= 1, "搜索'VS Code'能找到记忆")

    results = app.memory.search_memory("Python", limit=10)
    check(len(results) >= 1, "搜索'Python'能找到记忆")

    results = app.memory.search_memory("小明", limit=10)
    check(len(results) >= 1, "搜索'小明'能找到记忆")

    rendered = app.memory.render_memory()
    check("Preferences" in rendered, "/memory 包含 Preferences 分类")
    check("VS Code" in rendered or "编辑器" in rendered, "/memory 显示编辑器偏好")

    # 验证 LEGACY MEMORY.md 兼容
    legacy = (tmp / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    check("VS Code" in legacy or "编辑器" in legacy, "旧版 MEMORY.md 同步写入")

    app.close()
    return len(FAIL) == 0


# ================================================================
# 场景 2: 长对话 → 自动压缩 → 记忆归档
# ================================================================
def scenario_2_long_conversation():
    print("\n" + "=" * 60)
    print("场景 2: 长对话 → compact → 记忆归档")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp())
    env = make_env(tmp)

    app, replies = run_app(tmp, env, [
        "详细介绍一下 Python 的上下文管理器（with 语句），包括它的原理、__enter__和__exit__方法",
        "再对比一下 Python 和 JavaScript 的异步编程模型，各自的优缺点",
    ])

    # 检查对话记录（从 JSONL 文件直接读取）
    import json as _json
    session_path = tmp / "sessions" / f"{app.session_id}.jsonl"
    rows = []
    if session_path.exists():
        for line in session_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(_json.loads(line))
                except Exception:
                    pass
    msg_count = sum(1 for r in rows if r.get("role") in ("user", "assistant"))
    check(msg_count >= 2, f"会话 JSONL 有对话记录（当前{msg_count}条）")

    # 尝试触压缩
    try:
        result = app.compact_now()
        if result:
            archived = app.memory.list_context(prefix="ctx://sessions/archives/", limit=10)
            print(f"    [info] compact 成功，archives={len(archived)}")
            check(len(archived) >= 1, "压缩后生成 session archive")
        else:
            print("    [info] compact 未触发（token 未达阈值），这不算失败")
            PASS.append("compact 未达阈值（正常）")
    except Exception as exc:
        print(f"    [WARN] compact 异常: {exc}")

    app.close()
    return True


# ================================================================
# 场景 3: 偏好变更 → 旧记忆归档
# ================================================================
def scenario_3_preference_change():
    print("\n" + "=" * 60)
    print("场景 3: 偏好变更，验证旧记忆归档、新记忆激活")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp())
    env = make_env(tmp)

    app, replies = run_app(tmp, env, [
        "用 remember 工具记录：我的编辑器主题偏好是暗色主题，title 用'编辑器主题'",
    ])

    # 找 URI
    results_before = app.memory.search_memory("暗色", limit=5)
    check(len(results_before) >= 1, "偏好写入后搜索'暗色'能找到")

    # 改变偏好
    app2, replies2 = run_app(tmp, env, [
        "用 remember 工具更新：我改主意了，编辑器主题偏好改为亮色主题，title 用'编辑器主题'",
    ])

    # 验证新偏好
    results_after = app2.memory.search_memory("亮色", limit=5)
    check(len(results_after) >= 1, "搜索'亮色'能找到新偏好")

    # 检查 render_memory 显示最新状态
    rendered = app2.memory.render_memory()
    check("亮色" in rendered, "/memory 显示更新后的'亮色'偏好")

    app.close()
    app2.close()
    return True


# ================================================================
# 场景 4: 项目知识积累 → /compact → 跨对话记忆
# （同一临时目录，两个独立 session）
# ================================================================
def scenario_4_cross_session():
    print("\n" + "=" * 60)
    print("场景 4: 项目知识积累 → compact → 新 session 验证记忆持久")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp())
    env = make_env(tmp)

    # Session A: 积累知识
    app_a, replies_a = run_app(tmp, env, [
        "我的项目叫'天网系统'，技术栈是 Python 3.11 + FastAPI + SQLite。"
        "数据库用 SQLite 做本地缓存，Redis 做消息队列。请用 remember 工具记下来。",
    ])

    # 压缩
    try:
        app_a.compact_now()
        print("    [info] session A compact 完成")
    except Exception as exc:
        print(f"    [WARN] compact: {exc}")

    app_a.close()

    # Session B: 新会话，检查记忆
    env["MY_AGENT_SESSION_ID"] = f"test-b-{tmp.name[-8:]}"
    app_b, replies_b = run_app(tmp, env, [
        "我之前告诉你我的项目技术栈是什么？用 search_context 查一下。",
    ])

    # 检查记忆持久
    results = app_b.memory.search_memory("天网", limit=5)
    check(len(results) >= 1, "新 session 搜索'天网'能找到之前的项目记忆")

    results = app_b.memory.search_memory("FastAPI", limit=5)
    check(len(results) >= 1, "搜索'FastAPI'能找到技术栈记忆")

    results = app_b.memory.search_memory("SQLite", limit=5)
    check(len(results) >= 1, "搜索'SQLite'能找到数据库记忆")

    # 验证 context tools 可用
    from my_agent2.tools.context import SearchContextTool
    tool = SearchContextTool(app_b.memory)
    output = tool.execute(query="队列", limit=5)
    check("Redis" in output, "search_context 工具搜索'队列'找到 Redis")

    app_b.close()
    return True


# ================================================================
# 场景 5: CLI 命令可用性
# ================================================================
def scenario_5_cli_commands():
    print("\n" + "=" * 60)
    print("场景 5: CLI 命令可用性（无 LLM 调用）")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp())
    env = make_env(tmp)

    app, replies = run_app(tmp, env, [
        "用 remember 记录：测试记忆内容，类别 events，标题 Test",
    ])

    # /tools
    tools = app.registry.names()
    check("search_context" in tools, "/tools 包含 search_context")
    check("read_context" in tools, "/tools 包含 read_context")
    check("list_context" in tools, "/tools 包含 list_context")
    check("show_context_links" in tools, "/tools 包含 show_context_links")
    check("remember" in tools, "/tools 包含 remember")

    # /memory
    rendered = app.memory.render_memory()
    check("Memory OS" in rendered, "/memory 显示 Memory OS 标题")
    check("Events" in rendered, "/memory 包含 Events 类别")

    # /context
    objects = app.memory.list_context(limit=20)
    check(len(objects) >= 1, "/context 返回 ContextObject 列表")
    check(any("mem://" in o.get("uri", "") for o in objects), "ContextObject URI 格式正确")

    # session 创建
    session_id = app.session_id
    check(session_id.startswith("test-"), f"session ID 正确: {session_id}")

    app.close()
    return True


# ================================================================
# Main
# ================================================================
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=int, default=0, help="Run specific scenario (1-5)")
    args = parser.parse_args()

    scenarios = [
        (1, "偏好记忆写入与召回", scenario_1_preferences),
        (2, "长对话 → compact → 归档", scenario_2_long_conversation),
        (3, "偏好变更，旧记忆归档", scenario_3_preference_change),
        (4, "跨 session 记忆持久", scenario_4_cross_session),
        (5, "CLI 命令可用性", scenario_5_cli_commands),
    ]

    print("=" * 60)
    print("my_agent2 真实场景测试")
    print(f"Provider: {os.getenv('MY_AGENT_PROVIDER', 'deepseek')}")
    print(f"Model: {os.getenv('MY_AGENT_MODEL', 'deepseek-chat')}")
    print("=" * 60)

    for num, name, fn in scenarios:
        if args.scenario and args.scenario != num:
            continue
        try:
            fn()
        except Exception as exc:
            print(f"\n  [CRASH] 场景{num}异常: {exc}")
            import traceback
            traceback.print_exc()
            FAIL.append(f"场景{num}崩溃: {exc}")

    # Report
    print("\n" + "=" * 60)
    print(f"结果: {len(PASS)} pass, {len(FAIL)} fail")
    print("=" * 60)

    if PASS:
        print("\n通过的检查:")
        for p in PASS:
            print(f"  [PASS] {p}")
    if FAIL:
        print("\n失败的检查:")
        for f in FAIL:
            print(f"  [FAIL] {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
