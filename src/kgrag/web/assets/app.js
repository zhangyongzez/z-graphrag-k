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
  // 带上语言，后端据此返回对应语言的字段说明与错误信息
  const langHeader = { "Accept-Language": getLang() };
  const res = await fetch(path, {
    headers: opts.body instanceof FormData
      ? langHeader
      : { "Content-Type": "application/json", ...langHeader },
    ...opts,
  });
  if (res.status === 401) {
    showLogin();
    throw new Error(t("login.error_empty"));
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function modal({ title, body, okText, cancelText, onOk, onCancel, danger = false }) {
  okText = okText ?? t("common.ok");
  cancelText = cancelText ?? t("common.cancel");
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
  // 「系统配置」里选中的 LLM，会话未指定模型时回退到它
  defaultLlmId: "",
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
  applyI18n();
  syncLangButtons();
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
        hint.textContent = t("login.ad_hint", { server: m.ad_server });
      }
    } catch { /* ignore */ }
  }
}

function enterApp() {
  $("#login-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  $("#user-name").textContent =
    `${state.user.display_name || state.user.username}${isAdmin() ? ` · ${t("users.role_admin")}` : ""}`;
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
    if (!h.embedding_ready) missing.push(t("setup.missing_embedding"));
    if (!h.llm_ready) missing.push(t("setup.missing_llm"));

    const mask = modal({
      title: t("setup.title"),
      body: `
        <div class="muted" style="margin-bottom:16px;line-height:1.8">
          ${esc(t("setup.intro"))}
        </div>
        <div class="card" style="background:var(--bg);margin-bottom:14px">
          <div style="margin-bottom:10px"><strong>${esc(t("setup.missing"))}</strong></div>
          ${missing.map((m) => `<div style="padding:4px 0">· ${esc(m)}</div>`).join("")}
        </div>
        <div class="muted" style="font-size:13px;line-height:1.9">
          <strong style="color:var(--text)">${esc(t("setup.steps"))}</strong><br>
          ${esc(t("setup.step1"))}<br>
          ${esc(t("setup.step2"))}<br>
          ${esc(t("setup.step3"))}<br>
          <br>
          <span style="color:var(--amber)">${esc(t("setup.note"))}</span>
        </div>
        <label class="row" style="margin-top:16px;gap:8px;cursor:pointer">
          <input type="checkbox" id="setup-skip">
          <span class="muted" style="font-size:13px">${esc(t("setup.dont_show"))}</span>
        </label>`,
      okText: t("setup.go"),
      cancelText: t("setup.later"),
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
      : `<span style="color:var(--amber)">${esc(t("nav.embedding_unconfigured"))}</span>`;
    const llm = h.llm_ready
      ? `LLM · ${esc(h.llm_model || h.llm)}`
      : `<span style="color:var(--amber)">${esc(t("nav.llm_unconfigured"))}</span>`;
    $("#sys-status").innerHTML =
      `${esc(h.vectorstore)}<br>${emb}<br>${llm}`;
  } catch {
    $("#sys-status").textContent = t("nav.service_down");
  }
}

$("#btn-login").addEventListener("click", doLogin);
$("#login-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });

async function doLogin() {
  const username = $("#login-user").value.trim();
  const password = $("#login-pass").value;
  if (!username || !password) return toast(t("login.error_empty"), "err");
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
      [t("dash.kb_count"), s.kb_count],
      [t("dash.docs"), s.total_documents],
      [t("dash.chunks"), s.total_chunks],
      [t("dash.embedding"), `${s.embedding_provider} · ${s.embedding_dim}`],
      [t("dash.vectorstore"), s.vectorstore_provider],
      [t("dash.llm"), `${s.llm_provider} · ${s.llm_model || "-"}`],
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
            <div>${esc(kb.name)} ${kb.is_public ? `<span class="badge">${esc(t("dash.public"))}</span>` : ""}
              ${kb.is_owner ? `<span class="badge ok">${esc(t("dash.mine"))}</span>` : ""}</div>
            <div class="muted">${esc(kb.description || t("dash.no_desc"))}</div>
          </div>
          <span class="badge">${kb.document_count} ${esc(t("dash.docs"))}</span>
          <span class="badge">${kb.chunk_count} ${esc(t("dash.chunks"))}</span>
        </div>`).join("")
      : `<div class="empty">${esc(t("dash.no_kb"))}</div>`;
  } catch (e) {
    toast(t("kb.load_failed", { msg: e.message }), "err");
  }
}

$("#btn-refresh").addEventListener("click", loadDashboard);

/* ------------------------------------------------------------------ 知识库列表 */
async function loadKbList() {
  try {
    state.kbs = await api("/api/kb");
    const el = $("#kb-list");
    if (!state.kbs.length) {
      el.innerHTML = `<div class="empty"><div class="big">▤</div>${esc(t("kb.empty"))}</div>`;
      return;
    }
    el.innerHTML = `<div class="card"><table>
      <thead><tr>
        <th>${esc(t("kb.name"))}</th><th>${esc(t("kb.description"))}</th>
        <th>${esc(t("kb.owner"))}</th><th>${esc(t("kb.docs"))}</th>
        <th>${esc(t("kb.chunks"))}</th><th>${esc(t("kb.created_at"))}</th><th></th>
      </tr></thead>
      <tbody>${state.kbs.map((kb) => `
        <tr>
          <td><strong>${esc(kb.name)}</strong>
            ${kb.is_public ? `<span class="badge">${esc(t("dash.public"))}</span>` : ""}</td>
          <td class="muted">${esc(kb.description || "-")}</td>
          <td class="muted">${esc(kb.owner_name || "—")}</td>
          <td>${kb.document_count}</td>
          <td>${kb.chunk_count}</td>
          <td class="muted mono">${esc(kb.created_at.slice(0, 10))}</td>
          <td class="actions">
            <button class="btn btn-sm" data-open="${kb.id}">${esc(t("kb.manage"))}</button>
          </td>
        </tr>`).join("")}
      </tbody></table></div>`;
    $$("[data-open]").forEach((b) => {
      b.addEventListener("click", () => openKb(b.dataset.open));
    });
  } catch (e) {
    toast(t("kb.load_failed", { msg: e.message }), "err");
  }
}

/* ------------------------------------------------------------------ 新建知识库 */
$("#btn-create-kb").addEventListener("click", () => {
  modal({
    title: t("kb.create"),
    body: `
      <label class="field"><span>${esc(t("kb.name"))}</span>
        <input class="input" id="m-name" placeholder="${esc(t("kb.name_placeholder"))}"></label>
      <label class="field"><span>${esc(t("kb.description"))}</span>
        <textarea class="textarea" id="m-desc" placeholder="${esc(t("kb.desc_placeholder"))}"></textarea></label>`,
    okText: t("common.create"),
    onOk: async () => {
      const name = $("#m-name").value.trim();
      if (!name) throw new Error(t("kb.name_required"));
      await api("/api/kb", {
        method: "POST",
        body: JSON.stringify({ name, description: $("#m-desc").value.trim() }),
      });
      toast(t("kb.create_success"), "ok");
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
    $("#kb-detail-desc").textContent = kb.description || t("dash.no_desc");
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
      el.innerHTML = `<div class="empty">${esc(t("kb.detail.no_docs"))}</div>`;
      return;
    }
    el.innerHTML = `<table>
      <thead><tr><th>${esc(t("kb.detail.filename"))}</th>
        <th>${esc(t("kb.detail.chunks"))}</th><th></th></tr></thead>
      <tbody>${data.documents.map((d) => `
        <tr>
          <td>${esc(d.filename)}</td>
          <td>${d.chunks}</td>
          <td class="actions">
            <button class="btn btn-sm btn-danger" data-del="${esc(d.filename)}">${esc(t("common.delete"))}</button>
          </td>
        </tr>`).join("")}
      </tbody></table>
      <div class="muted" style="margin-top:12px">${esc(t("kb.detail.total_chunks", { n: data.row_count }))}</div>`;
    $$("[data-del]").forEach((b) => {
      b.addEventListener("click", async () => {
        if (!confirm(t("kb.detail.delete_doc_confirm", { name: b.dataset.del }))) return;
        try {
          await api(`/api/kb/${kb.id}/documents/${encodeURIComponent(b.dataset.del)}`,
            { method: "DELETE" });
          toast(t("kb.deleted"), "ok");
          loadKbDocs();
        } catch (e) { toast(e.message, "err"); }
      });
    });
  } catch (e) {
    toast(e.message, "err");
  }
}

$("#btn-back-kb").addEventListener("click", () => show("kb"));

/* 从服务器本地路径导入（适合大批量目录） */
$("#btn-ingest-local").addEventListener("click", () => {
  const kb = state.currentKb;
  if (!kb) return;
  modal({
    title: t("kb.detail.ingest_title"),
    body: `
      <label class="field"><span>${esc(t("kb.detail.ingest_path"))}</span>
        <input class="input" id="ing-path"
          placeholder="D:/docs  or  /data/docs">
        <div class="field-hint">${esc(t("kb.detail.ingest_hint"))}</div>
      </label>
      <div id="ing-result" class="field-hint"></div>`,
    okText: t("kb.detail.ingest_start"),
    onOk: async () => {
      const path = $("#ing-path").value.trim();
      if (!path) throw new Error(t("kb.detail.path_required"));

      const btn = document.querySelector("#modal-ok");
      if (btn) { btn.disabled = true; btn.textContent = t("common.loading"); }
      const res = $("#ing-result");
      if (res) res.textContent = t("kb.detail.ingest_progress");

      try {
        const r = await api(`/api/kb/${kb.id}/ingest?path=${encodeURIComponent(path)}`, {
          method: "POST",
        });
        toast(t("kb.detail.ingest_done", { files: r.files, chunks: r.chunks }), "ok");
        loadKbDocs();
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = t("kb.detail.ingest_start"); }
      }
    },
  });
  setTimeout(() => $("#ing-path")?.focus(), 0);
});

$("#btn-delete-kb").addEventListener("click", async () => {
  const kb = state.currentKb;
  if (!kb) return;
  if (!confirm(t("kb.delete_confirm", { name: kb.name }))) return;
  try {
    await api(`/api/kb/${kb.id}`, { method: "DELETE" });
    toast(t("kb.deleted"), "ok");
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
     <span class="muted" data-s="${esc(f.name)}">${esc(t("kb.detail.uploading"))}</span></div>`).join("");

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
        el.textContent = r.ok ? `✓ ${r.chunks} ${t("kb.detail.chunks")}` : `✗ ${r.error}`;
        el.className = r.ok ? "badge ok" : "badge err";
      }
    });
    toast(t("kb.detail.upload_done", { ok: res.uploaded, fail: res.failed }),
      res.failed ? "err" : "ok");
    setTimeout(() => { prog.innerHTML = ""; }, 3000);
    loadKbDocs();
  } catch (e) {
    toast(t("kb.detail.upload_failed", { msg: e.message }), "err");
    prog.innerHTML = "";
  }
}

/* ------------------------------------------------------------------ 对话 */
async function loadChat() {
  // 三个加载互不依赖，任一失败都不能拖垮其它（否则下拉框会空白）
  await Promise.all([
    loadConversations(),
    loadChatKbs(),
    loadModelOptions(),
  ]);
  if (state.conversations.length && !state.currentConv) {
    openConversation(state.conversations[0].id);
  }
}

/* 对话页左侧的「知识库权重」需要全量知识库清单 */
async function loadChatKbs() {
  try {
    state.kbs = await api("/api/kb");
    const el = $("#chat-kb-weights");
    if (el) renderKbWeights(state.currentConv?.kb_weights || []);
  } catch (e) {
    toast(e.message, "err");
  }
}

async function loadModelOptions() {
  try {
    const [h, models] = await Promise.all([
      api("/health"),
      api("/api/models?kind=llm").catch(() => []),
    ]);
    const sel = $("#chat-model");
    if (!sel) return;
    if (!models.length) {
      sel.innerHTML = `<option value="">${esc(t("chat.no_model_registered"))}</option>`;
      return;
    }
    // 只列真实模型；默认选中「系统配置」里指定的那个，避免出现两个指向同一模型的选项
    sel.innerHTML = models.map((m) =>
      `<option value="${esc(m.id)}">${esc(m.name)}（${esc(m.provider)}）</option>`
    ).join("");
    state.defaultLlmId = h.llm_model_id || models[0].id;
    sel.value = state.defaultLlmId;
  } catch (e) {
    toast(e.message, "err");
  }
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
    el.innerHTML = `<div class="muted" style="padding:12px;text-align:center">${esc(t("chat.no_conv"))}</div>`;
    return;
  }
  el.innerHTML = state.conversations.map((c) => `
    <div class="list-item ${state.currentConv?.id === c.id ? "active" : ""}" data-conv="${c.id}">
      <span class="grow" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        ${esc(c.title)}</span>
      <button class="btn btn-sm" data-del-conv="${c.id}" title="${esc(t("common.delete"))}">×</button>
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
      if (!confirm(t("chat.delete_confirm"))) return;
      await api(`/api/chat/conversations/${btn.dataset.delConv}`, { method: "DELETE" });
      if (state.currentConv?.id === btn.dataset.delConv) state.currentConv = null;
      loadConversations();
      if (!state.currentConv) $("#chat-log").innerHTML = emptyChat();
    });
  });
}

function emptyChat() {
  return `<div class="empty"><div class="big">◐</div>${esc(t("chat.empty"))}</div>`;
}

async function openConversation(id) {
  try {
    const c = await api(`/api/chat/conversations/${id}`);
    state.currentConv = c;
    state.chatHistory = c.messages
      .filter((m) => m.role !== "system")
      .map((m) => [m.role === "user" ? "human" : "ai", m.content]);
    $("#chat-title").textContent = c.title || t("chat.title");
    $("#chat-sub").textContent =
      c.kb_weights.length
        ? t("chat.kb_linked", { n: c.kb_weights.length })
        : t("chat.no_kb_selected");
    // 会话没存模型时，回退到「系统配置」里选中的那个，避免下拉框空白
    $("#chat-model").value = c.llm_model || state.defaultLlmId;
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
    else appendMsg("assistant", m.content, m.sources, m.reasoning);
  });
  log.scrollTop = log.scrollHeight;
}

function renderKbWeights(weights) {
  const el = $("#chat-kb-weights");
  if (!state.kbs.length) {
    el.innerHTML = `<div class="muted" style="font-size:12px;padding:6px 0">${esc(t("chat.no_kb"))}</div>`;
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
  $("#chat-sub").textContent = weights.length
    ? t("chat.kb_linked", { n: weights.length })
    : t("chat.no_kb_selected");
}

$("#btn-new-conv").addEventListener("click", async () => {
  try {
    const c = await api("/api/chat/conversations", {
      method: "POST",
      body: JSON.stringify({ title: t("chat.new"), llm_model: state.defaultLlmId }),
    });
    state.conversations.unshift(c);
    await openConversation(c.id);
  } catch (e) {
    toast(e.message, "err");
  }
});

/* 切换大模型：立即写回当前会话，避免切走再切回时丢失选择 */
$("#chat-model").addEventListener("change", async () => {
  if (!state.currentConv) return;
  const modelId = $("#chat-model").value;
  try {
    await api(`/api/chat/conversations/${state.currentConv.id}`, {
      method: "PATCH",
      body: JSON.stringify({ llm_model: modelId }),
    });
    state.currentConv.llm_model = modelId;
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

function appendMsg(role, text, sources, reasoning) {
  const log = $("#chat-log");
  const empty = log.querySelector(".empty");
  if (empty) empty.remove();

  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `
    <div class="avatar">${role === "user" ? t("chat.me") : "AI"}</div>
    <div class="bubble"></div>`;
  const bubble = div.querySelector(".bubble");

  // 推理过程：折叠展示在正文之前
  if (reasoning) {
    bubble.appendChild(buildReasoning(reasoning));
  }

  const body = document.createElement("div");
  body.className = "msg-body";
  body.textContent = text;
  bubble.appendChild(body);

  log.appendChild(div);
  log.scrollTop = log.scrollHeight;

  if (sources && sources.length) {
    const det = document.createElement("details");
    det.className = "sources";
    det.innerHTML = `<summary>${esc(t("chat.sources", { n: sources.length }))}</summary><ul>${
      sources.map((s) => `<li>${esc(s.filename || "?")} — ${esc((s.preview || "").slice(0, 70))}</li>`).join("")
    }</ul>`;
    bubble.appendChild(det);
  }
  return div;
}

/* 推理过程区块（可折叠） */
function buildReasoning(text) {
  const det = document.createElement("details");
  det.className = "reasoning";
  det.innerHTML = `<summary>💭 ${esc(t("chat.reasoning"))}</summary><div class="reasoning-body"></div>`;
  det.querySelector(".reasoning-body").textContent = text;
  return det;
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
    let reasoning = "";
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
          bubble.querySelector(".avatar").textContent = t("chat.retrieval_only");
        } else if (ev === "reasoning") {
          reasoning += data.text;
          let det = bubble.querySelector(".reasoning");
          if (!det) {
            det = buildReasoning("");
            bubble.querySelector(".bubble").prepend(det);
          }
          det.querySelector(".reasoning-body").textContent = reasoning;
          $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
        } else if (ev === "delta") {
          answer += data.text;
          let body = bubble.querySelector(".msg-body");
          if (!body) {
            body = document.createElement("div");
            body.className = "msg-body";
            bubble.querySelector(".bubble").appendChild(body);
          }
          body.textContent = answer;
          $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
        } else if (ev === "error") {
          throw new Error(data.message);
        }
      }
    }

    bubble.querySelector(".avatar").textContent =
      bubble.dataset.retrievalOnly ? t("chat.retrieval_only") : "AI";
    if (sources.length) {
      const det = document.createElement("details");
      det.className = "sources";
      det.innerHTML = `<summary>${esc(t("chat.sources", { n: sources.length }))}</summary><ul>${
        sources.map((s) => `<li>${esc(s.filename || "?")} — ${esc((s.preview || "").slice(0, 70))}</li>`).join("")
      }</ul>`;
      bubble.querySelector(".bubble").appendChild(det);
    }
    state.chatHistory.push(["human", q], ["ai", answer]);
    loadConversations();
  } catch (e) {
    bubble.querySelector(".avatar").textContent = "!";
    bubble.querySelector(".bubble").textContent = t("chat.error", { msg: e.message });
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
      const what = f.model_kind === "llm"
      ? t("config.model_kind_llm")
      : t("config.model_kind_embedding");
      return `<div class="muted" style="padding:8px 0;font-size:13px">
        ${esc(t("config.no_model", { kind: what }))}</div>`;
    }
    return `<select class="select" id="cfg-${f.key}">
      <option value="">${esc(t("config.not_selected"))}</option>
      ${opts.map((o) => `<option value="${esc(o.id)}" ${o.id === val ? "selected" : ""}>
        ${esc(o.name)}${o.model ? ` · ${esc(o.model)}` : ""}（${esc(o.provider)}）</option>`).join("")}
    </select>`;
  }
  if (f.type === "bool") {
    return `<div class="switch">
      <input type="checkbox" id="cfg-${f.key}" ${val ? "checked" : ""}>
      <span class="muted">${val ? esc(t("common.enabled")) : esc(t("common.disabled"))}</span></div>`;
  }
  if (f.type === "select") {
    return `<select class="select" id="cfg-${f.key}">${
      f.options.map((o) => `<option value="${esc(o)}" ${o === val ? "selected" : ""}>${esc(o)}</option>`).join("")
    }</select>`;
  }
  if (f.type === "password") {
    const isSet = val && val.is_set;
    return `<input class="input" type="password" id="cfg-${f.key}"
      placeholder="${isSet ? esc(t("config.set_secret")) : esc(t("config.not_set"))}"
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
        ? `<span class="badge err">${esc(t("config.need_restart"))}</span>`
        : `<span class="badge ok">${esc(t("config.hot_reload"))}</span>`}
      ${g.description ? `<span class="muted">${esc(g.description)}</span>` : ""}
    </div>
    <div class="form-grid">
      ${g.fields.map((f) => `
        <label class="field" ${f.type === "model_select" ? 'style="grid-column:1/-1"' : ""}>
          <span>${esc(f.label)}
            ${!isDefault(f) ? `<span class="badge" style="margin-left:6px">${esc(t("config.modified"))}</span>` : ""}
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
      ? (r.restart_required
        ? t("config.saved_restart", { n: r.changed.length })
        : t("config.saved", { n: r.changed.length }))
      : t("config.no_change"), r.restart_required ? "err" : "ok");
    loadConfig();
  } catch (e) {
    toast(e.message, "err");
  }
});

$("#btn-reset-config").addEventListener("click", async () => {
  if (!confirm(t("config.reset_confirm"))) return;
  try {
    await api("/api/admin/config/reset", { method: "POST" });
    toast(t("config.reset_done"), "ok");
    loadConfig();
  } catch (e) {
    toast(e.message, "err");
  }
});

/* ------------------------------------------------------------------ 模型管理 */
/* 提供方显示名。用函数而非常量，保证切换语言后取到新值 */
function providerLabel(key) {
  const map = {
    anthropic: "Anthropic (Claude)",
    openai: t("models.provider_openai"),
    ollama: "Ollama",
    local: t("models.provider_local"),
    hash: t("models.provider_hash"),
    mock: "Mock",
  };
  return map[key] || key;
}

const KIND_LABELS = {
  llm: t("models.tab_llm"),
  embedding: "Embedding",
};

async function loadModels() {
  try {
    const kind = state.modelKind === "all" ? "" : state.modelKind;
    state.models = await api("/api/admin/models" + (kind ? `?kind=${kind}` : ""));
    const el = $("#model-list");
    if (!state.models.length) {
      el.innerHTML = `<div class="empty">
        <div class="big">◈</div>${esc(t("models.empty"))}</div>`;
      return;
    }
    el.innerHTML = `<table>
      <thead><tr>
        <th>${esc(t("models.name"))}</th><th>${esc(t("models.kind"))}</th>
        <th>${esc(t("models.provider"))}</th><th>${esc(t("models.model_path"))}</th>
        <th>${esc(t("models.address"))}</th><th>${esc(t("models.key"))}</th>
        <th>${esc(t("models.dim"))}</th><th></th>
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
          <td>${m.has_api_key ? `<span class="badge ok">${esc(t("common.configured"))}</span>` : '<span class="muted">-</span>'}</td>
          <td>${m.dim ?? "-"}</td>
          <td class="actions">
            <button class="btn btn-sm" data-edit-model="${m.id}">${esc(t("common.edit"))}</button>
            <button class="btn btn-sm btn-danger" data-del-model="${m.id}">${esc(t("common.delete"))}</button>
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
        if (!confirm(t("models.delete_confirm", { name: m?.name || "" }))) return;
        try {
          await api(`/api/admin/models/${b.dataset.delModel}`, { method: "DELETE" });
          toast(t("kb.deleted"), "ok");
          loadModels();
        } catch (e) { toast(e.message, "err"); }
      });
    });
  } catch (e) {
    toast(e.message, "err");
  }
}

/* 新增 / 编辑模型弹窗。
 * - openModelModal()                          → 新增（默认大模型）
 * - openModelModal(null, {kind, provider})    → 新增（预填默认值）
 * - openModelModal(existingModel)             → 编辑
 */
function openModelModal(model, defaults) {
  const isEdit = !!model?.id;
  const m = model || { kind: "llm", provider: "openai", ...defaults };
  modal({
    title: isEdit ? t("models.edit_title") : t("models.create_title"),
    body: `
      <div class="row" style="gap:12px;margin-bottom:4px">
        <label class="field grow" style="margin-bottom:0"><span>${esc(t("models.kind"))} *</span>
          <select class="select" id="mm-kind" ${isEdit ? "disabled" : ""}>
            <option value="llm" ${m.kind === "llm" ? "selected" : ""}>${esc(t("models.kind_llm"))}</option>
            <option value="embedding" ${m.kind === "embedding" ? "selected" : ""}>${esc(t("models.kind_embedding"))}</option>
          </select>
          <div class="field-hint" id="mm-kind-hint"></div></label>
      </div>

      <label class="field"><span>${esc(t("models.name"))} *</span>
        <input class="input" id="mm-name" value="${esc(m.name || "")}"
          placeholder="${esc(t("models.placeholder_name"))}"></label>

      <label class="field"><span>${esc(t("models.provider"))} *</span>
        <select class="select" id="mm-provider"></select>
        <div class="field-hint" id="mm-provider-hint"></div></label>

      <div id="mm-conn"></div>

      <label class="field"><span>${esc(t("models.note"))}</span>
        <input class="input" id="mm-note" value="${esc(m.note || "")}"></label>
      <div id="mm-check" class="field-hint"></div>`,
    okText: isEdit ? t("common.save") : t("common.add"),
    onOk: async () => {
      const kind = $("#mm-kind").value;
      const provider = $("#mm-provider").value;
      const name = $("#mm-name").value.trim();
      if (!name) throw new Error(t("models.name_required"));

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
        toast(t("models.saved"), "ok");
      } else {
        const created = await api("/api/admin/models", {
          method: "POST", body: JSON.stringify(payload),
        });
        if (created?.warning) {
          toast(created.warning, "err");
        } else {
          toast(t("models.created"), "ok");
        }
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
        <label class="field"><span>${esc(t("models.api_base"))}</span>
          <input class="input" id="mm-base" value="${esc(m.base_url || "")}"
            placeholder="${provider === "ollama" ? "http://localhost:11434" : esc(t("models.placeholder_base"))}">
          <div class="field-hint">${esc(t("models.hint_api_base"))}</div></label>
        <label class="field"><span>${esc(t("models.api_key"))}</span>
          <input class="input" type="password" id="mm-key"
            placeholder="${isEdit && m.has_api_key ? esc(t("config.set_secret")) : "sk-..."}">
          <div class="field-hint">${esc(t("models.hint_api_key"))}</div></label>` : ""}
      ${isLocal ? `
        <label class="field"><span>${esc(t("models.local_path"))} *</span>
          <input class="input" id="mm-path" value="${esc(m.path || "")}"
            placeholder="${esc(t("models.placeholder_path"))}">
          <div class="field-hint">${esc(t("models.hint_local_path"))}</div></label>` : ""}
      <div class="row" style="gap:12px">
        <label class="field grow"><span>${esc(t("models.model_id"))}</span>
          <input class="input" id="mm-model" value="${esc(m.model || "")}"
            placeholder="${isLocal ? "" : esc(t("models.placeholder_model"))}">
          <div class="field-hint">${esc(t("models.hint_model_id"))}</div></label>
        <label class="field" style="width:120px"><span>${esc(t("models.dim"))}</span>
          <input class="input" type="number" id="mm-dim" value="${m.dim ?? ""}"
            placeholder="${isLocal ? esc(t("models.auto")) : esc(t("models.placeholder_dim"))}">
          <div class="field-hint">${esc(t("models.hint_dim"))}</div></label>
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
            ? `✓ ${info.message}${info.dim ? ` (${t("models.dim")} ${info.dim})` : ""}`
            : `✗ ${info.message}`;
          chk.style.color = info.ok ? "var(--green)" : "var(--red)";
          if (info.ok && info.dim && !$("#mm-dim").value) $("#mm-dim").value = info.dim;
        } catch { /* 忽略探测失败 */ }
      });
    }
  };

  const KIND_HINTS = {
    llm: t("models.hint_kind_llm"),
    embedding: t("models.hint_kind_embedding"),
  };
  const PROVIDER_HINTS = {
    anthropic: t("models.hint_anthropic"),
    openai: t("models.hint_openai"),
    ollama: t("models.hint_ollama"),
    local: t("models.hint_local"),
    hash: t("models.hint_hash"),
    mock: t("models.hint_mock"),
  };

  const fillProviders = () => {
    const kind = kindSel.value;
    const providers = kind === "llm"
      ? ["anthropic", "openai", "ollama", "mock"]
      : ["local", "openai", "ollama", "hash"];
    provSel.innerHTML = providers.map((p) =>
      `<option value="${p}" ${p === m.provider ? "selected" : ""}>${providerLabel(p)}</option>`
    ).join("");
    if (!providers.includes(m.provider)) provSel.value = providers[0];
    $("#mm-kind-hint").textContent = KIND_HINTS[kind] || "";
    renderConn();
  };

  kindSel.addEventListener("change", fillProviders);
  provSel.addEventListener("change", () => {
    $("#mm-provider-hint").textContent = PROVIDER_HINTS[provSel.value] || "";
    renderConn();
  });
  fillProviders();
  $("#mm-provider-hint").textContent = PROVIDER_HINTS[provSel.value] || "";
}

$("#btn-create-model").addEventListener("click", () => openModelModal(null));
$("#btn-create-local-model").addEventListener("click", () =>
  openModelModal(null, { kind: "embedding", provider: "local" }));

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
      selected: t("admin_dash.selected"), provider: t("admin_dash.provider"),
      model: t("admin_dash.model"), device: t("admin_dash.device"),
      dim: t("admin_dash.dim"), address: t("admin_dash.address"),
      uri: t("admin_dash.uri"), collection: t("admin_dash.collection"),
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
        <div class="stat"><div class="label">${esc(t("dash.kb_count"))}</div><div class="value">${d.totals.kb_count}</div></div>
        <div class="stat"><div class="label">${esc(t("dash.docs"))}</div><div class="value">${d.totals.documents}</div></div>
        <div class="stat"><div class="label">${esc(t("dash.chunks"))}</div><div class="value">${d.totals.chunks}</div></div>
      </div>

      <div class="card" style="margin-bottom:20px">
        <h2>${esc(t("admin_dash.services"))}</h2>
        <div class="svc-grid">
          ${d.services.map((s) => `
            <div class="svc">
              <div class="name"><span class="dot ${s.status}"></span>${esc(s.name)}</div>
              <div class="val mono">${esc(s.address || "-")}</div>
            </div>`).join("")}
        </div>
      </div>

      <div class="form-grid" style="margin-bottom:20px">
        ${modelCard(t("admin_dash.embedding"), d.models.embedding)}
        ${modelCard(t("admin_dash.llm"), d.models.llm)}
        ${modelCard(t("admin_dash.vectorstore"), d.models.vectorstore)}
      </div>

      <div class="card">
        <h2>${esc(t("admin_dash.kbs", { n: d.knowledge_bases.length }))}</h2>
        ${d.knowledge_bases.length ? `<table>
          <thead><tr><th>${esc(t("kb.name"))}</th><th>${esc(t("kb.description"))}</th>
            <th>${esc(t("admin_dash.collection"))}</th><th>${esc(t("admin_dash.dim"))}</th>
            <th>${esc(t("kb.docs"))}</th><th>${esc(t("kb.chunks"))}</th></tr></thead>
          <tbody>${d.knowledge_bases.map((k) => `
            <tr>
              <td><strong>${esc(k.name)}</strong></td>
              <td class="muted">${esc(k.description || "-")}</td>
              <td class="mono">${esc(k.collection)}</td>
              <td>${k.embedding_dim}</td>
              <td>${k.document_count}</td>
              <td>${k.chunk_count}</td>
            </tr>`).join("")}
          </tbody></table>` : `<div class="empty">${esc(t("admin_dash.no_kb"))}</div>`}
      </div>`;
}

$("#btn-refresh-admin").addEventListener("click", loadAdminDash);

/* ------------------------------------------------------------------ 用户管理 */
async function loadUsers() {
  try {
    const users = await api("/api/auth/users");
    $("#user-list").innerHTML = `<table>
      <thead><tr><th>${esc(t("users.username"))}</th><th>${esc(t("users.display_name"))}</th>
        <th>${esc(t("users.role"))}</th><th>${esc(t("users.source"))}</th>
        <th>${esc(t("users.status"))}</th><th>${esc(t("users.last_login"))}</th><th></th></tr></thead>
      <tbody>${users.map((u) => `
        <tr>
          <td><strong>${esc(u.username)}</strong></td>
          <td class="muted">${esc(u.display_name || "-")}</td>
          <td><span class="badge ${u.role === "admin" ? "ok" : ""}">${esc(u.role)}</span></td>
          <td class="muted">${esc(u.source)}</td>
          <td>${u.is_active ? `<span class="badge ok">${esc(t("users.active"))}</span>` : `<span class="badge err">${esc(t("users.inactive"))}</span>`}</td>
          <td class="muted mono">${esc((u.last_login_at || "-").slice(0, 19))}</td>
          <td class="actions">
            ${u.role !== "admin" ? `
              <button class="btn btn-sm" data-toggle-user="${u.id}" data-active="${u.is_active}">
                ${u.is_active ? esc(t("users.disable")) : esc(t("users.enable"))}</button>
              <button class="btn btn-sm btn-danger" data-del-user="${u.id}">${esc(t("common.delete"))}</button>` : ""}
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
        if (!confirm(t("users.delete_confirm"))) return;
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
      <thead><tr><th>${esc(t("users.time"))}</th><th>${esc(t("users.operator"))}</th>
        <th>${esc(t("users.action"))}</th><th>${esc(t("users.target"))}</th>
        <th>${esc(t("users.detail"))}</th></tr></thead>
      <tbody>${logs.map((l) => `
        <tr>
          <td class="muted mono">${esc((l.created_at || "").slice(0, 19).replace("T", " "))}</td>
          <td>${esc(l.actor_name || "-")}</td>
          <td class="mono">${esc(l.action)}</td>
          <td class="muted">${esc(l.target || "-")}</td>
          <td class="muted">${esc(l.detail || "")}</td>
        </tr>`).join("")}
      </tbody></table>` : `<div class="empty">${esc(t("users.audit_empty"))}</div>`;
  } catch { /* ignore */ }
}

$("#btn-create-user").addEventListener("click", () => {
  modal({
    title: t("users.create_title"),
    body: `
      <label class="field"><span>${esc(t("users.username"))}</span>
        <input class="input" id="m-username"></label>
      <label class="field"><span>${esc(t("users.password"))}</span>
        <input class="input" id="m-password" type="password"></label>
      <label class="field"><span>${esc(t("users.display_name"))}</span>
        <input class="input" id="m-display"></label>
      <label class="field"><span>${esc(t("users.email"))}</span>
        <input class="input" id="m-email"></label>
      <label class="field"><span>${esc(t("users.role"))}</span>
        <select class="select" id="m-role">
          <option value="user">${esc(t("users.role_user"))}</option>
          <option value="admin">${esc(t("users.role_admin"))}</option>
        </select></label>`,
    okText: t("common.create"),
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
      toast(t("users.created"), "ok");
      loadUsers();
    },
  });
});

/* ------------------------------------------------------------------ 初始化 */
/* ------------------------------------------------------------------ 语言切换 */
function syncLangButtons() {
  $$(".lang-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.lang === getLang());
  });
}

$$(".lang-btn").forEach((b) => {
  b.addEventListener("click", () => {
    setLang(b.dataset.lang);
    syncLangButtons();
    // 动态内容需要重绘
    if (state.user) {
      show(state.page);
    }
  });
});

// 语言变化时刷新动态内容
window.addEventListener("langchange", () => {
  syncLangButtons();
  if (state.user) show(state.page);
});

boot();
