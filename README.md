# my_agent2

`my_agent2` 是一个本地运行的通用型 Python Agent，面向命令行工作流、工具调用、
会话树管理、上下文压缩和多 Agent 协作场景。

## 上下文与记忆架构

my_agent2 采用多层上下文与记忆系统：

- **TreeSession（树形会话）** — 追加式 JSONL 会话树，支持分支、跳转、分叉和克隆。原始会话数据的唯一事实来源。
- **ContextFS（上下文文件系统）** — 持久化上下文/记忆对象存储，分为 L0（摘要）、L1（概览）和 L2（完整正文）三层。对象以 URI 寻址。
- **MemoryGraph（记忆图谱）** — ContextFS URI 之间的轻量级链接索引（支持、矛盾、更新、相关、派生自）。
- **RuntimeContextBuilder（运行时上下文构建器）** — 每轮调用前搜索 ContextFS 并将相关记忆注入模型上下文。
- **SessionMemoryCommitter（会话记忆提交器）** — 连接树压缩与长期记忆的桥梁，由 `/compact` 触发。

### 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MY_AGENT_RUNTIME_CONTEXT_LIMIT` | `6` | 运行时上下文搜索返回条数上限 |
| `MY_AGENT_RUNTIME_CONTEXT_MAX_CHARS` | `12000` | 运行时上下文输出字符预算 |
| `MY_AGENT_CONTEXT_BACKEND` | `local` | 上下文后端选择（MVP 仅 `local`） |
| `MY_AGENT_AUTO_LINK_FANOUT` | `5` | 自动链接候选记忆数 |
| `MY_AGENT_AUTO_LINK_MIN_CONFIDENCE` | `0.3` | 自动链接最低置信度 |

### 上下文工具

| 工具 | 说明 |
|---|---|
| `search_context` | 按关键词搜索结构化记忆（覆盖 L0/L1/L2） |
| `read_context` | 按 URI 读取上下文对象 |
| `list_context` | 列出指定 URI 前缀下的对象 |
| `show_context_links` | 查看记忆图谱链接关系 |
| `remember` | 将持久化笔记写入长期记忆（已升级） |

它目前支持：

- DeepSeek、Anthropic、OpenAI-compatible 三类模型接口
- 命令行对话中的流式输出
- stdio 和 Streamable HTTP 两类 MCP 工具接入
- 工作区文件工具：`read_file`、`write_file`、`edit_file`、`glob`、`grep`
- shell 命令执行和网页抓取
- 持久化会话日志、轻量长期记忆和用户偏好
- 基于会话树的上下文压缩，压缩入口为明确命令 `/compact`
- `update_todos` 任务规划工具
- `skills/{name}/SKILL.md` 技能加载
- 用于调研、分析、编码、审查的一次性子代理
- 带命名队友和 inbox 的持久多 Agent 协作
- 只读工具和独立子代理调用的并行执行

## 快速开始

```bash
uv sync
cp .env.example .env
# 编辑 .env，至少填写 DEEPSEEK_API_KEY

uv run my-agent2
```

在这台 Mac 上，更稳定的启动方式是：

```bash
./run.sh
```

`run.sh` 会自动定位项目目录、设置 `PYTHONPATH`、切换到仓库根目录，并使用虚拟环境里的
Python 入口启动程序。这样可以避免终端当前目录和 editable install 带来的导入问题。

启动本地 Web 工作台：

```bash
./run_web.sh
```

默认监听 `http://127.0.0.1:8765`。Web 工作台复用同一个 `AgentApp` 应用层，
通过 HTTP/SSE 暴露聊天流、会话树、active branch、context debug、工具、MCP 和 team 状态。
会话仍然持久化在 `sessions/*.jsonl`，JSONL 仍是事实来源。

默认 `.env` 配置使用 DeepSeek：

```bash
MY_AGENT_PROVIDER=deepseek
MY_AGENT_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
MY_AGENT_MAX_CONTEXT_TOKENS=64000
MY_AGENT_COMPACT_THRESHOLD=0.7
MY_AGENT_COMPACT_KEEP_MESSAGES=8
MY_AGENT_STARTUP_COMPACTION=0
```

## MCP 工具

`my_agent2` 可以连接外部 MCP server，并把远端工具暴露给 Agent 使用。配置文件位于项目根目录的
`mcp_servers.json`。

示例：

```json
{
  "mcpServers": {
    "filesystem": {
      "transport": "stdio",
      "command": "uvx",
      "args": ["mcp-server-filesystem", "."],
      "env": {
        "SOME_TOKEN": "${SOME_TOKEN}"
      }
    },
    "local_http": {
      "transport": "streamable_http",
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_TOKEN}"
      }
    }
  }
}
```

如果配置里提供了 `command` 或 `url`，`transport` 可以省略。MCP 工具会注册为
`mcp_{server}_{tool}` 形式，非法字符会被替换成 `_`。当前 MCP 集成只暴露工具，
还没有映射 MCP resources 和 prompts。

## 命令行指令

在 CLI 里可以使用这些命令：

- `/help`：查看命令帮助
- `/tools`：列出已注册工具
- `/todos`：查看当前任务列表
- `/memory`：查看长期记忆
- `/mcp`：查看 MCP server 状态和发现的 MCP 工具
- `/compact`：明确触发当前会话分支的上下文压缩
- `/team`：查看持久队友状态
- `/inbox`：读取并清空 lead inbox
- `/tree [--filter default|no-tools|user-only|labeled-only|all]`：查看 JSONL 会话树
- `/jump ID`：把 active leaf 移到某个历史节点
- `/fork ID`：从某个历史节点分叉，下一条输入会创建兄弟分支
- `/clone`：把当前 active branch 克隆成新 session 并切换过去
- `/label ID LABEL`：给会话树节点打标签
- `/exit`：退出

## 会话树与上下文压缩

会话持久化在 `sessions/` 下的 append-only JSONL 文件里。JSONL 文件是事实来源；
程序启动时会通过重放文件重建内存索引。

`/compact` 会在当前 active branch 上追加一个 `compaction` 条目：

- 旧上下文被压成结构化 checkpoint summary
- 最近上下文保留原文
- 原始历史不会被删除
- 后续模型上下文会自动使用“压缩摘要 + 最近消息”

压缩是命令式触发，不会被普通自然语言对话误触发。

## 项目结构

```text
src/my_agent2/
  cli.py              CLI 入口
  server.py           本地 Web/API 入口
  loop.py             应用装配
  runner.py           模型与工具调用循环
  compactor.py        旧版线性历史压缩
  tree_session.py     会话树、分支、标签和结构化压缩
  team.py             持久队友管理和 inbox 消息总线
  memory.py           日志、长期记忆和 token 记录
  skills.py           技能加载器
  context.py          system prompt 构造器
  tools/              内置工具实现
  subagents/          通用子代理注册表
templates/
  system.md           主 Agent 提示词
  subagents/*.md      子代理角色提示词
skills/
  summarize/SKILL.md  示例技能
```

## 说明

默认情况下，文件工具只允许访问配置的工作区。相对路径会从 `MY_AGENT_WORKSPACE` 或当前目录解析。
