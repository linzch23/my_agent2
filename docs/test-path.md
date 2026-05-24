 ---  完整交互测试流程
  启动：
  .\.venv\Scripts\Activate.ps1

  $env:PYTHONPATH="src"
  $env:MY_AGENT_SESSION_ID="default"

  python -m my_agent2

  ---
  第1步：验证工具注册

  输入：
  /tools

  预期结果： 工具列表中包含以下新工具：
  - search_context
  - read_context
  - list_context
  - show_context_links
  - remember（原有，已升级）

  ---
  第2步：对话中写入记忆

  输入你这条内容来让 agent 使用 remember 工具：
  帮我记住两件事：1）我偏好使用暗色主题，所有编辑器都用暗色；2）项目技术选型是 Python 3.11 标准库，不依赖外部数据库

  预期结果： Agent 会调用 remember 工具（可能调用两次），每次返回 Remembered: mem://user/preferences/... 或 Remembered:
  mem://project/constraints/...

  ---
  第3步：再积累一些对话

  接着随便聊几句，让会话树有足够内容：
  介绍一下 Python 3.11 相比之前版本的重要改进

  Agent 回复后再说一句：
  暗色主题对眼睛保护有什么科学依据吗

  预期结果： 正常对话回复，会话树积累消息。

  ---
  第4步：触发压缩 → 自动记忆提交

  输入：
  /compact

  预期结果： 看到类似输出：
  [memory] session archive: ctx://sessions/archives/2026/05/24/default-xxxx
  已压缩当前上下文。
  如果没有 [memory] 行，说明消息量还没达到压缩阈值，再聊几轮后重试。

  ---
  第5步：查看结构化记忆

  输入：
  /memory

  预期结果： 输出类似：
  # Memory OS

  ## Preferences
  - [暗色主题](mem://user/preferences/...) trust=0.8

  ## Constraints
  - [Python 3.11](mem://project/constraints/...) trust=0.8

  ## Legacy
  - [preferences] 用户偏好使用暗色主题...
  - [constraints] 项目技术选型是 Python 3.11...

  ---
  第6步：查看上下文对象列表

  输入：
  /context

  预期结果： 列出所有 ContextObject，每行包含 URI、类型、标题和信任度：
  - mem://user/preferences/2026/05/24/暗色主题 [memory] 暗色主题 trust=0.8
  - mem://project/constraints/2026/05/24/python-311 [memory] Python 3.11 trust=0.8
  - ctx://sessions/archives/2026/05/24/default-xxxx [session] Session Archive trust=0.7

  ---
  第7步：测试上下文搜索

  输入：
  用 search_context 搜索"暗色"

  预期结果： Agent 调用 search_context 工具，返回匹配的记忆列表（含
  URI、Title、Trust、Abstract），"暗色主题偏好"排在最前面。

  ---
  第8步：测试读取上下文

  输入：
  用 read_context 读取 mem://user/preferences 开头的记忆

  （或直接指定上一步搜索结果中的某个具体 URI）

  预期结果： 返回该记忆的完整内容（layer=auto 返回概览，layer=full 返回正文）。

  ---
  第9步：测试上下文链接

  输入：
  用 show_context_links 查看上一步那个 URI 的链接关系

  预期结果： 显示 derived_from → ctx://sessions/archives/... 链接，表明这条记忆来源于哪个 session archive。

  ---
  第10步：验证运行时上下文注入

  退出重启 CLI，然后输入一条与已有记忆相关的问题：
  我想换个编辑器主题

  预期结果： 虽然你看不到 system prompt 内部，但 Agent 应该在回复中提及你之前记录的"暗色主题偏好"——这说明
  RuntimeContextBuilder 成功从 ContextFS 召回了相关记忆并注入到了当前对话。

  ---
  快速检查清单

  ┌──────┬────────────────────┬──────────────────────────────────┐
  │ 步骤 │     命令/操作      │             通过标志             │
  ├──────┼────────────────────┼──────────────────────────────────┤
  │ 1    │ /tools             │ 含 search_context 等 4 个新工具  │
  ├──────┼────────────────────┼──────────────────────────────────┤
  │ 2    │ 让 agent remember  │ 返回 mem:// URI                  │
  ├──────┼────────────────────┼──────────────────────────────────┤
  │ 3    │ 多轮对话           │ 正常回复                         │
  ├──────┼────────────────────┼──────────────────────────────────┤
  │ 4    │ /compact           │ 打印 [memory] session archive:   │
  ├──────┼────────────────────┼──────────────────────────────────┤
  │ 5    │ /memory            │ 分类显示 Preferences/Constraints │
  ├──────┼────────────────────┼──────────────────────────────────┤
  │ 6    │ /context           │ 列出 URI + trust                 │
  ├──────┼────────────────────┼──────────────────────────────────┤
  │ 7    │ search_context     │ 按相关度排序                     │
  ├──────┼────────────────────┼──────────────────────────────────┤
  │ 8    │ read_context       │ 返回记忆内容                     │
  ├──────┼────────────────────┼──────────────────────────────────┤
  │ 9    │ show_context_links │ 显示 derived_from                │
  ├──────┼────────────────────┼──────────────────────────────────┤
  │ 10   │ 重启后提问         │ agent 引用之前的记忆             │
  └──────┴────────────────────┴──────────────────────────────────┘