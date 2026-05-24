结论先说：OpenViking 值得借鉴，但不建议整体接入。我们的取舍应该是“复用 `my_agent2` 的 runner/tool/subagent 底座，只把 context/memory 层重构成一个轻量版 Context Kernel（上下文内核）”。OpenViking 的强项正好在这里：它不是 Agent 执行框架，而是面向 Agent 的 Context Database（上下文数据库）。

**OpenViking 核心机制**
OpenViking 的基本定位是把 `Memory（记忆）`、`Resource（资源）`、`Skill（技能）` 三类上下文统一放进一个虚拟文件系统，用 `viking://...` URI（统一资源标识符）访问。官方架构文档明确把系统拆成 `Retrieve（上下文检索）`、`Session（会话管理）`、`Parse（上下文提取）`、`Compressor（压缩/去重）`、`Storage（存储层）`，底层是 `AGFS（内容存储） + 向量库（索引存储）` 双层结构。[架构概述](https://github.com/volcengine/OpenViking/blob/main/docs/zh/concepts/01-architecture.md)

它的上下文不是一次性塞进 prompt，而是 L0/L1/L2 三层：
- `L0（摘要）`：约 100 tokens，用于快速过滤和向量搜索。
- `L1（概览）`：约 1k-2k tokens，用于规划和导航。
- `L2（详情）`：原始完整内容，只在必要时读取。[上下文层级](https://github.com/volcengine/OpenViking/blob/main/docs/zh/concepts/03-context-layers.md)

检索有两层：`find()` 是低延迟简单检索；`search()` 是 session-aware（会话感知）深检索，会用 LLM 做意图分析，生成 0-5 个 `TypedQuery（类型化查询）`，再按 memory/resource/skill 做层级递归检索和 rerank（重排）。[检索机制](https://github.com/volcengine/OpenViking/blob/main/docs/zh/concepts/07-retrieval.md)

会话管理也比普通摘要强：`Session（会话）` 记录 message、context 使用、skill 使用和 tool 调用；`commit()` 分两阶段，先同步归档，再异步生成摘要、提取长期记忆、写 `memory_diff.json` 审计日志。[会话管理](https://github.com/volcengine/OpenViking/blob/main/docs/zh/concepts/08-session.md)

记忆体系有 8 类：用户侧 `profile/preferences/entities/events`，Agent 侧 `cases/patterns/tools/skills`。这点很关键，它把“用户偏好”和“Agent 执行经验”分开，避免所有长期信息都混进一个 `MEMORY.md`。[上下文类型](https://github.com/volcengine/OpenViking/blob/main/docs/zh/concepts/02-context-types.md)

**真正亮点**
1. **把 RAG 从“黑盒召回”变成“文件系统操作”**  
   Agent 可以 `list/read/search/forget` 明确操作上下文，而不是只拿一堆向量片段。

2. **L0/L1/L2 是很强的比赛叙事点**  
   它天然回答“为什么不会上下文爆炸”：先摘要定位，再概览判断，最后按需展开。

3. **Memory 是生命周期机制，不是一个工具调用**  
   Codex 插件的设计是：`UserPromptSubmit` 自动召回，`Stop` 增量捕获但不 commit，`PreCompact` 前确定性 commit。这比“让模型记得调用 remember”更可靠。[Codex memory plugin](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md)

4. **写入一致性有明确哲学**  
   它用路径锁和 Redo Log（重做日志）保护 `rm/mv/add_resource/session.commit`，原则是“宁可搜不到，不要搜到坏结果”。这和我们之前的“可审计、可恢复”主线高度一致。[事务模型](https://github.com/volcengine/OpenViking/blob/main/docs/zh/concepts/09-transaction.md)

5. **接口边界清晰**  
   MCP（模型上下文协议）端点直接暴露 `search/read/list/add_resource/forget/code_outline/code_search/code_expand/health` 等能力，说明它把 context/memory 做成可被不同 Agent runtime 复用的后端。[MCP 集成指南](https://github.com/volcengine/OpenViking/blob/main/docs/zh/guides/06-mcp-integration.md)

**对我们项目的融入判断**
如果我是总负责人，我会这样取舍：

保留 `my_agent2` 现有模块：
- `AgentRunner（执行循环）`
- `ModelClient（模型客户端）`
- `ToolRegistry（工具注册中心）`
- 文件/shell/web/todo/subagent/team 等工具

重点改造这些模块：

1. `memory.py` → 升级为 `MemoryOS（记忆系统）`  
   不再只维护 `MEMORY.md`，改成分类目录：`user/profile`、`user/preferences`、`project/entities`、`agent/cases`、`agent/patterns`、`agent/tools`、`agent/skills`。

2. `compactor.py` → 升级为 `SessionCommitter（会话提交器）`  
   当前只是压缩历史；应该改成“归档 + L0/L1 摘要 + 记忆提取 + diff 审计”。

3. `context.py` → 升级为 `RuntimeContextBuilder（运行时上下文构建器）`  
   每次模型调用前，根据用户输入检索相关 L0/L1，组装 Runtime Context，而不是把全部 memory 直接塞进 system prompt。

4. 新增 `ContextFS（上下文文件系统）`  
   用轻量 URI 即可，不必照搬 `viking://`。建议用我们已有概念：`ctx://resources/...`、`mem://user/...`、`mem://agent/...`、`skill://...`。

5. 新增 `ContextRetriever（上下文检索器）`  
   MVP 不必上向量库。先做 `L0/L1 + 关键词/regex + 简单打分`，比赛 demo 足够；后续再接 embedding。

6. 新增 `MemoryDiff（记忆变更审计）`  
   每次记忆写入都记录 `adds/updates/deletes`，这会直接增强“可信、可审计、可恢复”的答辩说服力。

**不建议融入的部分**
不建议直接搬：
- Rust/C++ `AGFS/RAGFS`
- 完整向量库和 rerank 服务
- HTTP Server、OAuth、Web Studio
- watch 自动刷新资源
- 全量 MCP server
- OpenViking 源码实现

原因很直接：复杂度和依赖会压过比赛主线，而且 OpenViking 是 AGPL-3.0 许可证，直接复制源码有合规风险。我们应该借鉴架构思想和机制，不复制实现。

**推荐落地路线**
MVP 阶段只做四件事：

1. `Context Object（上下文对象）` 标准结构：`uri/type/layer/source/trust/sensitivity/ttl/content/hash`。
2. L0/L1/L2 文件布局：每个资源或记忆都有 `.abstract.md`、`.overview.md`、原文。
3. 会话提交：把 `history` 归档成 session archive，并提取分类 memory。
4. 运行时召回：用户输入前检索相关 context，注入到模型调用。

这样我们的比赛叙事会变成：

“我们没有重新造一个 Agent runner，而是在现有 Agent 底座上加了一个可治理的 Context/Memory Kernel。它把上下文从 prompt 字符串升级成可分层、可检索、可审计、可恢复的运行时资产。”

搜索摘要
- 网站：GitHub 官方仓库 | 查询/对象：`volcengine/OpenViking`、README、docs、examples、源码 main 分支 | 次数：1 次网页打开 + 1 次浅克隆源码核验
- 工具：opencli | 操作：`opencli list -f yaml`、`opencli github -h` 预检 | 次数：0 次真实站点搜索
- 其他：Git | 操作：`git ls-remote`、`git clone --depth 1` | 用于确认 main 分支源码和文件结构