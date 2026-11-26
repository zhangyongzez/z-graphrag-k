/* ------------------------------------------------------------------ 工具 */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), kind === "err" ? 5000 : 2600);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 401) {
    showLogin();
    throw new Error("未登录");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function modal({ title, body, okText = "确定", cancelText = "取消", onOk, onCancel, danger = false }) {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal">
      <h3>${esc(title)}</h3>
      <div id="modal-body">${body}</div>
      <div class="modal-actions">
        <button class="btn" id="modal-cancel">${esc(cancelText)}</button>
        <button class="btn ${danger ? "btn-danger" : "btn-primary"}" id="modal-ok">${esc(okText)}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);
  const close = () => mask.remove();
  const cancel = () => {
    onCancel?.();
    close();
  };
  $("#modal-cancel", mask).onclick = cancel;
  mask.onclick = (e) => { if (e.target === mask) cancel(); };
  $("#modal-ok", mask).onclick = async () => {
    try {
      const keep = await onOk(mask);
      if (keep !== false) close();
    } catch (e) {
      toast(e.message, "err");
    }
  };
  return mask;
}

/* ------------------------------------------------------------------ 状态 */
const state = {
  user: null,
  page: "dashboard",
  kbs: [],
  currentKb: null,
  conversations: [],
  currentConv: null,
  chatHistory: [],
  models: [],
  modelKind: "all",
  configSchema: null,
  configDefaults: {},
  adminDash: null,
  configTab: "embedding",
};

function isAdmin() { return state.user?.role === "admin"; }

/* ------------------------------------------------------------------ 登录 */
function showLogin() {
  $("#app-view").classList.add("hidden");
  $("#login-view").classList.remove("hidden");
}

async function boot() {
  try {
    state.user = await api("/api/auth/me");
    enterApp();
  } catch {
    showLogin();
    try {
      const m = await api("/api/auth/auth-mode");
      if (m.ad_enabled) {
        const hint = $("#login-ad-hint");
        hint.style.display = "block";
        hint.textContent = `已启用 AD 域登录（${m.ad_server}），可直接使用域账号登录`;
      }
    } catch { /* ignore */ }
  }
}

function enterApp() {
  $("#login-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  $("#user-name").textContent =
    `${state.user.display_name || state.user.username}${isAdmin() ? " · 管理员" : ""}`;
  $$(".admin-only").forEach((el) => el.classList.toggle("hidden", !isAdmin()));
  show("dashboard");
  loadHealth();
  maybeShowSetupGuide();
}

/* 首次使用引导：管理员登录且模型未配置时弹出 */
async function maybeShowSetupGuide() {
  if (!isAdmin()) return;
  if (localStorage.getItem("kgrag_setup_dismissed") === "1") return;
  try {
    const h = await api("/health");
    if (h.embedding_ready && h.llm_ready) return;

    const missing = [];
    if (!h.embedding_ready) missing.push("Embedding 模型（用于文档检索）");
    if (!h.llm_ready) missing.push("大模型（用于生成回答）");

    const mask = modal({
      title: "开始使用",
      body: `
        <div class="muted" style="margin-bottom:16px;line-height:1.8">
          系统已就绪，但还需要配置模型才能使用完整功能。
        </div>
        <div class="card" style="background:var(--bg);margin-bottom:14px">
          <div style="margin-bottom:10px"><strong>还缺少：</strong></div>
          ${missing.map((m) => `<div style="padding:4px 0">· ${esc(m)}</div>`).join("")}
        </div>
        <div class="muted" style="font-size:13px;line-height:1.9">
          <strong style="color:var(--text)">配置步骤</strong><br>
          1. 进入「模型管理」，点「+ 新增模型」<br>
          2. 选择类型和提供方，填入地址 / 密钥 / 路径<br>
          3. 到「系统配置」下拉选中刚加的模型<br>
          <br>
          <span style="color:var(--amber)">
            未配置大模型时，对话会降级为「纯检索模式」——直接返回匹配的原文片段。
          </span>
        </div>
        <label class="row" style="margin-top:16px;gap:8px;cursor:pointer">
          <input type="checkbox" id="setup-skip">
          <span class="muted" style="font-size:13px">不再提示</span>
        </label>`,
      okText: "去配置",
      cancelText: "稍后",
      onOk: () => {
        if ($("#setup-skip")?.checked) {
          localStorage.setItem("kgrag_setup_dismissed", "1");
        }
        show("models");
      },
      onCancel: () => {
        if ($("#setup-skip")?.checked) {
          localStorage.setItem("kgrag_setup_dismissed", "1");
        }
      },
    });
    void mask;
  } catch { /* 忽略 */ }
}

async function loadHealth() {
  try {
    const h = await api("/health");
    // 未在「模型管理」登记时明确提示，避免让人误以为已就绪
    const emb = h.embedding_ready
      ? `Embedding · ${esc(h.embedding)}`
      : `<span style="color:var(--amber)">Embedding 未配置</span>`;
    const llm = h.llm_ready
      ? `LLM · ${esc(h.llm_model || h.llm)}`
      : `<span style="color:var(--amber)">LLM 未配置</span>`;
    $("#sys-status").innerHTML =
      `${esc(h.vectorstore)}<br>${emb}<br>${llm}`;
  } catch {
    $("#sys-status").textContent = "服务不可用";
  }
}

$("#btn-login").addEventListener("click", doLogin);
$("#login-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });

async function doLogin() {
  const username = $("#login-user").value.trim();
  const password = $("#login-pass").value;
  if (!username || !password) return toast("请输入用户名和密码", "err");
  try {
    const r = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    state.user = r.user;
    $("#login-pass").value = "";
    $("#login-error").textContent = "";
    enterApp();
  } catch (e) {
    $("#login-error").textContent = e.message;
  }
}

$("#btn-logout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  state.user = null;
  showLogin();
});

/* ------------------------------------------------------------------ 路由 */
const PAGE_LOADERS = {
  dashboard: loadDashboard,
  kb: loadKbList,
  chat: loadChat,
  config: loadConfig,
  models: loadModels,
  "admin-dash": loadAdminDash,
  users: loadUsers,
};

function show(page) {
  state.page = page;
  $$(".page").forEach((p) => p.classList.add("hidden"));
  const el = $(`#page-${page}`);
  if (el) el.classList.remove("hidden");
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  PAGE_LOADERS[page]?.();
}

$$(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => show(btn.dataset.page));
});

/* ------------------------------------------------------------------ 看板 */
async function loadDashboard() {
  try {
    const s = await api("/api/kb/stats/dashboard");
    $("#stats").innerHTML = [
      ["知识库", s.kb_count],
      ["文档", s.total_documents],
      ["向量块", s.total_chunks],
      ["Embedding", `${s.embedding_provider} · ${s.embedding_dim}维`],
      ["向量库", s.vectorstore_provider],
      ["LLM", `${s.llm_provider} · ${s.llm_model || "-"}`],
    ].map(([label, value], i) => `
      <div class="stat">
        <div class="label">${label}</div>
        <div class="value ${i >= 3 ? "sm" : ""}">${esc(value)}</div>
      </div>`).join("");

    const kbs = await api("/api/kb");
    state.kbs = kbs;
    $("#dash-kb-list").innerHTML = kbs.length
      ? kbs.map((kb) => `
        <div class="row" style="padding:10px 0;border-bottom:1px solid var(--border)">
          <div class="grow">
            <div>${esc(kb.name)} ${kb.is_public ? '<span class="badge">公共</span>' : ""}
              ${kb.is_owner ? '<span class="badge ok">我的</span>' : ""}</div>
            <div class="muted">${esc(kb.description || "无描述")}</div>
          </div>
          <span class="badge">${kb.document_count} 文档</span>
          <span class="badge">${kb.chunk_count} 块</span>
        </div>`).join("")
      : `<div class="empty">还没有知识库，去「知识库」页创建一个</div>`;
  } catch (e) {
    toast(`加载失败：${e.message}`, "err");
  }
}

$("#btn-refresh").addEventListener("click", loadDashboard);

/* ------------------------------------------------------------------ 知识库列表 */
async function loadKbList() {
  try {
    state.kbs = await api("/api/kb");
    const el = $("#kb-list");
    if (!state.kbs.length) {
      el.innerHTML = `<div class="empty"><div class="big">▤</div>还没有知识库</div>`;
      return;
    }
    el.innerHTML = `<div class="card"><table>
      <thead><tr>
        <th>名称</th><th>描述</th><th>归属</th><th>文档</th><th>向量块</th><th>创建时间</th><th></th>
      </tr></thead>
      <tbody>${state.kbs.map((kb) => `
        <tr>
          <td><strong>${esc(kb.name)}</strong>
            ${kb.is_public ? '<span class="badge">公共</span>' : ""}</td>
          <td class="muted">${esc(kb.description || "-")}</td>
          <td class="muted">${esc(kb.owner_name || "—")}</td>
          <td>${kb.document_count}</td>
          <td>${kb.chunk_count}</td>
          <td class="muted mono">${esc(kb.created_at.slice(0, 10))}</td>
          <td class="actions">
            <button class="btn btn-sm" data-open="${kb.id}">管理</button>
          </td>
        </tr>`).join("")}
      </tbody></table></div>`;
    $$("[data-open]").forEach((b) => {
      b.addEventListener("click", () => openKb(b.dataset.open));
    });
  } catch (e) {
    toast(`加载失败：${e.message}`, "err");
  }
}

/* ------------------------------------------------------------------ 新建知识库 */
$("#btn-create-kb").addEventListener("click", () => {
  modal({
    title: "新建知识库",
    body: `
      <label class="field"><span>名称</span>
        <input class="input" id="m-name" placeholder="例如：产品手册"></label>
      <label class="field"><span>描述</span>
        <textarea class="textarea" id="m-desc" placeholder="可选"></textarea></label>`,
    okText: "创建",
    onOk: async () => {
      const name = $("#m-name").value.trim();
      if (!name) throw new Error("名称不能为空");
      await api("/api/kb", {
        method: "POST",
        body: JSON.stringify({ name, description: $("#m-desc").value.trim() }),
      });
      toast("创建成功", "ok");
      loadKbList();
    },
  });
  setTimeout(() => $("#m-name")?.focus(), 0);
});

/* ------------------------------------------------------------------ 知识库详情 */
async function openKb(id) {
  try {
    const kb = await api(`/api/kb/${id}`);
    state.currentKb = kb;
    $("#kb-detail-name").textContent = kb.name;
    $("#kb-detail-desc").textContent = kb.description || "无描述";
    $("#btn-delete-kb").style.display = (kb.is_owner || isAdmin()) ? "" : "none";
    $("#dropzone").style.display = kb.can_write || isAdmin() ? "" : "none";
    show("kb-detail");
    await loadKbDocs();
  } catch (e) {
    toast(e.message, "err");
  }
}

async function loadKbDocs() {
  const kb = state.currentKb;
  if (!kb) return;
  try {
    const data = await api(`/api/kb/${kb.id}/documents`);
    const el = $("#kb-docs");
    if (!data.documents.length) {
      el.innerHTML = `<div class="empty">还没有文档，上传一些吧</div>`;
      return;
    }
    el.innerHTML = `<table>
      <thead><tr><th>文件名</th><th>向量块</th><th></th></tr></thead>
      <tbody>${data.documents.map((d) => `
        <tr>
          <td>${esc(d.filename)}</td>
          <td>${d.chunks}</td>
          <td class="actions">
            <button class="btn btn-sm btn-danger" data-del="${esc(d.filename)}">删除</button>
          </td>
        </tr>`).join("")}
      </tbody></table>
      <div class="muted" style="margin-top:12px">共 ${data.row_count} 个向量块</div>`;
    $$("[data-del]").forEach((b) => {
      b.addEventListener("click", async () => {
        if (!confirm(`确定删除「${b.dataset.del}」？`)) return;
        try {
          await api(`/api/kb/${kb.id}/documents/${encodeURIComponent(b.dataset.del)}`,
            { method: "DELETE" });
          toast("已删除", "ok");
          loadKbDocs();
        } catch (e) { toast(e.message, "err"); }
      });
    });
  } catch (e) {
    toast(e.message, "err");
  }
}

$("#btn-back-kb").addEventListener("click", () => show("kb"));

$("#btn-delete-kb").addEventListener("click", async () => {
  const kb = state.currentKb;
  if (!kb) return;
  if (!confirm(`删除知识库「${kb.name}」及其所有数据？此操作不可恢复。`)) return;
  try {
    await api(`/api/kb/${kb.id}`, { method: "DELETE" });
    toast("已删除", "ok");
    show("kb");
  } catch (e) { toast(e.message, "err"); }
});

/* ------------------------------------------------------------------ 上传 */
const dropzone = $("#dropzone");
const fileInput = $("#file-input");

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("over");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("over"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("over");
  uploadFiles([...e.dataTransfer.files]);
});
fileInput.addEventListener("change", () => {
  uploadFiles([...fileInput.files]);
  fileInput.value = "";
});

async function uploadFiles(files) {
  if (!files.length || !state.currentKb) return;
  const prog = $("#upload-progress");
  prog.innerHTML = files.map((f) =>
    `<div class="row" style="padding:6px 0"><span class="grow">${esc(f.name)}</span>
     <span class="muted" data-s="${esc(f.name)}">上传中…</span></div>`).join("");

  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));

  try {
    const res = await api(`/api/kb/${state.currentKb.id}/upload`, {
      method: "POST",
      body: fd,
    });
    res.results.forEach((r) => {
      const el = prog.querySelector(`[data-s="${CSS.escape(r.file)}"]`);
      if (el) {
        el.textContent = r.ok ? `✓ ${r.chunks} 块` : `✗ ${r.error}`;
        el.className = r.ok ? "badge ok" : "badge err";
      }
    });
    toast(`完成：成功 ${res.uploaded}，失败 ${res.failed}`,
      res.failed ? "err" : "ok");
    setTimeout(() => { prog.innerHTML = ""; }, 3000);
    loadKbDocs();
  } catch (e) {
    toast(`上传失败：${e.message}`, "err");
    prog.innerHTML = "";
  }
}

/* ------------------------------------------------------------------ 对话 */
async function loadChat() {
  await Promise.all([loadConversations(), loadChatKbs(), loadModelOptions()]);
  if (state.conversations.length && !state.currentConv) {
    openConversation(state.conversations[0].id);
  }
}

async function loadModelOptions() {
  try {
    const h = await api("/health");
    const models = await api("/api/models?kind=llm").catch(() => []);
    const sel = $("#chat-model");
    const current = h.llm_model || "-";
    sel.innerHTML = `<option value="">跟随系统配置（${esc(current)}）</option>` +
      models.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}（${esc(m.provider)}）</option>`).join("");
  } catch { /* ignore */ }
}

async function loadConversations() {
  try {
    state.conversations = await api("/api/chat/conversations");
    renderConvList();
  } catch (e) {
    toast(e.message, "err");
  }
}

function renderConvList() {
  const el = $("#conv-list");
  if (!state.conversations.length) {
    el.innerHTML = `<div class="muted" style="padding:12px;text-align:center">暂无对话</div>`;
    return;
  }
  el.innerHTML = state.conversations.map((c) => `
    <div class="list-item ${state.currentConv?.id === c.id ? "active" : ""}" data-conv="${c.id}">
      <span class="grow" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        ${esc(c.title)}</span>
      <button class="btn btn-sm" data-del-conv="${c.id}" title="删除">×</button>
    </div>`).join("");
  $$("[data-conv]").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.target.closest("[data-del-conv]")) return;
      openConversation(el.dataset.conv);
    });
  });
  $$("[data-del-conv]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("删除这个对话？")) return;
      await api(`/api/chat/conversations/${btn.dataset.delConv}`, { method: "DELETE" });
      if (state.currentConv?.id === btn.dataset.delConv) state.currentConv = null;
      loadConversations();
      if (!state.currentConv) $("#chat-log").innerHTML = emptyChat();
    });
  });
}

function emptyChat() {
  return `<div class="empty"><div class="big">◐</div>开始新的对话</div>`;
}

async function openConversation(id) {
  try {
    const c = await api(`/api/chat/conversations/${id}`);
    state.currentConv = c;
    state.chatHistory = c.messages
      .filter((m) => m.role !== "system")
      .map((m) => [m.role === "user" ? "human" : "ai", m.content]);
    $("#chat-title").textContent = c.title || "对话";
    $("#chat-sub").textContent =
      c.kb_weights.length ? `关联 ${c.kb_weights.length} 个知识库` : "未选择知识库";
    $("#chat-model").value = c.llm_model || "";
    renderConvList();
    renderMessages(c.messages);
    renderKbWeights(c.kb_weights);
  } catch (e) {
    toast(e.message, "err");
  }
}

function renderMessages(messages) {
  const log = $("#chat-log");
  if (!messages.length) { log.innerHTML = emptyChat(); return; }
  log.innerHTML = "";
  messages.forEach((m) => {
    if (m.role === "user") appendMsg("user", m.content);
    else appendMsg("assistant", m.content, m.sources);
  });
  log.scrollTop = log.scrollHeight;
}

function renderKbWeights(weights) {
  const el = $("#chat-kb-weights");
  if (!state.kbs.length) {
    el.innerHTML = `<div class="muted" style="font-size:12px;padding:6px 0">暂无知识库</div>`;
    return;
  }
  const map = new Map(weights.map((w) => [w.kb_id, w.weight]));
  el.innerHTML = state.kbs.map((kb) => `
    <div class="kb-weight">
      <input type="checkbox" data-w-kb="${kb.id}" ${map.has(kb.id) ? "checked" : ""}>
      <span class="grow" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        ${esc(kb.name)}</span>
      <input type="range" min="0" max="2" step="0.1" value="${map.get(kb.id) ?? 1}"
        data-w-range="${kb.id}" ${map.has(kb.id) ? "" : "disabled"}>
      <span class="w" data-w-val="${kb.id}">${(map.get(kb.id) ?? 1).toFixed(1)}</span>
    </div>`).join("");
  $$("[data-w-kb]").forEach((cb) => {
    cb.addEventListener("change", () => {
      const range = $(`[data-w-range="${cb.dataset.wKb}"]`);
      range.disabled = !cb.checked;
      saveWeights();
    });
  });
  $$("[data-w-range]").forEach((range) => {
    range.addEventListener("input", () => {
      $(`[data-w-val="${range.dataset.wRange}"]`).textContent = Number(range.value).toFixed(1);
    });
    range.addEventListener("change", saveWeights);
  });
}

async function saveWeights() {
  if (!state.currentConv) return;
  const weights = [];
  $$("[data-w-kb]").forEach((cb) => {
    if (!cb.checked) return;
    const range = $(`[data-w-range="${cb.dataset.wKb}"]`);
    weights.push({ kb_id: cb.dataset.wKb, weight: Number(range.value) });
  });
  await api(`/api/chat/conversations/${state.currentConv.id}`, {
    method: "PATCH",
    body: JSON.stringify({ kb_weights: weights }),
  }).catch(() => {});
  state.currentConv.kb_weights = weights;
  $("#chat-sub").textContent = weights.length ? `关联 ${weights.length} 个知识库` : "未选择知识库";
}

$("#btn-new-conv").addEventListener("click", async () => {
  try {
    const c = await api("/api/chat/conversations", {
      method: "POST",
      body: JSON.stringify({ title: "新对话" }),
    });
    state.conversations.unshift(c);
    await openConversation(c.id);
  } catch (e) {
    toast(e.message, "err");
  }
});

$("#btn-clear-chat").addEventListener("click", () => {
  $("#chat-log").innerHTML = emptyChat();
  state.chatHistory = [];
});

const chatInput = $("#chat-input");
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
});
$("#btn-send").addEventListener("click", sendChat);

function appendMsg(role, text, sources) {
  const log = $("#chat-log");
  const empty = log.querySelector(".empty");
  if (empty) empty.remove();

  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `
    <div class="avatar">${role === "user" ? "我" : "AI"}</div>
    <div class="bubble"></div>`;
  div.querySelector(".bubble").textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;

  if (sources && sources.length) {
    const det = document.createElement("details");
    det.className = "sources";
    det.innerHTML = `<summary>引用 ${sources.length} 条资料</summary><ul>${
      sources.map((s) => `<li>${esc(s.filename || "?")} — ${esc((s.preview || "").slice(0, 70))}</li>`).join("")
    }</ul>`;
    div.querySelector(".bubble").appendChild(det);
  }
  return div;
}

async function sendChat() {
  const q = chatInput.value.trim();
  if (!q) return;
  if (!state.currentConv) {
    try {
      const c = await api("/api/chat/conversations", {
        method: "POST",
        body: JSON.stringify({ title: q.slice(0, 20) }),
      });
      state.conversations.unshift(c);
      await openConversation(c.id);
    } catch (e) { return toast(e.message, "err"); }
  }
  chatInput.value = "";
  appendMsg("user", q);

  const bubble = appendMsg("assistant", "");
  bubble.querySelector(".avatar").textContent = "…";
  bubble.querySelector(".bubble").textContent = "";

  const weights = state.currentConv?.kb_weights || [];
  const model = $("#chat-model").value || "";

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: q,
        kb_weights: weights,
        history: state.chatHistory.slice(-10).map(([role, content]) => ({ role, content })),
        conversation_id: state.currentConv.id,
        llm_model: model,
      }),
    });
    if (res.status === 401) return showLogin();
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let answer = "";
    let sources = [];

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const part of parts) {
        const evLine = part.split("\n").find((l) => l.startsWith("event:"));
        const dataLine = part.split("\n").find((l) => l.startsWith("data:"));
        if (!evLine || !dataLine) continue;
        const ev = evLine.slice(6).trim();
        const data = JSON.parse(dataLine.slice(5).trim());

        if (ev === "sources") {
          sources = data.sources || [];
        } else if (ev === "retrieval_only") {
          // 未配置大模型，回答来自原文片段
          bubble.dataset.retrievalOnly = "1";
          bubble.querySelector(".avatar").textContent = "检";
        } else if (ev === "delta") {
          answer += data.text;
          bubble.querySelector(".bubble").textContent = answer;
          $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
        } else if (ev === "error") {
          throw new Error(data.message);
        }
      }
    }

    bubble.querySelector(".avatar").textContent =
      bubble.dataset.retrievalOnly ? "检" : "AI";
    if (sources.length) {
      const det = document.createElement("details");
      det.className = "sources";
      det.innerHTML = `<summary>引用 ${sources.length} 条资料</summary><ul>${
        sources.map((s) => `<li>${esc(s.filename || "?")} — ${esc((s.preview || "").slice(0, 70))}</li>`).join("")
      }</ul>`;
      bubble.querySelector(".bubble").appendChild(det);
    }
    state.chatHistory.push(["human", q], ["ai", answer]);
    loadConversations();
  } catch (e) {
    bubble.querySelector(".avatar").textContent = "!";
    bubble.querySelector(".bubble").textContent = `出错了：${e.message}`;
    toast(e.message, "err");
  }
}

/* ------------------------------------------------------------------ 系统配置 */
async function loadConfig() {
  try {
    const data = await api("/api/admin/config");
    state.configSchema = data.groups;
    state.configDefaults = data.defaults || {};
    if (!state.configSchema.some((g) => g.key === state.configTab)) {
      state.configTab = state.configSchema[0]?.key;
    }
    renderConfigTabs();
    renderConfigTab();
  } catch (e) {
    toast(e.message, "err");
  }
}

function renderConfigTabs() {
  $("#config-tabs").innerHTML = state.configSchema.map((g) => `
    <button class="tab ${g.key === state.configTab ? "active" : ""}" data-tab="${g.key}">
      ${esc(g.title)}${g.restart ? ' <span class="muted">↻</span>' : ""}</button>`).join("");
  $$("[data-tab]").forEach((b) => {
    b.addEventListener("click", () => { state.configTab = b.dataset.tab; renderConfigTab(); });
  });
}

/* 配置字段控件：model_select 渲染为下拉，选项来自「模型管理」 */
function fieldInput(f) {
  const val = f.value;
  if (f.type === "model_select") {
    const opts = f.options || [];
    if (!opts.length) {
      const what = f.model_kind === "llm" ? "大" : "Embedding";
      return `<div class="muted" style="padding:8px 0;font-size:13px">
        还没有可选的${what}模型，请先到「
        <a href="#" data-goto-models style="color:var(--accent)">模型管理</a>」新增</div>`;
    }
    return `<select class="select" id="cfg-${f.key}">
      <option value="">（未选择，使用 .env 默认）</option>
      ${opts.map((o) => `<option value="${esc(o.id)}" ${o.id === val ? "selected" : ""}>
        ${esc(o.name)}${o.model ? ` · ${esc(o.model)}` : ""}（${esc(o.provider)}）</option>`).join("")}
    </select>`;
  }
  if (f.type === "bool") {
    return `<div class="switch">
      <input type="checkbox" id="cfg-${f.key}" ${val ? "checked" : ""}>
      <span class="muted">${val ? "已启用" : "已停用"}</span></div>`;
  }
  if (f.type === "select") {
    return `<select class="select" id="cfg-${f.key}">${
      f.options.map((o) => `<option value="${esc(o)}" ${o === val ? "selected" : ""}>${esc(o)}</option>`).join("")
    }</select>`;
  }
  if (f.type === "password") {
    const isSet = val && val.is_set;
    return `<input class="input" type="password" id="cfg-${f.key}"
      placeholder="${isSet ? "已设置（留空不修改）" : "未设置"}"
      value="${isSet ? "" : esc(val?.value ?? "")}">`;
  }
  if (f.type === "number") {
    return `<input class="input" type="number" step="${f.step || "any"}" id="cfg-${f.key}"
      value="${val ?? ""}">`;
  }
  return `<input class="input" id="cfg-${f.key}" value="${esc(val ?? "")}"
    placeholder="${esc(f.placeholder || "")}">`;
}

function renderConfigTab() {
  const g = state.configSchema.find((x) => x.key === state.configTab);
  if (!g) return;
  renderConfigTabs();
  const isDefault = (f) => JSON.stringify(f.value) === JSON.stringify(f.default);
  $("#config-body").innerHTML = `
    <div class="row" style="margin-bottom:14px;gap:10px;flex-wrap:wrap">
      ${g.restart
        ? '<span class="badge err">修改后需重启服务</span>'
        : '<span class="badge ok">修改后即时生效</span>'}
      ${g.description ? `<span class="muted">${esc(g.description)}</span>` : ""}
    </div>
    <div class="form-grid">
      ${g.fields.map((f) => `
        <label class="field" ${f.type === "model_select" ? 'style="grid-column:1/-1"' : ""}>
          <span>${esc(f.label)}
            ${!isDefault(f) ? '<span class="badge" style="margin-left:6px">已修改</span>' : ""}
          </span>
          ${fieldInput(f)}
          ${f.help ? `<div class="field-hint">${esc(f.help)}</div>` : ""}
        </label>`).join("")}
    </div>`;
  $$("[data-goto-models]").forEach((a) => {
    a.addEventListener("click", (e) => { e.preventDefault(); show("models"); });
  });
}

$("#btn-save-config").addEventListener("click", async () => {
  const g = state.configSchema.find((x) => x.key === state.configTab);
  if (!g) return;
  const values = {};
  for (const f of g.fields) {
    const el = $(`#cfg-${f.key}`);
    if (!el) continue;
    if (f.type === "bool") values[f.key] = el.checked;
    else if (f.type === "number") values[f.key] = el.value === "" ? null : Number(el.value);
    else if (f.type === "password") {
      if (el.value) values[f.key] = el.value;
    } else values[f.key] = el.value;
  }
  try {
    const r = await api("/api/admin/config", {
      method: "PUT",
      body: JSON.stringify({ values }),
    });
    toast(r.changed.length
      ? `已保存 ${r.changed.length} 项${r.restart_required ? "，需重启服务生效" : ""}`
      : "没有变化", r.restart_required ? "err" : "ok");
    loadConfig();
  } catch (e) {
    toast(e.message, "err");
  }
});

$("#btn-reset-config").addEventListener("click", async () => {
  if (!confirm("恢复所有配置为默认值？当前自定义的配置会被清除。")) return;
  try {
    await api("/api/admin/config/reset", { method: "POST" });
    toast("已恢复默认配置", "ok");
    loadConfig();
  } catch (e) {
    toast(e.message, "err");
  }
});

/* ------------------------------------------------------------------ 模型管理 */
const PROVIDER_LABELS = {
  anthropic: "Anthropic（Claude）",
  openai: "OpenAI 兼容（含 DeepSeek / vLLM / 网关）",
  ollama: "Ollama 本地服务",
  local: "本地目录（sentence-transformers）",
  hash: "哈希占位（仅测试）",
  mock: "Mock（仅测试）",
};

const KIND_LABELS = { llm: "大模型", embedding: "Embedding" };

async function loadModels() {
  try {
    const kind = state.modelKind === "all" ? "" : state.modelKind;
    state.models = await api("/api/admin/models" + (kind ? `?kind=${kind}` : ""));
    const el = $("#model-list");
    if (!state.models.length) {
      el.innerHTML = `<div class="empty">
        <div class="big">◈</div>还没有模型，点击右上角「+ 新增模型」添加</div>`;
      return;
    }
    el.innerHTML = `<table>
      <thead><tr>
        <th>名称</th><th>类型</th><th>提供方</th><th>模型 / 路径</th>
        <th>地址</th><th>密钥</th><th>维度</th><th></th>
      </tr></thead>
      <tbody>${state.models.map((m) => `
        <tr>
          <td><strong>${esc(m.name)}</strong>
            ${m.note ? `<div class="muted" style="font-size:12px">${esc(m.note)}</div>` : ""}</td>
          <td><span class="badge ${m.kind === "llm" ? "" : "ok"}">${KIND_LABELS[m.kind] || m.kind}</span></td>
          <td class="muted">${esc(m.provider)}</td>
          <td class="mono muted" style="max-width:220px;overflow:hidden;text-overflow:ellipsis">
            ${esc(m.model || m.path || "-")}</td>
          <td class="mono muted" style="max-width:200px;overflow:hidden;text-overflow:ellipsis">
            ${esc(m.base_url || "-")}</td>
          <td>${m.has_api_key ? '<span class="badge ok">已配置</span>' : '<span class="muted">-</span>'}</td>
          <td>${m.dim ?? "-"}</td>
          <td class="actions">
            <button class="btn btn-sm" data-edit-model="${m.id}">编辑</button>
            <button class="btn btn-sm btn-danger" data-del-model="${m.id}">删除</button>
          </td>
        </tr>`).join("")}
      </tbody></table>`;

    $$("[data-edit-model]").forEach((b) => {
      b.addEventListener("click", () => {
        const m = state.models.find((x) => x.id === b.dataset.editModel);
        if (m) openModelModal(m);
      });
    });
    $$("[data-del-model]").forEach((b) => {
      b.addEventListener("click", async () => {
        const m = state.models.find((x) => x.id === b.dataset.delModel);
        if (!confirm(`删除模型「${m?.name || ""}」？`)) return;
        try {
          await api(`/api/admin/models/${b.dataset.delModel}`, { method: "DELETE" });
          toast("已删除", "ok");
          loadModels();
        } catch (e) { toast(e.message, "err"); }
      });
    });
  } catch (e) {
    toast(e.message, "err");
  }
}

/* 新增 / 编辑模型弹窗。model 为 null 时是新增 */
function openModelModal(model) {
  const isEdit = !!model;
  const m = model || { kind: "llm", provider: "openai" };
  modal({
    title: isEdit ? "编辑模型" : "新增模型",
    body: `
      <label class="field"><span>名称 *</span>
        <input class="input" id="mm-name" value="${esc(m.name || "")}"
          placeholder="如 deepseek-chat、bge-m3"></label>

      <div class="row" style="gap:12px">
        <label class="field grow"><span>类型 *</span>
          <select class="select" id="mm-kind" ${isEdit ? "disabled" : ""}>
            <option value="llm" ${m.kind === "llm" ? "selected" : ""}>大模型（LLM）</option>
            <option value="embedding" ${m.kind === "embedding" ? "selected" : ""}>Embedding</option>
          </select></label>
        <label class="field grow"><span>提供方 *</span>
          <select class="select" id="mm-provider"></select></label>
      </div>

      <div id="mm-conn"></div>

      <label class="field"><span>备注</span>
        <input class="input" id="mm-note" value="${esc(m.note || "")}"></label>
      <div id="mm-check" class="field-hint"></div>`,
    okText: isEdit ? "保存" : "新增",
    onOk: async () => {
      const kind = $("#mm-kind").value;
      const provider = $("#mm-provider").value;
      const name = $("#mm-name").value.trim();
      if (!name) throw new Error("名称不能为空");

      const payload = {
        name,
        kind,
        provider,
        model: $("#mm-model")?.value.trim() || "",
        base_url: $("#mm-base")?.value.trim() || "",
        api_key: $("#mm-key")?.value || "",
        path: $("#mm-path")?.value.trim() || "",
        dim: $("#mm-dim")?.value ? Number($("#mm-dim").value) : null,
        note: $("#mm-note").value.trim(),
      };

      if (isEdit) {
        delete payload.kind;
        await api(`/api/admin/models/${model.id}`, {
          method: "PATCH", body: JSON.stringify(payload),
        });
        toast("已保存", "ok");
      } else {
        await api("/api/admin/models", {
          method: "POST", body: JSON.stringify(payload),
        });
        toast("已新增，可在「系统配置」中选择", "ok");
      }
      loadModels();
    },
  });

  // 提供方随类型联动
  const kindSel = $("#mm-kind");
  const provSel = $("#mm-provider");

  // 连接信息随提供方联动
  const renderConn = () => {
    const provider = provSel.value;
    const isLocal = provider === "local";
    const isMock = provider === "mock" || provider === "hash";
    $("#mm-conn").innerHTML = `
      ${!isLocal && !isMock ? `
        <label class="field"><span>API 地址</span>
          <input class="input" id="mm-base" value="${esc(m.base_url || "")}"
            placeholder="${provider === "ollama" ? "http://localhost:11434" : "https://api.example.com/v1"}">
          <div class="field-hint">OpenAI 兼容端点需带 /v1 后缀；Ollama 填服务根地址。</div></label>
        <label class="field"><span>API Key / Token</span>
          <input class="input" type="password" id="mm-key"
            placeholder="${isEdit && m.has_api_key ? "已设置（留空不修改）" : "sk-..."}">
          <div class="field-hint">密钥保存在模型记录中，不会返回给前端。</div></label>` : ""}
      ${isLocal ? `
        <label class="field"><span>模型目录 *</span>
          <input class="input" id="mm-path" value="${esc(m.path || "")}"
            placeholder="D:/models/bge-m3 或 ./models/bge-m3">
          <div class="field-hint">需包含 config.json 与权重文件（model.safetensors / pytorch_model.bin）。</div></label>` : ""}
      <div class="row" style="gap:12px">
        <label class="field grow"><span>模型标识</span>
          <input class="input" id="mm-model" value="${esc(m.model || "")}"
            placeholder="${isLocal ? "可留空" : "如 gpt-4o、deepseek-chat"}">
          <div class="field-hint">调用 API 时使用的模型名。</div></label>
        <label class="field" style="width:120px"><span>维度</span>
          <input class="input" type="number" id="mm-dim" value="${m.dim ?? ""}"
            placeholder="${isLocal ? "自动" : "如 1024"}">
          <div class="field-hint">Embedding 用。</div></label>
      </div>`;
    // 本地模型路径失焦时自动探测
    if (isLocal && $("#mm-path")) {
      $("#mm-path").addEventListener("blur", async () => {
        const path = $("#mm-path").value.trim();
        if (!path) return;
        try {
          const info = await api(
            `/api/admin/models/inspect?path=${encodeURIComponent(path)}`,
            { method: "POST" });
          const chk = $("#mm-check");
          if (!chk) return;
          chk.textContent = info.ok
            ? `✓ ${info.message}${info.dim ? `，维度 ${info.dim}` : ""}`
            : `✗ ${info.message}`;
          chk.style.color = info.ok ? "var(--green)" : "var(--red)";
          if (info.ok && info.dim && !$("#mm-dim").value) $("#mm-dim").value = info.dim;
        } catch { /* 忽略探测失败 */ }
      });
    }
  };

  const fillProviders = () => {
    const kind = kindSel.value;
    const providers = kind === "llm"
      ? ["anthropic", "openai", "ollama", "mock"]
      : ["local", "openai", "ollama", "hash"];
    provSel.innerHTML = providers.map((p) =>
      `<option value="${p}" ${p === m.provider ? "selected" : ""}>${PROVIDER_LABELS[p] || p}</option>`
    ).join("");
    if (!providers.includes(m.provider)) provSel.value = providers[0];
    renderConn();
  };

  kindSel.addEventListener("change", fillProviders);
  provSel.addEventListener("change", renderConn);
  fillProviders();
}

$("#btn-create-model").addEventListener("click", () => openModelModal(null));

$$("#model-tabs [data-kind]").forEach((b) => {
  b.addEventListener("click", () => {
    state.modelKind = b.dataset.kind;
    $$("#model-tabs [data-kind]").forEach((x) =>
      x.classList.toggle("active", x.dataset.kind === state.modelKind));
    loadModels();
  });
});

/* ------------------------------------------------------------------ 系统看板 */
async function loadAdminDash() {
  // 先渲染已有数据，避免切页时空白等待（服务探测较慢）
  if (state.adminDash) renderAdminDash(state.adminDash);
  try {
    const d = await api("/api/admin/dashboard");
    state.adminDash = d;
    renderAdminDash(d);
  } catch (e) {
    toast(e.message, "err");
  }
}

function renderAdminDash(d) {
    const FIELD_LABELS = {
      selected: "当前选中", provider: "提供方", model: "模型", device: "设备",
      dim: "维度", address: "地址", uri: "地址", collection: "集合",
    };
    const modelCard = (title, m) => `
      <div class="card">
        <h2>${title}</h2>
        ${Object.entries(m).map(([k, v]) => `
          <div class="row" style="padding:6px 0;justify-content:space-between">
            <span class="muted">${esc(FIELD_LABELS[k] || k)}</span>
            <span class="mono" style="text-align:right;max-width:65%;word-break:break-all">
              ${esc(v === null || v === undefined || v === "" ? "—" : v)}</span>
          </div>`).join("")}
      </div>`;

    $("#admin-dash-body").innerHTML = `
      <div class="stats" style="margin-bottom:20px">
        <div class="stat"><div class="label">知识库</div><div class="value">${d.totals.kb_count}</div></div>
        <div class="stat"><div class="label">文档</div><div class="value">${d.totals.documents}</div></div>
        <div class="stat"><div class="label">向量块</div><div class="value">${d.totals.chunks}</div></div>
      </div>

      <div class="card" style="margin-bottom:20px">
        <h2>服务状态</h2>
        <div class="svc-grid">
          ${d.services.map((s) => `
            <div class="svc">
              <div class="name"><span class="dot ${s.status}"></span>${esc(s.name)}</div>
              <div class="val mono">${esc(s.address || "-")}</div>
            </div>`).join("")}
        </div>
      </div>

      <div class="form-grid" style="margin-bottom:20px">
        ${modelCard("Embedding 模型", d.models.embedding)}
        ${modelCard("大模型", d.models.llm)}
        ${modelCard("向量库", d.models.vectorstore)}
      </div>

      <div class="card">
        <h2>全部知识库（${d.knowledge_bases.length}）</h2>
        ${d.knowledge_bases.length ? `<table>
          <thead><tr><th>名称</th><th>描述</th><th>集合</th><th>维度</th><th>文档</th><th>块</th></tr></thead>
          <tbody>${d.knowledge_bases.map((k) => `
            <tr>
              <td><strong>${esc(k.name)}</strong></td>
              <td class="muted">${esc(k.description || "-")}</td>
              <td class="mono">${esc(k.collection)}</td>
              <td>${k.embedding_dim}</td>
              <td>${k.document_count}</td>
              <td>${k.chunk_count}</td>
            </tr>`).join("")}
          </tbody></table>` : `<div class="empty">暂无知识库</div>`}
      </div>`;
}

$("#btn-refresh-admin").addEventListener("click", loadAdminDash);

/* ------------------------------------------------------------------ 用户管理 */
async function loadUsers() {
  try {
    const users = await api("/api/auth/users");
    $("#user-list").innerHTML = `<table>
      <thead><tr><th>用户名</th><th>显示名</th><th>角色</th><th>来源</th><th>状态</th><th>最近登录</th><th></th></tr></thead>
      <tbody>${users.map((u) => `
        <tr>
          <td><strong>${esc(u.username)}</strong></td>
          <td class="muted">${esc(u.display_name || "-")}</td>
          <td><span class="badge ${u.role === "admin" ? "ok" : ""}">${esc(u.role)}</span></td>
          <td class="muted">${esc(u.source)}</td>
          <td>${u.is_active ? '<span class="badge ok">启用</span>' : '<span class="badge err">停用</span>'}</td>
          <td class="muted mono">${esc((u.last_login_at || "-").slice(0, 19))}</td>
          <td class="actions">
            ${u.role !== "admin" ? `
              <button class="btn btn-sm" data-toggle-user="${u.id}" data-active="${u.is_active}">
                ${u.is_active ? "停用" : "启用"}</button>
              <button class="btn btn-sm btn-danger" data-del-user="${u.id}">删除</button>` : ""}
          </td>
        </tr>`).join("")}
      </tbody></table>`;

    $$("[data-toggle-user]").forEach((b) => {
      b.addEventListener("click", async () => {
        const active = b.dataset.active === "1";
        await api(`/api/auth/users/${b.dataset.toggleUser}`, {
          method: "PATCH",
          body: JSON.stringify({ is_active: !active }),
        }).catch((e) => toast(e.message, "err"));
        loadUsers();
      });
    });
    $$("[data-del-user]").forEach((b) => {
      b.addEventListener("click", async () => {
        if (!confirm("删除该用户？")) return;
        await api(`/api/auth/users/${b.dataset.delUser}`, { method: "DELETE" })
          .catch((e) => toast(e.message, "err"));
        loadUsers();
      });
    });

    await loadAudit();
  } catch (e) {
    toast(e.message, "err");
  }
}

async function loadAudit() {
  try {
    const logs = await api("/api/admin/audit?limit=30");
    $("#audit-list").innerHTML = logs.length ? `<table>
      <thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>目标</th><th>详情</th></tr></thead>
      <tbody>${logs.map((l) => `
        <tr>
          <td class="muted mono">${esc((l.created_at || "").slice(0, 19).replace("T", " "))}</td>
          <td>${esc(l.actor_name || "-")}</td>
          <td class="mono">${esc(l.action)}</td>
          <td class="muted">${esc(l.target || "-")}</td>
          <td class="muted">${esc(l.detail || "")}</td>
        </tr>`).join("")}
      </tbody></table>` : `<div class="empty">暂无记录</div>`;
  } catch { /* ignore */ }
}

$("#btn-create-user").addEventListener("click", () => {
  modal({
    title: "新建用户",
    body: `
      <label class="field"><span>用户名</span>
        <input class="input" id="m-username"></label>
      <label class="field"><span>密码（至少 6 位）</span>
        <input class="input" id="m-password" type="password"></label>
      <label class="field"><span>显示名</span>
        <input class="input" id="m-display"></label>
      <label class="field"><span>邮箱</span>
        <input class="input" id="m-email"></label>
      <label class="field"><span>角色</span>
        <select class="select" id="m-role">
          <option value="user">普通用户</option>
          <option value="admin">管理员</option>
        </select></label>`,
    okText: "创建",
    onOk: async () => {
      await api("/api/auth/users", {
        method: "POST",
        body: JSON.stringify({
          username: $("#m-username").value.trim(),
          password: $("#m-password").value,
          display_name: $("#m-display").value.trim(),
          email: $("#m-email").value.trim(),
          role: $("#m-role").value,
        }),
      });
      toast("已创建", "ok");
      loadUsers();
    },
  });
});

/* ------------------------------------------------------------------ 初始化 */
boot();
