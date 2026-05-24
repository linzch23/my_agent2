# Context/Memory 系统代码审查报告

> 6 个并行 agent 审查，2026-05-24，只读不改

## 统计

| 审查模块 | 致命 | 严重 | 一般 | 建议 |
|----------|------|------|------|------|
| ContextFS | 5 | 0 | 6 | 1 |
| MemoryGraph | 0 | 4 | 7 | 1 |
| Memory OS | 0 | 7 | 4 | 3 |
| Committer + Extractor | 1 | 5 | 2 | 0 |
| RuntimeContext + Tools | 3 | 1 | 8 | 4 |
| AgentApp + CLI | 4 | 4 | 6 | 4 |
| **合计** | **13** | **21** | **33** | **13** |

---

## 致命缺陷 (13)

### F-01: URI 路径穿越漏洞 — contextfs.py

`_uri_to_path` 将 `mem://../../../etc/passwd` 转为 `mem/../../../etc/passwd.md`，拼接到 `self.root` 后穿越文件系统。攻击者可构造 URI 写入任意位置。

### F-02: `_load_index` 无 JSON 解析容错 — contextfs.py

`index.jsonl` 部分写入（崩溃残留）时 `json.loads` 直接抛异常，`ContextFS.__init__` 崩溃，程序无法启动。`_upsert_index` 非原子覆写 + 无容错加载 = 组合必崩溃。

### F-03: `write_object` 非原子写入 — contextfs.py

`_upsert_index` 先截断 `index.jsonl` 再写入。进程崩溃 → 文件不完整 → 下次启动 F-02 触发。

### F-04: `search_objects` L2 全文搜索 OOM — contextfs.py

每个匹配条目都 `read_text` 读取整个 L2 文件到内存。100 条目 × 50MB = 5GB 内存峰值。

### F-05: `read_object(layer="full")` 空 content_path 崩溃 — contextfs.py

`content_path=""` 时 `self.root / ""` = 目录，`read_text()` → `IsADirectoryError`。

### F-06: 敏感/隔离记忆未过滤 — context.py

`RuntimeContextBuilder.build` 不检查 `sensitivity`/`status`。`status=quarantine`、`sensitivity=internal`、`status=archived` 的记忆原样泄露到 system prompt。

### F-07: RememberTool category 无校验 — tools/state.py

`category` 参数无 `enum` 约束。`remember(note="xxx", category="preference")`（单数）静默接受，写为 `mem://user/events/` 而非 `mem://user/preferences/`。与 `commit_session_archive` 不一致（后者有 strict 集合校验）。

### F-08: 中文标题 slug 为空 — memory.py

`slug = re.sub(r"[^\w\s-]", "", title)` — `\w` 只匹配 ASCII，中文全部剔除。slug 为空 → URI `mem://user/events/2026/05/24/`（尾部缺标识），不同中文记忆撞 URI，误归档不相关记忆。

### F-09: `_fallback_render` None 值触发 TypeError — context.py

```python
for key, value in values.items():
    rendered = rendered.replace("{{ " + key + " }}", value)
```

`value` 为 `None` 时 `str.replace` 抛 `TypeError`。Jinja2 路径无此问题，fallback 路径有。

### F-10: `_parse_operations_fallback` 花括号深度计数 — session_memory_committer.py

字符级扫描不知 JSON 字符串边界。LLM 输出 `{"abstract": "使用 {memory} 管理"}` → 字符串内 `{}` 扰乱深度计数 → 解析失败。中文 prompt 鼓励中文输出，中文常用 `{}` 占位符。

### F-11: IndexError 首次启动无 session — loop.py

```python
self.session_id = custom_session_id or self.tree.listSessions()[0]
```

`custom_session_id` 为 None 且 `listSessions()` 空时 → `IndexError`。首次运行不设 `MY_AGENT_SESSION_ID` 必崩。

### F-12: 资源泄漏 — loop.py

`close()` 只关 MCP。`TeammateManager` 和 `TreeSessionManager` 可能有子进程/后台线程，不关闭导致资源泄漏、进程无法退出。没有 `__enter__`/`__exit__`。

### F-13: 初始化中途失败无清理 — loop.py

`__init__` 中任何步骤失败（MCP 已 start、tree 已创建、registry 半填充）→ 资源泄漏。`close()` 只在正常退出路径被调用。

---

## 严重缺陷 (21)

### S-01: `_parse_auto_link_json` 不处理 Markdown fence — memory_graph.py

LLM 输出 ` ```json\n[...]\n``` ` 时，解析器提取的文本包含结尾 `` ``` ``，`json.loads` 失败，大量 LLM 成功调用被错误降级到 keyword。

### S-02: `_parse_auto_link_json` 多数组场景提取错误 — memory_graph.py

LLM 输出 `[{...}] 解释 [{...}]` 时，`rindex("]")` 提取整个中间文本，`json.loads` 失败。

### S-03: `_write_links` 非原子 — memory_graph.py

`write_text` 先截断再写。崩溃 → 全部链接丢失。

### S-04: LLM auto_link 无自环防护 — memory_graph.py

`_llm_auto_link` 直接信任 LLM 返回的 `target_uri`，可能创建 `A → A` 自环链接。

### S-05: `_count_bigram_overlap` 精确 URI break 阻止归档 — memory.py

`remember_note` 中精确 URI 匹配后 `break` 退出循环，阻止后续条目的 bigram 归档检测。

### S-06: `"append"` action 通过校验但行为同 upsert — memory.py

`commit_session_archive` 接受 `"append"` 但处理逻辑与 `"upsert"` 完全相同（覆盖写入），调用方期望追加实际却替换。

### S-07: `content_path` 与 URI 不一致 — memory.py

`content_path=f"mem/{category}/{key}.md"` 使用原始 key（含空格、中文），而 URI 使用 slug 化 key。`events` 类别的 content_path 缺日期部分。

### S-08: `render_memory` 缺 4 个类别 — memory.py

`entities`、`open_tasks`、`tools`、`skills` 不在 `render_memory` 的 `categories` 字典中。这四个类别的记忆对 LLM 不可见。

### S-09: 新增类别需改 4 处 — memory.py

`_operation_to_uri`、`_category_prefix`、`valid_categories`、`render_memory.categories` 各硬编码 if/elif 链。4 处中已有 3 处不同步。

### S-10: `remember_note` 先归档后写入，中间失败无补偿 — memory.py

先 `write_object(old_obj)` 将旧记忆标记 archived，再 `write_object(new_obj)` 写新记忆。第二步失败时旧记忆已归档、新记忆不存在。

### S-11: `commit_session_archive` 逐操作提交无原子性 — memory.py

循环中每条操作独立写入。第 5 条失败时前 4 条已持久化，无回滚。

### S-12: LLM extract 异常消息泄露敏感信息 — session_memory_committer.py

`f"extraction_failed: {e}"` 将完整异常串（可能含 API key 片段）写入持久化存储。

### S-13: `_parse_extraction_json` markdown fence 缺失时 ValueError — session_memory_committer.py

LLM 输出 ` ```json\n{...} ` 但缺结尾 `` ``` ``，`text.index("```", start)` → `ValueError`。明明有合法 JSON 却被放弃。

### S-14: 提取 prompt 缺关键指令 — session_memory_committer.py

| 遗漏项 | 影响 |
|--------|------|
| 冲突信息（先A后B）处理规则 | LLM 不知道标注哪个为当前值 |
| quarantine/append 使用场景 | LLM 可能从不使用或滥用 |
| 空结果约定 | LLM 可能编造记忆来"完成任务" |
| links target_uri 如何确定 | LLM 无现有记忆索引，只能编造或留空 |

### S-15: AgentApp ask 流程 step 内多轮 tool use 时 token 漏检 — loop.py

`should_compact` 只检查最后一次 `create_message` 的 input tokens。tool-use 循环中前几次高 token 调用被忽略，上下文可能已严重超限。

### S-16: compact 两步非原子 — loop.py

`_compact_active_branch()` 成功但 `commit_compaction()` 失败 → tree 已 compact 但 memory 未归档，`compact_now` 返回 `True` 误导调用方。

### S-17: `_compact_active_branch` Summarizer 每次新建 — loop.py

LlmSummarizer 每次创建新实例，若构造或调用中抛异常直接穿透。

### S-18: `_build_registry` 内 MCP 生命周期耦合 — loop.py

MCP `start()` 在 `_build_registry` 内部执行。构造失败时 MCP 已启动但无机会 `close()`。

### S-19: `_build_registry` 内 `self.team` 副作用赋值 — loop.py

`self.team = TeammateManager(...)` 在 registry 构建方法内部。构造失败 → 前面已注册的工具 + MCP 处于脏状态。

### S-20: close() 仅关 MCP — loop.py

无 TeammateManager 关闭、无 TreeSessionManager 清理、无 MemoryStore flush。

### S-21: `SendMessageTool`/`ReadInboxTool` 多实例注册静默覆盖 — loop.py

类级别 `name` 属性不因 sender 参数变化。注册多个不同 sender 实例时后者静默覆盖前者。

---

## 一般缺陷 (33)

<details>
<summary>展开查看全部</summary>

| ID | 模块 | 描述 |
|----|------|------|
| G-01 | ContextFS | `_read_index_lines` 不必要序列化/反序列化双份内存 |
| G-02 | ContextFS | 空 URI 不校验，产生隐藏文件 `.md` |
| G-03 | ContextFS | 超大内容无防护，`write_text` 一次全量载入 |
| G-04 | ContextFS | CJK 字符覆盖不全（缺扩展 B-G、假名、谚文） |
| G-05 | ContextFS | 混合 CJK+ASCII token 产生噪音 bigram（"a中"） |
| G-06 | ContextFS | `_is_expired` 吞异常，无效 TTL 当永不过期 |
| G-07 | MemoryGraph | add_link 等置信度丢弃新 reason |
| G-08 | MemoryGraph | 死链接永不清理（记忆删除后残留在图） |
| G-09 | MemoryGraph | 链接只增不减，无删除 API |
| G-10 | MemoryGraph | 中英混排产生无意义 bigram（"I模"） |
| G-11 | MemoryGraph | keyword 匹配未用 abstract/overview |
| G-12 | MemoryGraph | LLM 降级无日志，难以排查 |
| G-13 | MemoryGraph | 无 source_uri 索引，万级链接全量扫描 |
| G-14 | Memory OS | bigram 阈值固定 2，不归一化标题长度 |
| G-15 | Memory OS | 空/纯符号标题产生畸形 URI |
| G-16 | Memory OS | 会话归档缺 diff 审计条目 |
| G-17 | Memory OS | MEMORY.md 和 Memory OS 双写但永不同步 |
| G-18 | Committer | dict 分支死代码（getBranch 从不返回 dict） |
| G-19 | Committer | debug 信息全文写入持久存储（长会话数十 KB） |
| G-20 | RuntimeContext | max_chars 超限跳过全部条目时与 0 结果同输出 |
| G-21 | RuntimeContext | neighbors() 调用无异常处理 |
| G-22 | RuntimeContext | max_chars 裁剪无 trust_score 排序 |
| G-23 | RuntimeContext | ContextBackend.remember 方法无调用路径（死代码） |
| G-24 | Tools | 所有工具的 limit 参数缺 min/max 约束 |
| G-25 | Tools | SearchContextTool 用方括号访问 `r['uri']`，数据损坏时 KeyError |
| G-26 | Tools | RememberTool 描述未列有效 category 值 |
| G-27 | AgentApp | auto-compact 失败被静默吞掉，无降级告警 |
| G-28 | AgentApp | `EnvFlag` 仅用于一处，布尔配置无统一入口 |
| G-29 | AgentApp | MCP 工具名冲突仅 warning，无覆盖/别名策略 |
| G-30 | AgentApp | should_compact 阈值判断有反复触发风险 |
| G-31 | AgentApp | `_compact_active_branch` 异常无直接保护 |
| G-32 | AgentApp | teammate_tools 闭包 role 字符串硬编码 |
| G-33 | AgentApp | startup compaction 无后续联动（仅 warning） |

</details>

---

## 建议 (13)

<details>
<summary>展开查看全部</summary>

| ID | 模块 | 描述 |
|----|------|------|
| R-01 | ContextFS | 纯 CJK 长 token 原样保留为无效搜索项 |
| R-02 | MemoryGraph | 无并发保护，文件锁缺失 |
| R-03 | MemoryGraph | 英文标题字符 bigram 语义弱（"ea"、"ar" 偶然匹配） |
| R-04 | MemoryGraph | 加载时无去重，历史残留永久保留 |
| R-05 | Memory OS | 标题双重 slug 化（无害但暴露不一致） |
| R-06 | Memory OS | `_category_prefix` 中 profile 无尾部斜杠，不一致 |
| R-07 | Memory OS | 无防护阻止 write_memory("") 清空旧版破坏新状态 |
| R-08 | Tools | ListContextTool 输出无 overview，模型需额外 read_context |
| R-09 | Tools | SearchContextTool 描述提到未解释的 L0/L1/L2 术语 |
| R-10 | Tools | ListContextTool 描述未说明默认前缀格式 |
| R-11 | Tools | LocalContextBackend 参数类型为 Any，无运行时检查 |
| R-12 | Context | fallback 渲染器存在模板变量注入风险（用户写 MEMORY.md 可注入） |
| R-13 | AgentApp | 11 个 os.getenv 分散在多处，应聚拢 |

</details>

---

## 修复优先级建议

### 第一批：安全和数据完整性 (12 项)

| ID | 简要 |
|----|------|
| F-01 | URI 路径穿越 |
| F-02 | _load_index JSON 容错 |
| F-03 | write_object 原子写入 |
| F-11 | 首次启动 IndexError |
| F-09 | _fallback_render None → TypeError |
| F-10 | JSON 解析花括号计数 |
| F-08 | 中文 slug 为空 |
| S-05 | bigram 精确 URI break |
| S-06 | "append" 行为同 upsert |
| S-07 | content_path 不匹配 |
| S-08 | render_memory 缺 4 类别 |
| S-09 | 4 处同步改一源 |

### 第二批：功能正确性 (10 项)

| ID | 简要 |
|----|------|
| F-06 | 敏感/隔离记忆未过滤 |
| F-07 | RememberTool category 无校验 |
| S-01 | auto_link 不处理 markdown fence |
| S-03 | _write_links 非原子 |
| S-10 | remember_note 先归档后写入 |
| S-11 | commit 逐操作无原子性 |
| F-12 | 资源泄漏 |
| F-13 | 初始化无清理 |
| S-14 | 提取 prompt 缺指令 |
| S-16 | compact 两步非原子 |

### 第三批：健壮性 (8 项)

| ID | 简要 |
|----|------|
| F-04 | search_objects OOM |
| F-05 | read_object 空 content_path |
| S-02 | auto_link 多数组 |
| S-04 | 自环防护 |
| S-12 | 异常消息泄露 |
| S-13 | fence 缺失 ValueError |
| S-15 | tool use 多轮 token 漏检 |
| S-17~S-21 | AgentApp 各类健壮性 |

### 第四批：体验和优化

所有一般和建议级别缺陷。

---

> 审查 agent: 6 个并行 · 审查文件: 12 个 · 总问题: 80 个
