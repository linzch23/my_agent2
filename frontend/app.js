const demoRun = {
  sessionId: "demo",
  title: "修复上下文注入缺陷",
  meta: "mock fallback · Trust Console",
  messages: [
    {
      id: "m1",
      role: "user",
      time: "09:41",
      text: "修复 Agent 每轮调用没有注入 Runtime Context 的问题，并保留可审计证据。",
      tools: [],
    },
    {
      id: "m2",
      role: "assistant",
      time: "09:42",
      text: "我会先搜索相关记忆，再读取 L1 概览；命中后沿 MemoryGraph 扩展邻近决策，最后把必要片段注入本轮上下文。",
      tools: [
        {
          id: "t1",
          name: "search_context",
          status: "done",
          input: { query: "Runtime Context 注入", limit: 5 },
          output: "mem://project/decisions/runtime-context\nmem://agent/patterns/context-budget",
        },
        {
          id: "t2",
          name: "show_context_links",
          status: "done",
          input: { uri: "mem://project/decisions/runtime-context", limit: 5 },
          output: "derived_from -> ctx://sessions/archives/2026/05/24/default-c1\nrelated -> mem://agent/patterns/context-budget",
        },
        {
          id: "t3",
          name: "read_context",
          status: "done",
          input: { uri: "mem://project/decisions/runtime-context", layer: "auto" },
          output: "L1 overview: 每轮模型调用前根据用户输入检索 ContextFS，并注入相关记忆。",
        },
      ],
    },
    {
      id: "m3",
      role: "assistant",
      time: "09:46",
      text: "完成后会话压缩会生成 CompactionEntry，并由 SessionMemoryCommitter 写入 ContextFS 与 MemoryGraph。",
      tools: [
        {
          id: "t4",
          name: "remember",
          status: "done",
          input: { category: "decisions", title: "Runtime Context 注入边界" },
          output: "Remembered: mem://project/decisions/runtime-context-boundary",
        },
      ],
    },
  ],
  tree: [
    { id: "root", parentId: null, type: "session_info", label: "session", preview: "修复上下文注入缺陷" },
    { id: "u1", parentId: "root", type: "message", label: "user", preview: "修复 Runtime Context 注入" },
    { id: "a1", parentId: "u1", type: "message", label: "assistant", preview: "搜索相关记忆并扩展 MemoryGraph" },
    { id: "tc1", parentId: "a1", type: "tool_call", label: "search_context", preview: "query=Runtime Context 注入" },
    { id: "tc2", parentId: "a1", type: "tool_call", label: "show_context_links", preview: "MemoryGraph neighbors" },
    { id: "c1", parentId: "tc2", type: "compaction", label: "compaction", preview: "压缩为结构化 checkpoint 并提交记忆" },
  ],
  contextDebug: {
    includedEntryIds: ["root", "u1", "a1", "tc1", "tc2", "c1"],
    estimatedTokens: 1840,
    compactionApplied: true,
  },
  contextObjects: [
    {
      uri: "mem://project/decisions/runtime-context",
      context_type: "memory",
      title: "Runtime Context 注入",
      overview: "每轮模型调用前根据用户输入检索 ContextFS，并注入相关记忆。",
      trust_score: 0.92,
    },
    {
      uri: "mem://agent/patterns/context-budget",
      context_type: "memory",
      title: "Context Budget",
      overview: "默认注入 L1 overview，只有按需读取时进入 L2 full text。",
      trust_score: 0.81,
    },
  ],
  activeLeafId: "c1",
  memory: "# Long-term Memory\n\n- Runtime Context 必须每轮按用户输入检索并注入，而不是只在启动时构造。",
};

const state = {
  run: structuredClone(demoRun),
  sessions: [],
  apiMode: false,
  navMode: "messages",
  leftMode: "chat",
  selectedToolId: "t2",
  selectedTreeId: demoRun.activeLeafId,
  activeUserMessageId: "m1",
  searchFlowStep: 0,
  searchFlowPlaying: false,
  sending: false,
};

const SEARCH_FLOW_AUTOPLAY_DELAY = 1500;
let searchFlowTimer = null;

const $ = (selector) => document.querySelector(selector);
const nodes = {
  connectionState: $("#connection-state"),
  sessionLabel: $("#session-label"),
  newSessionButton: $("#new-session-button"),
  sideNavEyebrow: $("#side-nav-eyebrow"),
  sideNavTitle: $("#side-nav-title"),
  sideNavTabs: [...document.querySelectorAll(".side-nav-tab")],
  userMessageNav: $("#user-message-nav"),
  modeTabs: [...document.querySelectorAll(".mode-tab")],
  chatView: $("#chat-view"),
  treeView: $("#tree-view"),
  composer: $("#composer"),
  composerInput: $("#composer-input"),
  sendButton: $("#send-button"),
  vizTitle: $("#viz-title"),
  vizBadge: $("#viz-badge"),
  vizContent: $("#viz-content"),
  toast: $("#toast"),
};

function currentTool() {
  const tools = allTools();
  if (state.selectedToolId === "demo-memorygraph") {
    return demoRun.messages.flatMap((message) => message.tools).find((tool) => tool.id === "t2");
  }
  return tools.find((tool) => tool.id === state.selectedToolId) || tools[0] || null;
}

function allTools() {
  return state.run.messages.flatMap((message) =>
    (message.tools || []).map((tool) => ({ ...tool, messageId: message.id })),
  );
}

async function loadData() {
  stopSearchFlowAutoplay();
  try {
    const sessionsPayload = await apiGet("/api/sessions");
    const sessions = Array.isArray(sessionsPayload.sessions) ? sessionsPayload.sessions : [];
    const selected = pickSession(sessions, sessionsPayload.activeSessionId);
    if (!selected) throw new Error("no sessions found");

    const sessionId = selected.id;
    const [runsPayload, treePayload, memoryPayload, contextDebug, contextPayload, toolsPayload] = await Promise.all([
      apiGet(`/api/sessions/${encodeURIComponent(sessionId)}/runs`),
      apiGet(`/api/sessions/${encodeURIComponent(sessionId)}/tree`),
      apiGet("/api/memory"),
      apiGet("/api/context/debug").catch(() => ({})),
      apiGet("/api/context?limit=200").catch(() => ({ objects: [] })),
      apiGet("/api/tools").catch(() => ({ tools: [] })),
    ]);

    state.run = normalizeApiRun({
      selected,
      runsPayload,
      treePayload,
      memoryPayload,
      contextDebug,
      contextPayload,
      toolsPayload,
    });
    state.sessions = normalizeSessionSummaries(sessions, sessionsPayload.activeSessionId);
    state.apiMode = true;
    state.selectedToolId = pickPreferredTool(allTools())?.id || "demo-memorygraph";
    state.selectedTreeId = state.run.activeLeafId || state.selectedTreeId || state.run.tree.at(-1)?.id || "";
  } catch (error) {
    state.run = structuredClone(demoRun);
    state.sessions = demoSessions();
    state.apiMode = false;
    state.selectedTreeId = state.run.activeLeafId || "";
    showToast(`使用演示数据：${error.message}`);
  }
  render();
}

function pickPreferredTool(tools) {
  const searchWithResults = tools.find((tool) => isSearchTool(tool) && parseSearchResults(tool).length);
  return (
    searchWithResults ||
    tools.find((tool) => isSearchTool(tool)) ||
    tools.find((tool) => isGraphTool(tool)) ||
    tools.find((tool) => isReadTool(tool)) ||
    tools.find((tool) => isRememberTool(tool)) ||
    tools[0]
  );
}

async function apiGet(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${path} ${response.status}`);
  return response.json();
}

async function apiPost(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${path} ${response.status}`);
  return data;
}

function pickSession(sessions, activeSessionId) {
  const normalized = sessions
    .map((item) => (typeof item === "string" ? { id: item } : item))
    .filter((item) => item && item.id);
  return (
    normalized.find((item) => item.id === activeSessionId) ||
    [...normalized].sort((a, b) => String(a.updatedAt || "").localeCompare(String(b.updatedAt || ""))).at(-1)
  );
}

function normalizeSessionSummaries(sessions, activeSessionId) {
  return sessions
    .map((item) => (typeof item === "string" ? { id: item } : item))
    .filter((item) => item && item.id)
    .map((item) => ({
      id: String(item.id),
      title: item.title || `Session ${shortId(item.id)}`,
      recordCount: item.recordCount ?? 0,
      createdAt: item.createdAt || "",
      updatedAt: item.updatedAt || item.createdAt || "",
      activeLeafId: item.activeLeafId || "",
      active: item.id === activeSessionId,
    }))
    .sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
}

function demoSessions() {
  return [
    {
      id: state.run.sessionId || "demo",
      title: state.run.title || "演示会话",
      recordCount: state.run.messages.length,
      createdAt: "",
      updatedAt: "",
      activeLeafId: state.run.activeLeafId || "",
      active: true,
    },
  ];
}

function normalizeApiRun({ selected, runsPayload, treePayload, memoryPayload, contextDebug, contextPayload, toolsPayload }) {
  const steps = Array.isArray(runsPayload.runs) ? runsPayload.runs : [];
  const toolEvents = Array.isArray(runsPayload.toolEvents) ? runsPayload.toolEvents : [];
  const messages = [];

  for (const step of steps) {
    if (step.kind !== "user" && step.kind !== "assistant") continue;
    const stepTools = Array.isArray(step.toolCalls) ? step.toolCalls : [];
    messages.push({
      id: step.id || crypto.randomUUID(),
      role: step.kind === "user" ? "user" : "assistant",
      time: formatTime(step.createdAt),
      text: step.output || step.summary || "",
      tools: stepTools.map((tool) => normalizeTool(tool, step.id)),
    });
  }

  if (!messages.length) {
    messages.push({
      id: "empty",
      role: "assistant",
      time: "",
      text: "当前 session 暂无可展示对话。可以在左下角发送一个任务。",
      tools: toolEvents.map((tool) => normalizeTool(tool, "empty")),
    });
  }

  const knownToolIds = new Set(messages.flatMap((message) => message.tools.map((tool) => tool.id)));
  const looseTools = toolEvents.map((tool) => normalizeTool(tool, "loose")).filter((tool) => !knownToolIds.has(tool.id));
  if (looseTools.length) {
    messages.push({
      id: "tool-events",
      role: "assistant",
      time: "",
      text: "这些工具调用来自 session JSONL，可点击在右侧查看可视化结果。",
      tools: looseTools,
    });
  }

  return {
    sessionId: selected.id,
    title: selected.title || `Session ${selected.id}`,
    meta: `real JSONL · ${selected.recordCount ?? steps.length} records`,
    messages,
    tree: (treePayload.nodes || []).map((node) => ({
      id: node.id,
      parentId: node.parentId || null,
      type: node.type || "node",
      label: node.label || node.type || "",
      preview: node.preview || "",
      status: node.status || "normal",
    })),
    contextDebug,
    contextObjects: Array.isArray(contextPayload.objects) ? contextPayload.objects : [],
    activeLeafId: selected.activeLeafId || (treePayload.nodes || []).find((node) => node.status === "active")?.id || "",
    memory: memoryPayload.memory || "",
    toolsCatalog: toolsPayload.tools || [],
  };
}

function normalizeTool(tool, fallbackId) {
  const input = tool.input && typeof tool.input === "object" ? tool.input : {};
  return {
    id: tool.id || tool.toolUseId || `${fallbackId}-${tool.name || "tool"}`,
    name: tool.name || "tool",
    status: normalizeStatus(tool.status),
    input,
    output: tool.output || "",
  };
}

function normalizeStatus(status) {
  return status === "running" || status === "error" || status === "pending" ? status : "done";
}

function render() {
  nodes.connectionState.textContent = state.apiMode ? "real API" : "demo data";
  nodes.sessionLabel.textContent = `session: ${state.run.sessionId || "session"}`;
  nodes.modeTabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.leftMode === state.leftMode));
  nodes.sideNavTabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.navMode === state.navMode));
  nodes.chatView.classList.toggle("active", state.leftMode === "chat");
  nodes.treeView.classList.toggle("active", state.leftMode === "tree");
  renderSideNav();
  renderChat();
  renderTree();
  renderVisualization();
}

function renderSideNav() {
  if (state.navMode === "sessions") {
    nodes.sideNavEyebrow.textContent = "Sessions";
    nodes.sideNavTitle.textContent = "历史会话";
    nodes.userMessageNav.className = "user-message-nav session-nav-list";
    renderSessionNav();
  } else {
    nodes.sideNavEyebrow.textContent = "User Turns";
    nodes.sideNavTitle.textContent = "消息定位";
    nodes.userMessageNav.className = "user-message-nav";
    renderUserNav();
  }
}

function renderUserNav() {
  const userMessages = state.run.messages.filter((message) => message.role === "user");
  if (!userMessages.length) {
    nodes.userMessageNav.replaceChildren(emptyUserNav());
    return;
  }
  nodes.userMessageNav.replaceChildren(
    ...userMessages.map((message, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `user-nav-item ${message.id === state.activeUserMessageId ? "active" : ""}`;
      button.innerHTML = `
        <span class="user-nav-index">${index + 1}</span>
        <span class="user-nav-copy">
          <span>${escapeHtml(message.time || "user")}</span>
          <strong>${escapeHtml(compactText(message.text, 58))}</strong>
        </span>
      `;
      button.addEventListener("click", () => jumpToUserMessage(message.id));
      return button;
    }),
  );
}

function renderSessionNav() {
  const sessions = state.sessions.length ? state.sessions : demoSessions();
  if (!sessions.length) {
    nodes.userMessageNav.replaceChildren(emptySideNav("暂无历史会话"));
    return;
  }
  nodes.userMessageNav.replaceChildren(
    ...sessions.map((session, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `session-nav-item ${session.id === state.run.sessionId ? "active" : ""}`;
      button.innerHTML = `
        <span class="session-nav-index">${index + 1}</span>
        <span class="session-nav-copy">
          <strong>${escapeHtml(compactText(session.title || session.id, 42))}</strong>
          <span>${escapeHtml(sessionTimeLabel(session))} · ${escapeHtml(String(session.recordCount ?? 0))} records</span>
          <code>${escapeHtml(shortId(session.id))}</code>
        </span>
      `;
      button.addEventListener("click", () => selectSession(session.id));
      return button;
    }),
  );
}

async function selectSession(sessionId) {
  if (!sessionId || sessionId === state.run.sessionId) return;
  stopSearchFlowAutoplay();
  if (!state.apiMode) {
    showToast("演示模式下没有可切换的真实历史会话。");
    return;
  }
  try {
    await apiPost("/api/sessions/select", { sessionId });
    state.selectedToolId = "";
    state.selectedTreeId = "";
    state.activeUserMessageId = "";
    state.searchFlowStep = 0;
    await loadData();
    showToast(`已切换到 session: ${shortId(sessionId)}`);
  } catch (error) {
    showToast(`切换会话失败：${error.message}`);
  }
}

function sessionTimeLabel(session) {
  const value = session.updatedAt || session.createdAt;
  if (!value) return "no timestamp";
  return formatTime(value);
}

function emptyUserNav() {
  return emptySideNav("暂无用户消息");
}

function emptySideNav(text) {
  const div = document.createElement("div");
  div.className = "user-nav-empty";
  div.textContent = text;
  return div;
}

function renderChat() {
  nodes.chatView.replaceChildren(...state.run.messages.map(renderMessage));
}

function renderMessage(message) {
  const article = document.createElement("article");
  article.className = `message-card ${message.role} ${message.loading ? "loading" : ""} ${message.id === state.activeUserMessageId ? "located" : ""}`;
  article.dataset.messageId = message.id;

  const head = document.createElement("div");
  head.className = "message-head";
  head.innerHTML = `<span class="message-role">${messageRoleLabel(message.role)}</span><span>${escapeHtml(message.time)}</span>`;

  const text = document.createElement("p");
  text.className = "message-text";
  if (message.loading && !message.text) {
    text.append("Agent 正在思考");
    text.appendChild(typingDots());
  } else {
    text.textContent = message.text || (message.role === "tool" ? "工具调用完成" : "(empty)");
    if (message.loading) text.appendChild(typingDots());
  }

  article.append(head, text);
  if (message.tools?.length) {
    const strip = document.createElement("div");
    strip.className = "tool-strip";
    for (const tool of message.tools) strip.appendChild(renderToolChip(tool));
    article.appendChild(strip);
  }
  return article;
}

function jumpToUserMessage(messageId) {
  state.activeUserMessageId = messageId;
  state.leftMode = "chat";
  render();
  window.requestAnimationFrame(() => {
    const target = nodes.chatView.querySelector(`[data-message-id="${CSS.escape(messageId)}"]`);
    if (!target) return;
    target.scrollIntoView({ block: "start", behavior: "smooth" });
    target.classList.add("flash-locate");
    window.setTimeout(() => target.classList.remove("flash-locate"), 1100);
  });
}

function messageRoleLabel(role) {
  if (role === "user") return "User";
  if (role === "tool") return "Tool";
  return "Agent";
}

function typingDots() {
  const dots = document.createElement("span");
  dots.className = "typing-dots";
  dots.innerHTML = "<i></i><i></i><i></i>";
  return dots;
}

function renderToolChip(tool) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `tool-chip ${toolKind(tool)} ${tool.status || ""} ${tool.id === state.selectedToolId ? "active" : ""}`;
  button.textContent = `${tool.name} · ${tool.status}`;
  button.addEventListener("click", () => {
    stopSearchFlowAutoplay();
    state.selectedToolId = tool.id;
    state.searchFlowStep = 0;
    render();
  });
  return button;
}

function scrollChatToBottom() {
  window.requestAnimationFrame(() => {
    nodes.chatView.scrollTop = nodes.chatView.scrollHeight;
  });
}

function appendLiveMessage(message) {
  state.run.messages.push(message);
  if (message.role === "user") state.activeUserMessageId = message.id;
  render();
  scrollChatToBottom();
}

function updateLiveMessage(id, patch) {
  const index = state.run.messages.findIndex((message) => message.id === id);
  if (index === -1) return;
  state.run.messages[index] = { ...state.run.messages[index], ...patch };
  render();
  scrollChatToBottom();
}

async function sendChatStream(message) {
  const userId = crypto.randomUUID();
  const assistantId = crypto.randomUUID();
  const pendingTools = new Map();

  appendLiveMessage({ id: userId, role: "user", time: "now", text: message, tools: [] });
  appendLiveMessage({ id: assistantId, role: "assistant", time: "streaming", text: "", tools: [], loading: true });

  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `/api/chat/stream ${response.status}`);
  }

  let assistantText = "";
  await readSse(response, {
    user_message: () => {},
    delta: (payload) => {
      assistantText += payload.text || "";
      updateLiveMessage(assistantId, { text: assistantText, loading: true });
    },
    tool_call: (payload) => {
      const tool = normalizeStreamTool(payload, "running");
      pendingTools.set(tool.id, tool);
      updateLiveMessage(assistantId, {
        text: assistantText || `正在调用 ${tool.name}...`,
        loading: true,
      });
    },
    tool_result: (payload) => {
      const result = normalizeStreamToolResult(payload, pendingTools);
      pendingTools.delete(result.id);
      state.selectedToolId = result.id;
      appendLiveMessage({
        id: `tool-${result.id}-${Date.now()}`,
        role: "tool",
        time: "done",
        text: `${result.name} 完成`,
        tools: [result],
      });
      updateLiveMessage(assistantId, { text: assistantText || "工具调用已完成，正在整理回复...", loading: true });
    },
    done: async (payload) => {
      assistantText = payload.reply || assistantText;
      updateLiveMessage(assistantId, { text: assistantText || "(no reply)", loading: false });
    },
    error: (payload) => {
      updateLiveMessage(assistantId, { text: `流式回复失败：${payload.error || "unknown error"}`, loading: false });
    },
  });
}

async function readSse(response, handlers) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const eventText of events) {
      const eventName = dispatchSseEvent(eventText, handlers);
      if (eventName === "done" || eventName === "error") {
        await reader.cancel().catch(() => {});
        return;
      }
    }
  }
  if (buffer.trim()) dispatchSseEvent(buffer, handlers);
}

function dispatchSseEvent(eventText, handlers) {
  let eventName = "message";
  const dataLines = [];
  for (const line of eventText.split(/\r?\n/)) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  const dataText = dataLines.join("\n");
  const payload = dataText ? JSON.parse(dataText) : {};
  handlers[eventName]?.(payload);
  return eventName;
}

function normalizeStreamTool(payload, status) {
  return {
    id: payload.id || payload.tool_use_id || payload.toolUseId || crypto.randomUUID(),
    name: payload.name || "tool",
    status,
    input: payload.input && typeof payload.input === "object" ? payload.input : {},
    output: payload.output || payload.content || "",
  };
}

function normalizeStreamToolResult(payload, pendingTools) {
  const id = payload.tool_use_id || payload.toolUseId || payload.id || payload.name || crypto.randomUUID();
  const pending = pendingTools.get(id) || {};
  const output = payload.output || payload.content || payload.result || "";
  return {
    id,
    name: payload.name || pending.name || "tool",
    status: normalizeStatus(payload.status || (String(output).toLowerCase().startsWith("error") ? "error" : "done")),
    input: payload.input && typeof payload.input === "object" ? payload.input : pending.input || {},
    output,
  };
}

function renderTree() {
  const rootKey = "__root__";
  const nodesByParent = new Map();
  for (const node of state.run.tree || []) {
    const key = node.parentId || rootKey;
    if (!nodesByParent.has(key)) nodesByParent.set(key, []);
    nodesByParent.get(key).push(node);
  }

  const actionBar = renderTreeActionBar();
  const root = document.createElement("div");
  root.className = "tree-list";
  const roots = nodesByParent.get(rootKey) || state.run.tree.filter((node) => !node.parentId);
  const rootDepth = roots.length > 1 ? 1 : 0;
  const rootMode = roots.length > 1 ? "branch-head" : "trunk";
  for (const item of roots) root.appendChild(renderTreeNode(item, nodesByParent, rootDepth, rootMode));
  nodes.treeView.replaceChildren(actionBar, root);
}

function renderTreeActionBar() {
  const selected = selectedTreeNode();
  const panel = document.createElement("section");
  panel.className = "tree-action-panel";
  panel.innerHTML = `
    <div>
      <h3>${escapeHtml(selected ? selected.label || selected.type : "选择一个节点")}</h3>
      <p>${escapeHtml(selected ? `${selected.id} · ${selected.preview || selected.type}` : "点击会话树节点后，可以从该节点 fork、jump 或打标签。")}</p>
    </div>
    <div class="tree-command-row">
      <button type="button" data-tree-action="fork" ${selected ? "" : "disabled"}>Fork</button>
      <button type="button" data-tree-action="jump" ${selected ? "" : "disabled"}>Jump</button>
      <button type="button" data-tree-action="label" ${selected ? "" : "disabled"}>Label</button>
      <button type="button" data-tree-action="clone">Clone</button>
    </div>
  `;
  panel.querySelector('[data-tree-action="fork"]').addEventListener("click", () => runTreeAction("fork"));
  panel.querySelector('[data-tree-action="jump"]').addEventListener("click", () => runTreeAction("jump"));
  panel.querySelector('[data-tree-action="label"]').addEventListener("click", () => runTreeAction("label"));
  panel.querySelector('[data-tree-action="clone"]').addEventListener("click", () => runTreeAction("clone"));
  return panel;
}

function renderTreeNode(node, nodesByParent, depth, mode = "trunk") {
  const wrap = document.createElement("div");
  const button = document.createElement("button");
  button.type = "button";
  button.className = `tree-node ${escapeAttr(node.type)} tree-${mode} ${node.status === "active" ? "active" : ""} ${node.id === state.selectedTreeId ? "selected" : ""}`;
  button.style.setProperty("--indent", `${depth * 18}px`);
  button.innerHTML = `
    <span class="tree-node-main">
      <span class="tree-node-title">${escapeHtml(node.label || node.type)} · ${escapeHtml(shortId(node.id))}</span>
      <span class="tree-node-preview">${escapeHtml(node.preview || "")}</span>
    </span>
    <span class="status-pill">${escapeHtml(node.status === "active" ? "active" : node.type)}</span>
  `;
  button.addEventListener("click", () => {
    state.selectedTreeId = node.id;
    state.selectedToolId = findToolForTreeNode(node)?.id || state.selectedToolId;
    render();
  });
  wrap.appendChild(button);
  const children = nodesByParent.get(node.id) || [];
  for (const child of children) {
    const childLayout = nextTreeChildLayout(depth, mode, children.length);
    wrap.appendChild(renderTreeNode(child, nodesByParent, childLayout.depth, childLayout.mode));
  }
  return wrap;
}

function nextTreeChildLayout(parentDepth, parentMode, siblingCount) {
  if (siblingCount > 1) return { depth: parentDepth + 1, mode: "branch-head" };
  if (parentMode === "branch-head") return { depth: parentDepth + 1, mode: "branch-body" };
  if (parentMode === "branch-body") return { depth: parentDepth, mode: "branch-body" };
  return { depth: parentDepth, mode: "trunk" };
}

function selectedTreeNode() {
  return (state.run.tree || []).find((node) => node.id === state.selectedTreeId) || null;
}

async function runTreeAction(action) {
  const selected = selectedTreeNode();
  if (action !== "clone" && !selected) return;
  if (!state.apiMode) {
    showToast(`演示模式：${action} 需要 my_agent2 Web API。`);
    return;
  }

  try {
    if (action === "fork") {
      await apiPost("/api/tree/fork", { entryId: selected.id });
      showToast(`已从 ${shortId(selected.id)} 设置 fork 点，下一条输入会创建分支。`);
    } else if (action === "jump") {
      await apiPost("/api/tree/jump", { entryId: selected.id });
      showToast(`已跳转到 ${shortId(selected.id)}。`);
    } else if (action === "label") {
      const label = window.prompt("给这个节点添加标签：", selected.label || "");
      if (label === null) return;
      await apiPost("/api/tree/label", { entryId: selected.id, label: label.trim() });
      showToast("标签已更新。");
    } else if (action === "clone") {
      await apiPost("/api/tree/clone", {});
      showToast("已克隆当前 active branch 到新 session。");
    }
    await loadData();
    state.leftMode = "tree";
  } catch (error) {
    showToast(`${action} 失败：${error.message}`);
  }
}

async function createNewSession() {
  const title = window.prompt("新会话标题（可留空）：", "非编码长任务测试");
  if (title === null) return;
  if (!state.apiMode) {
    const id = `demo-${Date.now().toString(36)}`;
    state.run = {
      ...structuredClone(demoRun),
      sessionId: id,
      title: title.trim() || "新演示会话",
      messages: [
        {
          id: "new-demo-message",
          role: "assistant",
          time: "new",
          text: "演示模式已创建本地新会话。启动 my_agent2 Web API 后会调用 POST /api/sessions 创建真实 session。",
          tools: [],
        },
      ],
      tree: [{ id: "root", parentId: null, type: "session_info", label: "session", preview: title.trim() || "新演示会话", status: "active" }],
      activeLeafId: "root",
    };
    state.selectedTreeId = "root";
    state.selectedToolId = "demo-memorygraph";
    render();
    return;
  }
  try {
    const payload = await apiPost("/api/sessions", { title: title.trim() || undefined });
    const sessionId = payload.sessionId || payload.state?.sessionId;
    showToast(sessionId ? `已创建并切换到 session: ${sessionId}` : "已创建并切换到新 session。");
    await loadData();
  } catch (error) {
    showToast(`新会话创建失败：${error.message}`);
  }
}

function findToolForTreeNode(node) {
  const label = `${node.label || ""} ${node.preview || ""}`.toLowerCase();
  return allTools().find((tool) => label.includes(tool.name.toLowerCase()));
}

function renderVisualization() {
  const tool = currentTool();
  if (!tool) {
    nodes.vizTitle.textContent = "选择一个工具调用";
    nodes.vizBadge.textContent = "Memory OS";
    nodes.vizContent.replaceChildren(emptyState("点击左侧对话里的工具调用，右侧会展示工具结果，而不是在对话框内展开。"));
    return;
  }

  nodes.vizTitle.textContent = tool.name;
  nodes.vizBadge.textContent = toolKindLabel(tool);
  if (!isSearchTool(tool)) stopSearchFlowAutoplay();
  if (isGraphTool(tool)) renderGraphTool(tool);
  else if (isSearchTool(tool)) renderSearchTool(tool);
  else if (isReadTool(tool)) renderReadContextTool(tool);
  else if (isRememberTool(tool)) renderRememberTool(tool);
  else renderGenericTool(tool);
}

function renderGraphTool(tool) {
  nodes.vizContent.replaceChildren(
    card("MemoryGraph 链接图", "静态展示本次 show_context_links 返回的源节点、关系边和目标节点。没有返回链接时不补虚构节点。", graphStaticComposition(tool)),
    card("原始工具结果", "工具返回仍保留为文本证据，方便审计。", rawBlock({ input: tool.input, output: tool.output })),
  );
}

function renderSearchTool(tool) {
  const model = buildSearchFlowModel(tool);
  const step = Math.max(0, Math.min(state.searchFlowStep, SEARCH_FLOW_STEPS.length - 1));
  nodes.vizContent.replaceChildren(
    card("ContextFS Search 动态流程", "数据来自本次真实 search_context 输出；Graph Walk 只使用同会话里的 show_context_links 结果，不补虚构邻居。", searchFlowComposition(model, step)),
    card("原始工具结果", "工具返回仍保留为审计证据；动态画板负责解释检索路径。", rawBlock(searchEvidencePayload(tool, model))),
  );
}

function renderReadContextTool(tool) {
  const layer = tool.input?.layer === "full" ? "L2" : "L1";
  nodes.vizContent.replaceChildren(
    card("L0 / L1 / L2 读取策略", `本次读取目标层级：${layer}。默认 auto 读取 L1 overview，避免上下文爆炸。`, layerStack(layer)),
    card("选中的上下文对象", "ContextObject 通过 URI 寻址，正文保存在 memory/context/{content_path}。", storageMap(tool)),
    card("读取结果", "这是将进入右侧证据面板的文本，不直接污染左侧对话流。", rawBlock(tool.output || "(empty)")),
  );
}

function renderRememberTool(tool) {
  nodes.vizContent.replaceChildren(
    card("记忆写入路径", "remember 会写入结构化 ContextObject，同时保留旧版 MEMORY.md 兼容。", storageMap(tool)),
    card("Memory Commit", "长期记忆不是每轮随意写入；关键沉淀来自 remember 或 compaction 后的 SessionMemoryCommitter。", layerStack("L0")),
    card("写入证据", "diffs.jsonl 记录变更审计，links.jsonl 记录 MemoryGraph 关系。", rawBlock({ input: tool.input, output: tool.output })),
  );
}

function renderGenericTool(tool) {
  nodes.vizContent.replaceChildren(
    card("工具调用结果", "普通工具也统一在右侧展示，左侧对话只保留工具调用入口和 Agent 输出节奏。", rawBlock({ name: tool.name, input: tool.input, output: tool.output })),
  );
}

function card(title, description, child) {
  const section = document.createElement("section");
  section.className = "viz-card";
  const head = document.createElement("div");
  head.className = "viz-card-head";
  head.innerHTML = `<div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(description)}</p></div>`;
  section.append(head, child);
  return section;
}

function graphStage(graph) {
  const stage = document.createElement("div");
  stage.className = "graph-stage";
  for (const edge of graph.edges) {
    const from = graph.nodes.find((node) => node.id === edge.from);
    const to = graph.nodes.find((node) => node.id === edge.to);
    if (!from || !to) continue;
    const edgeEl = document.createElement("div");
    const x1 = from.x + 59;
    const y1 = from.y + 29;
    const x2 = to.x + 59;
    const y2 = to.y + 29;
    const length = Math.hypot(x2 - x1, y2 - y1);
    const angle = Math.atan2(y2 - y1, x2 - x1) * (180 / Math.PI);
    edgeEl.className = `graph-edge ${edge.active ? "active" : ""}`;
    edgeEl.style.cssText = `left:${x1}px;top:${y1}px;width:${length}px;transform:rotate(${angle}deg)`;
    edgeEl.title = `${edge.relation} ${edge.confidence}`;
    stage.appendChild(edgeEl);
  }
  for (const node of graph.nodes) {
    const nodeEl = document.createElement("div");
    nodeEl.className = `graph-node ${node.kind}`;
    nodeEl.style.left = `${node.x}px`;
    nodeEl.style.top = `${node.y}px`;
    nodeEl.textContent = node.label;
    stage.appendChild(nodeEl);
  }
  return stage;
}

function graphModel(tool) {
  const uri = tool.input?.uri || extractUri(tool.output) || "mem://project/decisions/runtime-context";
  return {
    nodes: [
      { id: "query", label: "query\n用户任务", kind: "seed", x: 18, y: 132 },
      { id: "hit", label: shortUri(uri), kind: "hit", x: 164, y: 132 },
      { id: "archive", label: "session archive\nctx://...", kind: "neighbor", x: 324, y: 56 },
      { id: "pattern", label: "context budget\npattern", kind: "neighbor", x: 324, y: 204 },
      { id: "tool", label: "read_context\nL1/L2", kind: "seed", x: 164, y: 262 },
    ],
    edges: [
      { from: "query", to: "hit", relation: "search", confidence: 0.91, active: true },
      { from: "hit", to: "archive", relation: "derived_from", confidence: 0.95, active: true },
      { from: "hit", to: "pattern", relation: "related", confidence: 0.73, active: true },
      { from: "pattern", to: "tool", relation: "selected", confidence: 0.82, active: false },
    ],
  };
}

function graphStaticComposition(tool) {
  const model = buildGraphStaticModel(tool);
  const board = document.createElement("div");
  board.className = "search-flow-board graph-static-board";
  const title = document.createElement("div");
  title.className = "search-board-title";
  title.innerHTML = `
    <strong>${escapeHtml(shortUri(model.source.uri || "MemoryGraph"))}</strong>
    <span>${model.links.length ? `${model.links.length} 条真实链接` : "当前节点暂无真实链接"}</span>
  `;
  board.append(title, graphStaticCanvas(model));
  return board;
}

function buildGraphStaticModel(tool) {
  const sourceUri = tool.input?.uri || extractUri(tool.output) || "";
  const sourceObject = contextObjectByUri(sourceUri);
  const { links } = parseGraphLinks(tool);
  const source = {
    uri: sourceUri,
    label: sourceObject?.contextType || "source",
    title: sourceObject?.title || shortUri(sourceUri || "source"),
    subtitle: sourceObject?.overview || sourceUri || "show_context_links 输入节点",
    x: 8,
    y: 48,
  };
  const targets = links.map((link, index) => {
    const targetObject = contextObjectByUri(link.target);
    return {
      ...link,
      uri: link.target,
      label: link.relation,
      title: targetObject?.title || shortUri(link.target),
      subtitle: targetObject?.overview || `confidence ${link.confidence}`,
      x: 62,
      y: graphTargetY(index, links.length),
    };
  });
  return { source, links, targets };
}

function graphStaticCanvas(model) {
  const canvas = document.createElement("div");
  canvas.className = "search-board-canvas graph-static-canvas";
  canvas.appendChild(graphStaticEdges(model));
  canvas.appendChild(graphStaticNode(model.source, "source"));
  model.targets.forEach((target) => canvas.appendChild(graphStaticNode(target, "target")));
  if (!model.links.length) {
    const empty = document.createElement("div");
    empty.className = "graph-empty-note";
    empty.textContent = "show_context_links 返回 No links，说明当前 ContextFS 对象还没有显式 MemoryGraph 边。";
    canvas.appendChild(empty);
  }
  canvas.appendChild(graphStaticLegend(model));
  return canvas;
}

function graphStaticEdges(model) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "flow-edges visible graph-static-edges");
  svg.setAttribute("viewBox", "0 0 100 100");
  svg.setAttribute("preserveAspectRatio", "none");
  model.targets.forEach((target) => {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", String(model.source.x + 14));
    line.setAttribute("y1", String(model.source.y + 5));
    line.setAttribute("x2", String(target.x));
    line.setAttribute("y2", String(target.y + 5));
    line.setAttribute("class", "flow-link selected");
    svg.appendChild(line);

    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("x", String((model.source.x + target.x) / 2 + 6));
    label.setAttribute("y", String((model.source.y + target.y) / 2 + 2));
    label.setAttribute("class", "graph-edge-label");
    label.textContent = `${target.relation} ${target.confidence}`;
    svg.appendChild(label);
  });
  return svg;
}

function graphStaticNode(node, kind) {
  const div = document.createElement("div");
  div.className = `flow-node visible ${kind === "source" ? "flow-context-node search-hit selected" : "flow-graph-node selected"}`;
  div.style.setProperty("--x", `${node.x}%`);
  div.style.setProperty("--y", `${node.y}%`);
  div.innerHTML = `
    <span class="flow-node-label">${escapeHtml(node.label)}</span>
    <strong>${escapeHtml(node.title)}</strong>
    <span>${escapeHtml(node.subtitle || "")}</span>
    ${node.confidence ? `<em>${escapeHtml(node.confidence)}</em>` : ""}
  `;
  return div;
}

function graphStaticLegend(model) {
  const div = document.createElement("div");
  div.className = "search-flow-legend";
  div.innerHTML = `
    <span><i class="legend-dot search"></i>源节点</span>
    <span><i class="legend-dot graph"></i>目标节点：${model.targets.length}</span>
    <span><i class="legend-dot selected"></i>链接：${model.links.length}</span>
  `;
  return div;
}

function graphTargetY(index, count) {
  if (count <= 1) return 48;
  const top = count <= 3 ? 28 : 16;
  const gap = count <= 3 ? 20 : 16;
  return top + index * gap;
}

function retrievalFlow() {
  const wrap = document.createElement("div");
  wrap.className = "retrieval-flow";
  [
    ["1 Query", "用户输入或工具参数形成检索词。"],
    ["2 Search L0/L1", "先用摘要和概览低成本定位候选。"],
    ["3 Graph Walk", "沿 MemoryGraph 扩展邻近记忆。"],
    ["4 Select Text", "按预算选择 L1 或按需读取 L2。"],
  ].forEach(([title, body]) => {
    const item = document.createElement("div");
    item.className = "flow-step";
    item.innerHTML = `<strong>${title}</strong><span>${body}</span>`;
    wrap.appendChild(item);
  });
  return wrap;
}

const SEARCH_FLOW_STEPS = [
  ["1 Query", "用户输入或工具参数形成检索词。"],
  ["2 Search L0/L1", "先用摘要和概览低成本定位候选。"],
  ["3 Graph Walk", "沿 MemoryGraph 扩展邻近记忆。"],
  ["4 Select Text", "按预算选择 L1 或按需读取 L2。"],
];

function searchFlowComposition(model, step) {
  const wrap = document.createElement("div");
  wrap.className = "search-flow-composition";

  const controls = document.createElement("div");
  controls.className = "search-flow-controls";
  const playButton = document.createElement("button");
  playButton.type = "button";
  playButton.className = `search-flow-play ${state.searchFlowPlaying ? "playing" : ""}`;
  playButton.textContent = state.searchFlowPlaying ? "暂停" : step >= SEARCH_FLOW_STEPS.length - 1 ? "重播" : "自动播放";
  playButton.addEventListener("click", toggleSearchFlowAutoplay);
  const status = document.createElement("span");
  status.textContent = state.searchFlowPlaying ? "自动推进中" : `当前 ${step + 1}/${SEARCH_FLOW_STEPS.length}`;
  controls.append(playButton, status);

  const timeline = document.createElement("div");
  timeline.className = "search-flow-timeline";
  SEARCH_FLOW_STEPS.forEach(([title, body], index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `search-scene-tab ${index === step ? "active" : ""} ${index < step ? "done" : ""}`;
    button.innerHTML = `<strong>${title}</strong><span>${body}</span>`;
    button.addEventListener("click", () => {
      stopSearchFlowAutoplay();
      state.searchFlowStep = index;
      renderVisualization();
    });
    timeline.appendChild(button);
  });

  const board = document.createElement("div");
  board.className = `search-flow-board scene-${step}`;
  board.appendChild(searchBoardTitle(step));
  board.appendChild(searchBoardCanvas(model, step));

  wrap.append(controls, timeline, board);
  return wrap;
}

function toggleSearchFlowAutoplay() {
  if (state.searchFlowPlaying) {
    stopSearchFlowAutoplay({ rerender: true });
  } else {
    startSearchFlowAutoplay();
  }
}

function startSearchFlowAutoplay() {
  clearSearchFlowTimer();
  state.searchFlowPlaying = true;
  if (state.searchFlowStep >= SEARCH_FLOW_STEPS.length - 1) state.searchFlowStep = 0;
  renderVisualization();
  scheduleSearchFlowAdvance();
}

function scheduleSearchFlowAdvance() {
  clearSearchFlowTimer();
  searchFlowTimer = window.setTimeout(() => {
    if (!state.searchFlowPlaying) return;
    if (state.searchFlowStep < SEARCH_FLOW_STEPS.length - 1) {
      state.searchFlowStep += 1;
      renderVisualization();
      scheduleSearchFlowAdvance();
    } else {
      stopSearchFlowAutoplay({ rerender: true });
    }
  }, SEARCH_FLOW_AUTOPLAY_DELAY);
}

function stopSearchFlowAutoplay({ rerender = false } = {}) {
  const wasPlaying = state.searchFlowPlaying;
  clearSearchFlowTimer();
  state.searchFlowPlaying = false;
  if (rerender && wasPlaying) renderVisualization();
}

function clearSearchFlowTimer() {
  if (!searchFlowTimer) return;
  window.clearTimeout(searchFlowTimer);
  searchFlowTimer = null;
}

function searchBoardTitle(step) {
  const [title, body] = SEARCH_FLOW_STEPS[step];
  const header = document.createElement("div");
  header.className = "search-board-title";
  header.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(body)}</span>`;
  return header;
}

function searchBoardCanvas(model, step) {
  const canvas = document.createElement("div");
  canvas.className = "search-board-canvas progressive-search-canvas";
  canvas.appendChild(searchFlowEdges(model, step));
  canvas.appendChild(searchFlowNode({
    id: "query",
    type: "query",
    label: "Query",
    title: model.query || "(未提供 query)",
    subtitle: "用户输入 / 工具参数",
    x: 4,
    y: 42,
  }, step, model));
  model.searchNodes.forEach((node) => canvas.appendChild(searchFlowNode(node, step, model)));
  if (step >= 2) {
    model.graphNodes.forEach((node) => canvas.appendChild(searchFlowNode(node, step, model)));
  }
  canvas.appendChild(searchFlowLegend(model, step));
  return canvas;
}

function queryTokens(query) {
  const tokens = String(query || "")
    .split(/\s+/)
    .map((token) => token.trim())
    .filter(Boolean);
  if (tokens.length >= 3) return tokens.slice(0, 5);
  return ["用户输入", "工具参数", "关键词", "URI"];
}

function extractSearchQuery(tool) {
  const input = tool.input && typeof tool.input === "object" ? tool.input : {};
  return input.query || input.pattern || input.target || "";
}

function buildSearchFlowModel(tool) {
  const query = extractSearchQuery(tool);
  const hits = parseSearchResults(tool);
  const hitUris = new Set(hits.map((item) => item.uri));
  const allObjects = normalizeContextObjects(state.run.contextObjects || []);
  const items = mergeContextObjects(allObjects, hits);
  const searchNodes = layoutSearchNodes(items, hitUris);
  const links = parseGraphLinksForSearch(hits);
  const graphNodes = layoutGraphNodes(links, searchNodes);
  return { query, items, hits, hitUris, searchNodes, links, graphNodes };
}

function layoutSearchNodes(items, hitUris) {
  const count = Math.max(items.length, 1);
  const columns = count > 8 ? 3 : count > 4 ? 2 : 1;
  const rows = Math.ceil(count / columns);
  return items.map((item, index) => ({
    ...item,
    id: item.uri,
    type: "context",
    isSearchHit: hitUris.has(item.uri),
    label: item.contextType || "memory",
    title: item.title || shortUri(item.uri),
    subtitle: item.overview || item.uri,
    x: 31 + (index % columns) * 15,
    y: 18 + Math.floor(index / columns) * Math.min(70 / Math.max(rows - 1, 1), 18),
  }));
}

function layoutGraphNodes(links, searchNodes) {
  const known = new Set(searchNodes.map((node) => node.id));
  const targetUris = unique(links.map((link) => link.target).filter((uri) => uri && !known.has(uri))).slice(0, 5);
  const ySlots = targetUris.length <= 3 ? [25, 47, 69] : [15, 31, 47, 63, 79];
  return targetUris.map((uri, index) => ({
    id: uri,
    uri,
    type: "graph",
    label: "neighbor",
    title: shortUri(uri),
    subtitle: graphRelationSummary(uri, links),
    score: graphConfidenceSummary(uri, links),
    x: 70,
    y: ySlots[index] ?? 47,
  }));
}

function searchFlowNode(node, step, model) {
  const div = document.createElement("div");
  const isQuery = node.type === "query";
  const isSearch = node.type === "context";
  const isGraph = node.type === "graph";
  const visible = isQuery || (isSearch && step >= 1) || (isGraph && step >= 2);
  const selected = step >= 3 && !isQuery && isSelectedSearchNode(node, model);
  const active = (step === 0 && isQuery) || (step === 1 && node.isSearchHit) || (step === 2 && isGraph) || selected;
  div.className = [
    "flow-node",
    `flow-${node.type}-node`,
    node.isSearchHit ? "search-hit" : "",
    visible ? "visible" : "",
    active ? "active" : "",
    selected ? "selected" : "",
  ].join(" ");
  div.style.setProperty("--x", `${node.x}%`);
  div.style.setProperty("--y", `${node.y}%`);
  div.innerHTML = `
    <span class="flow-node-label">${escapeHtml(node.label)}</span>
    <strong>${escapeHtml(node.title)}</strong>
    <span>${escapeHtml(node.subtitle || "")}</span>
    ${node.score ? `<em>${escapeHtml(node.score)}</em>` : ""}
  `;
  return div;
}

function searchFlowEdges(model, step) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", `flow-edges ${step >= 2 ? "visible" : ""}`);
  svg.setAttribute("viewBox", "0 0 100 100");
  svg.setAttribute("preserveAspectRatio", "none");
  if (step < 2) return svg;
  const nodesById = new Map([...model.searchNodes, ...model.graphNodes].map((node) => [node.id, node]));
  model.links.forEach((link) => {
    const from = nodesById.get(link.source);
    const to = nodesById.get(link.target);
    if (!from || !to) return;
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", String(from.x + 12));
    line.setAttribute("y1", String(from.y + 5));
    line.setAttribute("x2", String(to.x));
    line.setAttribute("y2", String(to.y + 5));
    line.setAttribute("class", `flow-link ${step === 2 ? "walking" : "selected"}`);
    svg.appendChild(line);
  });
  return svg;
}

function searchFlowLegend(model, step) {
  const div = document.createElement("div");
  div.className = "search-flow-legend";
  const graphNote = model.links.length
    ? `Graph Walk 扩展 ${model.graphNodes.length} 个邻近节点`
    : "未发现同会话 show_context_links 链接结果";
  div.innerHTML = `
    <span><i class="legend-dot query"></i>Query</span>
    <span><i class="legend-dot context"></i>ContextFS 全量：${model.searchNodes.length}</span>
    <span><i class="legend-dot search"></i>Search 命中：${model.hits.length}</span>
    <span><i class="legend-dot graph"></i>${escapeHtml(graphNote)}</span>
    <span><i class="legend-dot selected"></i>Step ${step + 1}</span>
  `;
  return div;
}

function isSelectedSearchNode(node, model) {
  if (node.type === "context") return model.hitUris.has(node.id);
  if (node.type === "graph") return model.links.some((link) => link.target === node.id);
  return false;
}

function layerStack(activeLayer) {
  const wrap = document.createElement("div");
  wrap.className = "layer-stack";
  [
    ["L0", "abstract", "一句话摘要，用于快速过滤和列表呈现。"],
    ["L1", "overview", "默认注入层，2-4 句解释为什么相关。"],
    ["L2", "full content", "完整正文，只有明确读取或证据展开时使用。"],
  ].forEach(([layer, name, body]) => {
    const item = document.createElement("div");
    item.className = `layer-box ${activeLayer === layer ? "active" : ""}`;
    item.innerHTML = `<strong>${layer} · ${name}</strong><span>${body}</span>`;
    wrap.appendChild(item);
  });
  return wrap;
}

function storageMap(tool) {
  const uri = tool.input?.uri || extractUri(tool.output) || inferMemoryUri(tool);
  const wrap = document.createElement("div");
  wrap.className = "storage-map";
  [
    ["URI", uri],
    ["index.jsonl", "ContextObject 元数据：title、abstract、overview、trust、status"],
    ["mem/.../*.md", "L2 正文文件，按 URI 路径落盘"],
    ["links.jsonl", "MemoryGraph 关系边：supports / related / derived_from"],
    ["diffs.jsonl", "写入与提交的审计记录"],
  ].forEach(([left, right]) => {
    const row = document.createElement("div");
    row.className = "storage-row";
    row.innerHTML = `<strong>${escapeHtml(left)}</strong><span>${escapeHtml(right)}</span>`;
    wrap.appendChild(row);
  });
  return wrap;
}

function selectionList(items) {
  const wrap = document.createElement("div");
  wrap.className = "selection-list";
  items.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "selection-row";
    row.innerHTML = `<strong>${index + 1}</strong><span>${escapeHtml(item.uri)}<br>${escapeHtml(item.reason)}</span><span class="score">${item.score}</span>`;
    wrap.appendChild(row);
  });
  return wrap;
}

function parseSearchResults(tool) {
  const text = typeof tool.output === "string" ? tool.output : JSON.stringify(tool.output || "");
  const blocks = text.split(/\n(?=-\s+(?:mem|ctx):\/\/)/g);
  const items = [];
  for (const block of blocks) {
    const uri = extractUri(block);
    if (!uri) continue;
    const trust = block.match(/trust=([^|\n]+)/)?.[1]?.trim() || "";
    const contextType = block.match(/type=([^|\n]+)/)?.[1]?.trim() || "";
    const lines = block.split(/\r?\n/).slice(1).join("\n").trim();
    const titleMatch = lines.match(/^([^:\n：]+)[:：]\s*([\s\S]*)$/);
    const title = titleMatch ? titleMatch[1].trim() : shortUri(uri);
    const overview = titleMatch ? titleMatch[2].trim() : lines;
    items.push({
      uri,
      title,
      overview,
      contextType,
      score: trust || "?",
      reason: overview || "search_context 返回的真实命中项",
    });
  }
  return items.slice(0, 5);
}

function normalizeContextObjects(objects) {
  return objects
    .filter((item) => item && item.uri)
    .map((item) => ({
      uri: item.uri,
      title: item.title || shortUri(item.uri),
      overview: item.overview || item.abstract || "",
      contextType: item.context_type || item.contextType || "context",
      score: item.trust_score == null ? "?" : String(item.trust_score),
      reason: item.overview || item.abstract || "ContextFS index object",
    }));
}

function mergeContextObjects(objects, hits) {
  const byUri = new Map();
  objects.forEach((item) => byUri.set(item.uri, item));
  hits.forEach((hit) => byUri.set(hit.uri, { ...(byUri.get(hit.uri) || {}), ...hit }));
  return [...byUri.values()];
}

function parseGraphLinksForSearch(items) {
  const searchUris = new Set(items.map((item) => item.uri));
  if (!searchUris.size) return [];
  const links = [];
  allTools()
    .filter((tool) => isGraphTool(tool))
    .forEach((tool) => {
      const { source, links: parsedLinks } = parseGraphLinks(tool);
      if (!searchUris.has(source)) return;
      links.push(...parsedLinks);
    });
  return uniqueBy(links, (link) => `${link.source}|${link.relation}|${link.target}`);
}

function parseGraphLinks(tool) {
  const output = String(tool.output || "");
  const source = tool.input?.uri || output.match(/^Links for ((?:mem|ctx):\/\/[^:]+):/m)?.[1] || "";
  const links = [];
  for (const line of output.split(/\r?\n/)) {
    const match = line.match(/(?:-\s*)?([^\s]+)\s*->\s*((?:mem|ctx):\/\/[^\s()]+)(?:\s*\(confidence=([^)]+)\))?/);
    if (!match) continue;
    links.push({
      source,
      relation: match[1],
      target: match[2],
      confidence: match[3] || "?",
    });
  }
  return { source, links };
}

function contextObjectByUri(uri) {
  if (!uri) return null;
  return normalizeContextObjects(state.run.contextObjects || []).find((item) => item.uri === uri) || null;
}

function graphRelationSummary(uri, links) {
  const relations = unique(links.filter((link) => link.target === uri).map((link) => link.relation));
  return relations.length ? relations.join(" / ") : "linked neighbor";
}

function graphConfidenceSummary(uri, links) {
  const confidence = links.find((link) => link.target === uri)?.confidence;
  return confidence ? `conf ${confidence}` : "";
}

function searchEvidencePayload(tool, model) {
  return {
    input: tool.input,
    searchOutput: tool.output,
    contextFsObjects: model.items.map((item) => ({
      uri: item.uri,
      title: item.title,
      trust: item.score,
      type: item.contextType,
      searchHit: model.hitUris.has(item.uri),
    })),
    parsedSearchHits: model.hits.map((item) => ({
      uri: item.uri,
      title: item.title,
      trust: item.score,
      type: item.contextType,
    })),
    graphLinksFromShowContextLinks: model.links,
  };
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function uniqueBy(values, keyFn) {
  const seen = new Set();
  return values.filter((value) => {
    const key = keyFn(value);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function rawBlock(value) {
  const pre = document.createElement("pre");
  pre.className = "raw-block";
  pre.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return pre;
}

function emptyState(text) {
  const div = document.createElement("div");
  div.className = "empty-state";
  div.textContent = text;
  return div;
}

function isGraphTool(tool) {
  return /graph|link|neighbor|show_context_links/.test(tool.name);
}

function isSearchTool(tool) {
  return /search|list_context/.test(tool.name);
}

function isReadTool(tool) {
  return /read_context|context/.test(tool.name) && !isSearchTool(tool) && !isGraphTool(tool);
}

function isRememberTool(tool) {
  return /remember|commit|compact/.test(tool.name);
}

function toolKind(tool) {
  if (isGraphTool(tool) || isRememberTool(tool)) return "memory";
  if (isSearchTool(tool)) return "search";
  if (isReadTool(tool)) return "read";
  if (/write|edit|run_command/.test(tool.name)) return "write";
  return "generic";
}

function toolKindLabel(tool) {
  if (isGraphTool(tool)) return "MemoryGraph";
  if (isSearchTool(tool)) return "Context Search";
  if (isReadTool(tool)) return "L0/L1/L2";
  if (isRememberTool(tool)) return "Memory Commit";
  return "Tool Result";
}

function extractUri(text) {
  const match = String(text || "").match(/(?:mem|ctx):\/\/[^\s,'"）)]+/);
  return match ? match[0] : "";
}

function inferMemoryUri(tool) {
  const category = tool.input?.category || "events";
  const title = slug(tool.input?.title || tool.input?.query || tool.name || "memory");
  if (category === "profile") return "mem://user/profile";
  if (["preferences", "entities", "events"].includes(category)) return `mem://user/${category}/${title}`;
  return `mem://project/${category}/${title}`;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function shortId(value) {
  return String(value || "").slice(0, 8);
}

function shortUri(value) {
  const text = String(value || "");
  if (text.length <= 42) return text;
  return `${text.slice(0, 18)}...${text.slice(-18)}`;
}

function compactText(value, maxLength) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= maxLength) return text || "(empty)";
  return `${text.slice(0, maxLength - 1)}…`;
}

function slug(value) {
  return String(value || "memory")
    .trim()
    .toLowerCase()
    .replace(/[^\w\u4e00-\u9fff\s-]/g, "")
    .replace(/\s+/g, "-")
    .slice(0, 80);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return String(value ?? "").replace(/[^a-zA-Z0-9_-]/g, "_");
}

function showToast(message) {
  nodes.toast.textContent = message;
  nodes.toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    nodes.toast.hidden = true;
  }, 2600);
}

nodes.modeTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    state.leftMode = tab.dataset.leftMode;
    render();
  });
});

nodes.sideNavTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    state.navMode = tab.dataset.navMode;
    render();
  });
});

nodes.newSessionButton.addEventListener("click", createNewSession);

nodes.composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = nodes.composerInput.value.trim();
  if (!message || state.sending) return;

  if (!state.apiMode) {
    state.run.messages.push({ id: crypto.randomUUID(), role: "user", time: "queued", text: message, tools: [] });
    nodes.composerInput.value = "";
    render();
    showToast("演示模式已加入对话；启动 Web API 后会发送到 /api/chat。");
    return;
  }

  state.sending = true;
  nodes.composerInput.disabled = true;
  nodes.sendButton.disabled = true;
  nodes.sendButton.textContent = "发送中";
  let streamCompleted = false;
  try {
    nodes.composerInput.value = "";
    await sendChatStream(message);
    streamCompleted = true;
    state.sending = false;
    nodes.composerInput.disabled = false;
    nodes.sendButton.disabled = false;
    nodes.sendButton.textContent = "发送";
    render();
    loadData().catch((error) => showToast(`会话刷新失败：${error.message}`));
  } catch (error) {
    showToast(`发送失败：${error.message}`);
  } finally {
    if (!streamCompleted) {
      state.sending = false;
      nodes.composerInput.disabled = false;
      nodes.sendButton.disabled = false;
      nodes.sendButton.textContent = "发送";
      render();
    }
  }
});

render();
loadData();
