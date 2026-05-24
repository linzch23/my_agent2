# my_agent2 能力清单

本文记录 `my_agent2` 当前已经具备的核心能力、主要位置和已知差距，方便后续维护。

| 能力 | 状态 | 主要位置 | 说明 |
|---|---:|---|---|
| CLI 入口 | 已完成 | `src/my_agent2/cli.py`、`src/my_agent2/loop.py` | 已通过 `pyproject.toml` 打包为 `uv` 项目。 |
| 模型客户端 | 已完成 | `src/my_agent2/model_client.py` | 支持 `deepseek`、`anthropic` 和 `openai-compatible`。 |
| 工具调用 runner | 已完成 | `src/my_agent2/runner.py` | 使用 provider-neutral blocks，并支持安全工具并行调用。 |
| 工具注册与参数校验 | 已完成 | `src/my_agent2/tools/base.py`、`registry.py` | schema helper 较轻量，校验基础 JSON schema 类型。 |
| shell 工具 | 已完成 | `src/my_agent2/tools/shell.py` | 限定工作区 cwd，支持超时。 |
| 网页抓取工具 | 已完成 | `src/my_agent2/tools/web.py` | 支持文本提取和原始 HTML。 |
| 文件读写编辑 | 已完成 | `src/my_agent2/tools/filesystem.py` | 支持工作区逃逸保护。 |
| 搜索工具 | 已完成 | `src/my_agent2/tools/filesystem.py` | 支持基础 glob/regex 搜索。 |
| Todo 规划 | 已完成 | `src/my_agent2/tools/state.py` | 保持完整列表覆盖模型，并限制同时只有一个 `in_progress`。 |
| 技能加载器 | 已完成 | `src/my_agent2/skills.py`、`tools/state.py` | 支持嵌套 `SKILL.md`、技能摘要、`always: true`，并在依赖未安装时使用 fallback frontmatter 解析。 |
| 内置技能 | 部分完成 | `skills/summarize/SKILL.md` | 当前只内置通用 summarization 技能。 |
| system prompt 构造 | 已完成 | `src/my_agent2/context.py`、`templates/system.md` | 注入 memory、user profile 和 skills。 |
| 长期记忆 | 已完成 | `src/my_agent2/memory.py`、`contextfs.py`、`memory_graph.py` | ContextFS-backed Memory OS with structured memory objects, URI-addressable with L0/L1/L2 layers, MemoryGraph relationship indexing, session archiving from tree compaction, runtime context injection per model call. Legacy MEMORY.md compatibility preserved. |
| 用户画像记忆 | 已完成 | `templates/USER.md`、`MemoryStore.read_user/write_user` | 由 compaction 更新。 |
| 情景记忆 | 已完成 | `MemoryStore.append_episode` | 使用 UTC+8 日期文件。 |
| 原始历史日志 | 已完成 | `MemoryStore.append_history` | 记录用户输入和最终 assistant 输出。 |
| 启动归档 | 已完成 | `MemoryStore.load_unarchived_history`、`HistoryCompactor.compact_startup` | 在 system prompt 构造前归档未标记的上次会话。 |
| 自动历史压缩 | 已完成 | `src/my_agent2/compactor.py`、`runner.py`、`memory.py` | 可按 token 阈值或 history 长度 fallback 触发。 |
| 会话树压缩 | 已完成 | `src/my_agent2/tree_session.py` | `/compact` 会在当前 active branch 追加结构化 compaction entry。 |
| token 日志 | 已完成 | `TokenLog` | 记录 provider-neutral 的 input/output token 字段，时间使用 UTC+8。 |
| token 聚合 | 已完成 | `TokenLog.stats_by_date`、`stats_by_model` | 支持基础聚合。 |
| 一次性子代理 | 已完成 | `src/my_agent2/subagents`、`tools/dispatch.py` | 通用角色：researcher、analyst、coder、reviewer。 |
| 子代理工具白名单 | 已完成 | `src/my_agent2/subagents/registry.py` | 安全设置写在代码里，不写在 prompt 模板里。 |
| 并行子代理派遣 | 已完成 | `runner.py`、`DispatchSubagentTool.concurrency_safe` | 独立 dispatch 调用可以并行执行。 |
| 持久 Agent Team | 已完成 | `src/my_agent2/team.py` | 支持命名队友、持久配置、inbox 和线程。 |
| Team 工具 | 已完成 | `src/my_agent2/tools/team.py` | 注册给 lead 和 teammate agent。 |
| Team CLI 快捷命令 | 已完成 | `src/my_agent2/cli.py` | 支持 `/team` 和 `/inbox`。 |
| 运行期目录忽略 | 已完成 | `.gitignore` | 避免提交运行生成状态。 |

## 剩余差距

- 文件搜索和编辑工具已经可用，但功能仍偏轻量；后续可增加分页、过滤和更强的编辑匹配。
- 内置技能保持克制。后续应根据项目方向添加通用技能。
- 当前环境没有跑真实 DeepSeek/Anthropic API 集成测试；已有测试主要使用 fake model。
- 超大单轮对话的 prefix/suffix 拆分压缩还未完整接入主流程。

## 当前验证

- `python3 -m compileall src`
- smoke tests 覆盖：
  - 文件工具和 todo 工具
  - provider-neutral 工具调用转换
  - fake model 下的历史压缩
  - 三层记忆写入：`MEMORY.md`、`USER.md`、每日情景记忆
  - 启动压缩 marker 行为
  - always-active skills 和 system prompt 注入
  - message bus 和 team 工具
  - 使用 fake model 启动 teammate 线程
