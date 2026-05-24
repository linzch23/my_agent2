我调研后的判断：不要把 `gbrain` 当成要整体迁入的 agent 框架；它真正值得融入的是 `Context/Memory Kernel（上下文/记忆内核）` 的机制层。你们当前蓝本 `my_agent2` 的 runner、tool、MCP、subagent/team 基础已经够用，比赛重心应放在把记忆从“文件日志 + 压缩摘要”升级为“可检索、可追溯、可治理的结构化上下文系统”。

**核心结论**

`gbrain` 的亮点不是“更会聊天”，而是把长期知识管理做成了一个可持续运转的 brain：写入即结构化、自动链接成图、混合检索、事实时间线、后台维护、自修复与评测闭环。它的 README 明确描述了写入自动抽取实体关系、typed links（类型化关系边）、Hybrid Search（混合检索）、Structured timeline（结构化时间线）和 Cron enrichment（定时增强）这些机制；检索文档也说明它不是只靠向量，而是组合 Vector（向量）、BM25（关键词）、RRF（倒数排序融合）、Knowledge Graph（知识图谱）和 reranker（重排器）。来源：[README](https://raw.githubusercontent.com/garrytan/gbrain/master/README.md)、[retrieval architecture](https://raw.githubusercontent.com/garrytan/gbrain/master/docs/architecture/RETRIEVAL.md)。

**建议融入模块**

1. `MemoryPage（记忆页）` + `Source（来源域）`
   你们现在的 [memory.py](<D:\agent架构比赛\my_agent2\src\my_agent2\memory.py:1>) 主要是 `MEMORY.md`、`history.jsonl`、每日 episode、compactions。建议新增一层结构化记忆对象：
   `slug/type/title/content/frontmatter/timeline/source_id/provenance/confidence/created_at/updated_at`。
   这能直接承接你们 AgentOS 的 `Memory OS（记忆操作系统）` 概念。

2. `Context Object（上下文对象）` 与 `Runtime Context（运行时上下文）` 分离
   当前 [context.py](<D:\agent架构比赛\my_agent2\src\my_agent2\context.py:1>) 是一次性拼 system prompt。建议吸收 `gbrain-context` 的做法：每轮 deterministic context injection（确定性上下文注入），单独注入时间、任务、当前工作区、最近决策、可用 memory，而不是完全依赖压缩摘要。来源：[gbrain context engine](https://raw.githubusercontent.com/garrytan/gbrain/master/src/core/context-engine.ts)。

3. `AutoLinker（自动链接器）`
   优先移植轻量版：扫描 Markdown 链接、wikilink、实体 slug，自动生成 `mentions/depends_on/decided_in/produced_by/contradicts/supersedes` 等关系边。`gbrain` 的强点是“每次写入都让图谱变新”，这比单纯做 RAG 更适合比赛展示。

4. `HybridMemorySearch（混合记忆检索）`
   MVP 不要上完整 pgvector + reranker。先做：
   - BM25/SQLite FTS 或简单关键词索引
   - embedding 可选
   - RRF 融合
   - source/type/recency/trust 权重
   - graph 邻居扩展  
   这能讲清楚“ContextFS（上下文文件系统）不是堆 prompt，而是可治理检索面”。

5. `FactTimeline（事实时间线）`
   建议吸收 `gbrain` 的 `Facts fence（事实栏）` 和 temporal trajectory（时间轨迹）思想，但做比赛版：
   `fact/entity/kind/valid_from/valid_until/source/confidence/superseded_by`。
   这可以支撑你们已有的“可审计、可恢复、可回滚”叙事，比普通 memory 更有创新性。

6. `BrainMaintenanceCycle（记忆维护循环）`
   不建议完整复制 `Minions（后台任务队列）`，但应实现轻量维护周期：
   `sync -> extract_links -> extract_facts -> dedupe -> detect_conflicts -> compact -> index`。
   这能展示 memory 不是静态文件，而是会自我整理的系统。

**不建议融入**

`Minions（后台任务队列）`、HTTP OAuth MCP server（远程认证服务）、多 engine PGLite/Postgres 抽象、完整 skillpack 生态、admin UI、复杂 benchmark 框架。它们工程量大，会稀释你们比赛重点。当前阶段只需要借鉴机制，不要复制生态。

**如果我是总负责人**

我会把比赛版架构定为：

`my_agent2` 继续负责 Agent Runner（智能体运行器）、Tool Layer（工具层）、MCP Bridge（MCP 桥接）、Subagents（子智能体）；新增一个 `ContextMemoryKernel（上下文记忆内核）`，内部包含 `ContextFS（上下文文件系统）`、`Memory OS（记忆操作系统）`、`FactTimeline（事实时间线）`、`GraphIndex（图索引）`、`HybridRetriever（混合检索器）`、`MaintenanceCycle（维护循环）`。

一句话参赛表述可以是：

> 我们不是再做一个 agent runner，而是在现有 agent 底座上加入可溯源、可授权、可审计、可恢复的 Context-Memory Kernel，让智能体从“会调用工具”升级为“能治理自己的上下文和长期记忆”。

**推荐落地顺序**

MVP：
`MemoryPage` 数据模型、写入 API、自动链接、事实时间线、检索前注入 Runtime Context。

增强：
混合检索、关系图遍历、冲突检测、记忆晋升规则、维护循环。

暂缓：
分布式队列、远程 brain server、多租户 OAuth、完整评测平台。

搜索摘要
- 网站：GitHub / raw.githubusercontent.com | 查询词：garrytan/gbrain README、RETRIEVAL、brains-and-sources、context-engine、skills resolver | 次数：直接读取仓库与源码
- OpenCLI：执行了 `opencli list -f yaml` 和 `opencli github -h` 预检；未用 OpenCLI 做真实站点搜索
- 本地：浅克隆 `garrytan/gbrain` 到临时目录并阅读源码；同时检查了 `my_agent2` 的 README、context、memory、runner、compactor