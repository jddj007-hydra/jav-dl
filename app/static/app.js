const $ = (id) => document.getElementById(id);
const views = {
  search: $("view-search"),
  western: $("view-western"),
  library: $("view-library"),
  follow: $("view-follow"),
  queue: $("view-queue"),
  settings: $("view-settings"),
};

let lastResources = [];
let lastWorks = [];
let lastWorksQuery = "";
let lastLibrary = null;
let lastSuck = false;
let lastMeta = null;
let detailCode = "";
const LIB_PAGE = 120;
let libraryKind = "jav";
let libraryPayload = null;
let libraryQuery = "";
let librarySort = "group";
let libraryLimit = LIB_PAGE;
let fromWorks = false;
let queueSource = null;
let queueRetry = null;
let queueSlow = null;
let sseFailures = 0;
let javKind = "censored";
let javPage = 1;
let javMode = "latest";
let javBootstrapped = false;
let westernKind = "scene";
let westernPage = 1;
let westernMode = "latest";
let westernTheme = "";
let westernBootstrapped = false;
let westernItems = [];
let westernResources = [];
let westernCurrent = null;
let queueItems = [];
let queueFilter = "all";

const STATUS_LABEL = {
  queued: "排队",
  waiting: "排队",
  active: "下载中",
  downloading: "下载中",
  paused: "已暂停",
  complete: "已完成",
  error: "失败",
  cancelled: "已取消",
  removed: "已取消",
};

function renderPanelLinks(settings) {
  const links = (settings && settings.panels) || [];
  const html = links.map((item) => {
    const url = escapeHtml(item.url || "");
    const label = escapeHtml(item.label || "");
    if (!url) return "";
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  }).join("");
  for (const id of ["panel-links", "queue-panels"]) {
    const box = $(id);
    if (!box) continue;
    box.innerHTML = html;
    box.hidden = !html;
  }
  syncHeaderHeight();
}

async function loadPanelLinks() {
  try {
    renderPanelLinks(await api("/api/settings"));
  } catch {
    renderPanelLinks(null);
  }
}

function route() {
  const hash = location.hash.replace("#/", "") || "search";
  const name = hash.startsWith("queue")
    ? "queue"
    : hash.startsWith("settings")
      ? "settings"
      : hash.startsWith("follow")
        ? "follow"
        : hash.startsWith("library")
          ? "library"
          : hash.startsWith("western")
            ? "western"
            : "search";
  Object.entries(views).forEach(([k, el]) => { el.hidden = k !== name; });
  document.querySelectorAll("nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.nav === name);
  });
  if (name === "queue") {
    refreshQueue();
    startQueueStream();
  } else {
    stopQueueStream();
  }
  if (name === "follow") loadFollow();
  if (name === "settings") loadSettings();
  if (name === "library") loadLibrary();
  if (name === "search") ensureJavLatest();
  if (name === "western") ensureWesternLatest();
}

async function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const headers = { ...(opts.headers || {}) };
  if (opts.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, {
    credentials: "same-origin",
    ...opts,
    method,
    headers,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error)) || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

let filePickResolve = null;

function closeFilePicker(value) {
  $("file-picker").hidden = true;
  $("file-picker-note").hidden = true;
  const resolve = filePickResolve;
  filePickResolve = null;
  if (resolve) resolve(value);
}

function askFiles(files) {
  const list = $("file-picker-list");
  list.innerHTML = (files || []).map((file) => `
    <li>
      <label>
        <input type="checkbox" data-index="${file.index}" ${file.selected ? "checked" : ""} />
        <span class="name">${escapeHtml(file.name || "")}</span>
        <span class="num">${escapeHtml(file.size_text || "")}</span>
      </label>
    </li>`).join("");
  $("file-picker-note").hidden = true;
  $("file-picker").hidden = false;
  return new Promise((resolve) => {
    filePickResolve = resolve;
  });
}

async function startDownload(payload, statusEl) {
  setStatus(statusEl, "正在读取文件列表…");
  const preview = await api("/api/downloads/files", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (preview.mode === "choose") {
    const indexes = await askFiles(preview.files || []);
    if (!indexes) {
      await api(`/api/downloads/files/${encodeURIComponent(preview.token)}`, { method: "DELETE" }).catch(() => {});
      setStatus(statusEl, "已取消");
      return;
    }
    await api("/api/downloads", {
      method: "POST",
      body: JSON.stringify({ ...payload, pick_token: preview.token, file_indexes: indexes }),
    });
  }
  location.hash = "#/queue";
}

$("file-picker-ok").addEventListener("click", () => {
  const indexes = [...$("file-picker-list").querySelectorAll("input:checked")]
    .map((el) => Number(el.dataset.index))
    .filter((n) => Number.isInteger(n));
  if (!indexes.length) {
    setStatus($("file-picker-note"), "请至少选一个文件");
    return;
  }
  closeFilePicker(indexes);
});

$("file-picker-cancel").addEventListener("click", () => closeFilePicker(null));

function setStatus(el, msg, kind) {
  if (!msg) { el.hidden = true; el.textContent = ""; return; }
  el.hidden = false;
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
}

function skeletonCards(n) {
  return Array.from({ length: n }, () => (
    '<div class="skeleton"><div class="sk-img"></div><div class="sk-line"></div><div class="sk-line short"></div></div>'
  )).join("");
}

function showSkeleton(wrapId, listId) {
  $(wrapId).hidden = false;
  $(listId).innerHTML = skeletonCards(12);
}

const FALLBACK_COVER = "/static/placeholder.svg";

document.addEventListener("error", (e) => {
  const img = e.target;
  if (!(img instanceof HTMLImageElement)) return;
  const raw = img.getAttribute("src") || "";
  if (raw === FALLBACK_COVER || raw.endsWith("/placeholder.svg")) return;
  img.dataset.fallback = "1";
  img.classList.add("is-fallback");
  if (raw && img.closest(".lib-cover")) {
    const card = img.closest(".lib-card");
    if (card) delete card.dataset.poster;
  }
  img.src = FALLBACK_COVER;
}, true);

function coverSrc(url) {
  if (!url) return FALLBACK_COVER;
  return "/api/img?url=" + encodeURIComponent(url);
}

function coverImage(url, opts = {}) {
  const missing = !url;
  const classes = [opts.className || "", missing ? "is-fallback" : ""].filter(Boolean);
  const attrs = [
    classes.length ? `class="${classes.join(" ")}"` : "",
    missing ? `data-fallback="1"` : "",
    `src="${coverSrc(url)}"`,
    opts.lazy ? `loading="lazy"` : "",
    opts.full ? `data-full="${escapeHtml(opts.full)}"` : "",
    `alt="${escapeHtml(opts.alt || "")}"`,
  ].filter(Boolean).join(" ");
  return `<img ${attrs} />`;
}

function normalizeActors(actors) {
  return (actors || []).map((a) => (
    typeof a === "string" ? { name: a, photo: "", url: "" } : a
  )).filter((a) => a && a.name);
}

function dlRow(label, value) {
  if (!value) return "";
  return `<dt>${label}</dt><dd>${value}</dd>`;
}

function libraryFlag(lib, suck) {
  const parts = [];
  if (suck) parts.push('<p class="lib-flag suck">suck · 不会再下载</p>');
  if (lib && lib.present) {
    const path = lib.path ? ` · ${escapeHtml(lib.path)}` : "";
    parts.push(`<p class="lib-flag">库里已有${path}</p>`);
  }
  return parts.join("");
}

function downloadLabel(present, suck) {
  if (suck) return "已标 suck";
  if (present) return "下载（库里已有）";
  return "下载";
}

function coverBadge(item) {
  if (item && item.suck) return '<span class="lib-badge suck">suck</span>';
  if (item && item.library && item.library.present) return '<span class="lib-badge">已有</span>';
  return "";
}

function suckActions(kind, key, title, present, suck) {
  if (!key) return "";
  if (suck) {
    return `<p class="suck-actions"><button type="button" class="ghost small" data-suck-clear="${escapeHtml(kind)}" data-key="${escapeHtml(key)}" data-title="${escapeHtml(title || key)}">取消 suck</button></p>`;
  }
  const label = present ? "删掉并标 suck" : "标 suck";
  return `<p class="suck-actions"><button type="button" class="ghost small danger" data-suck-mark="${escapeHtml(kind)}" data-key="${escapeHtml(key)}" data-title="${escapeHtml(title || key)}" data-remove="${present ? "1" : "0"}">${label}</button></p>`;
}

function renderMeta(payload) {
  const card = $("meta-card");
  const meta = payload.metadata;
  lastMeta = payload;
  lastLibrary = payload.library || null;
  lastSuck = !!payload.suck;
  detailCode = payload.code || "";
  const actions = suckActions(
    "jav",
    detailCode,
    (meta && meta.title) || detailCode,
    !!(lastLibrary && lastLibrary.present),
    lastSuck,
  );
  if (!meta) {
    if ((lastLibrary && lastLibrary.present) || lastSuck) {
      card.hidden = false;
      card.innerHTML = libraryFlag(lastLibrary, lastSuck) + actions;
      return;
    }
    card.hidden = true;
    return;
  }
  const actors = normalizeActors(meta.actors);
  const genres = (meta.genres || []).map((g) => `<span class="tag">${escapeHtml(g)}</span>`).join("");
  const actorHtml = actors.length
    ? `<div class="actor-row">${actors.map((a) => `
        <div class="actor-card">
          ${a.photo ? coverImage(a.photo, { alt: a.name }) : `<div class="actor-ph"></div>`}
          <span>${escapeHtml(a.name)}</span>
        </div>`).join("")}</div>`
    : "";
  const samples = meta.samples || [];
  const previewHtml = samples.length
    ? `<div class="preview-row">${samples.map((s) => `
        ${coverImage(s.thumb || s.full, { full: s.full || s.thumb || "", alt: "预览" })}
      `).join("")}</div>`
    : "";
  card.hidden = false;
  card.innerHTML = `
    <div class="meta-main">
      ${coverImage(meta.cover, { className: "cover", full: meta.cover || "" })}
      <div>
        ${libraryFlag(lastLibrary, lastSuck)}
        ${actions}
        <h1><span class="code">${payload.code}</span> ${escapeHtml(meta.title || "")}</h1>
        <dl>
          ${dlRow("发售", escapeHtml(meta.release_date || ""))}
          ${dlRow("时长", escapeHtml(meta.runtime || ""))}
          ${dlRow("导演", escapeHtml(meta.director || ""))}
          ${dlRow("厂家", escapeHtml(meta.studio || ""))}
          ${dlRow("发行", escapeHtml(meta.label || ""))}
          ${dlRow("系列", escapeHtml(meta.series || ""))}
        </dl>
        ${genres ? `<div class="genre-row">${genres}</div>` : ""}
      </div>
    </div>
    ${actorHtml}
    ${previewHtml}`;
}

function tagHtml(item) {
  const tags = [...(item.tags || [])];
  if (item.pack) tags.push("pack");
  if (!tags.length) return "";
  return `<div class="tags">${tags.map((t) => `<span class="tag ${t}">${t}</span>`).join("")}</div>`;
}

function renderWorks(items) {
  const wrap = $("works-wrap");
  const list = $("works-list");
  if (!items || !items.length) {
    wrap.hidden = true;
    list.innerHTML = "";
    return;
  }
  wrap.hidden = false;
  list.innerHTML = items.map((it) => `
    <button type="button" class="work-card${it.library && it.library.present ? " in-library" : ""}${it.suck ? " is-suck" : ""}" data-code="${escapeHtml(it.code)}">
      <span class="work-cover">
        ${coverImage(it.cover, { lazy: true })}
        ${coverBadge(it)}
      </span>
      <span class="code">${escapeHtml(it.code)}</span>
      <span class="work-title">${escapeHtml(it.title || "")}</span>
      <span class="work-date">${escapeHtml(it.release_date || "")}</span>
    </button>`).join("");
}

function renderResources(items) {
  const wrap = $("resources-wrap");
  const list = $("resource-list");
  lastResources = items || [];
  if (!lastResources.length) {
    wrap.hidden = true;
    list.innerHTML = "";
    return;
  }
  wrap.hidden = false;
  list.innerHTML = lastResources.map((it, i) => `
    <article class="res-item${i === 0 ? " top" : ""}">
      <div class="res-rank">${it.rank || i + 1}</div>
      <div class="res-body">
        <div class="res-title">${escapeHtml(it.title || "")}</div>
        <div class="res-meta">
          ${tagHtml(it)}
          <span>${escapeHtml(it.size || "?")}</span>
          <span>热度 ${it.heat ?? 0}</span>
          <span>${escapeHtml(it.date || "")}</span>
        </div>
      </div>
      <div class="res-actions">
        <button type="button" data-dl="${it.info_hash}" data-code="${escapeHtml(detailCode)}"${lastSuck ? " disabled" : ""}>${downloadLabel(lastLibrary && lastLibrary.present, lastSuck)}</button>
        <button type="button" class="ghost" data-copy="${it.info_hash}">复制</button>
      </div>
    </article>`).join("");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function looksLikeCode(q) {
  return /^[a-z]{2,5}-?\d{2,5}$/i.test(String(q).replace(/[\s_]/g, ""));
}

function showBack(on) {
  $("search-back").hidden = !on;
}

function clearDetail() {
  $("meta-card").hidden = true;
  $("meta-card").innerHTML = "";
  $("resources-wrap").hidden = true;
  $("resource-list").innerHTML = "";
  lastResources = [];
  lastLibrary = null;
  lastSuck = false;
  lastMeta = null;
}

function clearWorksView() {
  $("works-wrap").hidden = true;
}

function rememberWorks(query, items) {
  lastWorksQuery = query;
  lastWorks = items || [];
}

function showWorksList() {
  fromWorks = false;
  showBack(false);
  clearDetail();
  $("jav-feed").hidden = false;
  $("code-input").value = lastWorksQuery;
  renderWorks(lastWorks);
  const n = lastWorks.length;
  const latest = javMode === "latest";
  setStatus(
    $("search-status"),
    n ? (latest ? `最新 ${n} 部` : `找到 ${n} 部作品，点一张看磁链`) : (latest ? "没有更多了" : "没有搜到作品"),
    n ? "good" : "bad",
  );
}

function markSeg(id, kind) {
  document.querySelectorAll(`#${id} button`).forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.kind === kind);
  });
}

function renderPager(el, page, lastPage, onPick) {
  const prev = page > 1;
  const next = lastPage ? page < lastPage : true;
  el.innerHTML = "";
  const make = (label, target, enabled) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost";
    btn.textContent = label;
    btn.disabled = !enabled;
    if (enabled) btn.addEventListener("click", () => onPick(target));
    return btn;
  };
  el.append(make("上一页", page - 1, prev));
  const label = document.createElement("span");
  label.textContent = lastPage ? `第 ${page} / ${lastPage} 页` : `第 ${page} 页`;
  el.append(label);
  el.append(make("下一页", page + 1, next));
}

async function loadJavFeed(page) {
  javMode = "latest";
  javPage = page;
  fromWorks = false;
  showBack(false);
  clearDetail();
  $("jav-feed").hidden = false;
  showSkeleton("works-wrap", "works-list");
  setStatus($("search-status"), "加载最新…");
  try {
    const data = await api(`/api/jav/latest?kind=${encodeURIComponent(javKind)}&page=${page}`);
    const items = data.items || [];
    rememberWorks("", items);
    renderWorks(items);
    renderPager($("jav-pager"), page, null, (next) => loadJavFeed(next));
    if (!items.length) {
      $("jav-pager").querySelectorAll("button")[1].disabled = true;
    }
    const n = items.length;
    setStatus(
      $("search-status"),
      data.error || (n ? `最新 ${n} 部` : "没有更多了"),
      n && !data.error ? "good" : "bad",
    );
  } catch (err) {
    clearWorksView();
    setStatus($("search-status"), err.message, "bad");
  }
}

function ensureJavLatest() {
  if (javBootstrapped) return;
  javBootstrapped = true;
  loadJavFeed(1);
}

async function runCodeSearch(code, { fromList = false } = {}) {
  fromWorks = fromList;
  showBack(fromList && lastWorks.length > 0);
  if (fromList) $("jav-feed").hidden = true;
  if (fromList) {
    $("works-wrap").hidden = true;
  } else {
    clearWorksView();
  }
  const [meta, res] = await Promise.allSettled([
    api("/api/search?q=" + encodeURIComponent(code)),
    api("/api/resources?code=" + encodeURIComponent(code)),
  ]);
  let msg = "";
  let kind = "";
  if (meta.status === "fulfilled") {
    if (meta.value.mode === "keyword") {
      rememberWorks(code, meta.value.items || []);
      fromWorks = false;
      showBack(false);
      clearDetail();
      $("jav-feed").hidden = false;
      javMode = "search";
      $("jav-pager").innerHTML = "";
      renderWorks(lastWorks);
      const n = lastWorks.length;
      msg = meta.value.error || (n ? `找到 ${n} 部作品，点一张看磁链` : "没有搜到作品");
      kind = n ? "good" : "bad";
      setStatus($("search-status"), msg, kind);
      return;
    }
    renderMeta(meta.value);
    if (meta.value.error) msg = "元数据：" + meta.value.error;
  } else {
    msg = "元数据失败：" + meta.reason.message;
    kind = "bad";
  }
  if (res.status === "fulfilled") {
    const items = res.value.items || [];
    renderResources(items);
    if (res.value.error) {
      msg = (msg ? msg + "；" : "") + res.value.error;
      kind = "bad";
    } else if (!items.length) {
      msg = (msg ? msg + "；" : "") + "没有搜到磁链";
      kind = "bad";
    } else if (!msg) {
      msg = `找到 ${items.length} 条磁链`;
      kind = "good";
    }
  } else {
    msg = (msg ? msg + "；" : "") + "磁链搜索失败：" + res.reason.message;
    kind = "bad";
  }
  setStatus($("search-status"), msg, kind);
}

$("search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("code-input").value.trim();
  if (!q) return;
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  setStatus($("search-status"), "查询中…");
  javMode = "search";
  $("jav-pager").innerHTML = "";
  fromWorks = false;
  showBack(false);
  clearDetail();
  clearWorksView();
  try {
    if (looksLikeCode(q)) {
      await runCodeSearch(q, { fromList: false });
      return;
    }
    const data = await api("/api/search?q=" + encodeURIComponent(q));
    if (data.mode === "code") {
      await runCodeSearch(data.code, { fromList: false });
      return;
    }
    const items = data.items || [];
    rememberWorks(q, items);
    javMode = "search";
    history.replaceState({ javdl: "works", q }, "", location.hash || "#/");
    renderWorks(items);
    setStatus(
      $("search-status"),
      data.error || (items.length ? `找到 ${items.length} 部作品，点一张看磁链` : "没有搜到作品"),
      items.length ? "good" : "bad",
    );
  } catch (err) {
    setStatus($("search-status"), err.message, "bad");
  } finally {
    btn.disabled = false;
  }
});

$("jav-kind").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-kind]");
  if (!btn) return;
  javKind = btn.dataset.kind;
  markSeg("jav-kind", javKind);
  loadJavFeed(1);
});

$("jav-latest").addEventListener("click", () => loadJavFeed(1));

$("works-list").addEventListener("click", (e) => {
  const card = e.target.closest("[data-code]");
  if (!card) return;
  const code = card.dataset.code;
  $("code-input").value = code;
  history.pushState({ javdl: "code", code, q: lastWorksQuery }, "", location.hash || "#/");
  runCodeSearch(code, { fromList: true });
});

$("back-to-works").addEventListener("click", () => {
  if (history.state && history.state.javdl === "code") {
    history.back();
    return;
  }
  showWorksList();
});

window.addEventListener("popstate", (e) => {
  const hash = location.hash.replace("#/", "") || "";
  if (hash.startsWith("queue") || hash.startsWith("settings") || hash.startsWith("western") || hash.startsWith("library") || hash.startsWith("follow")) return;
  if (e.state && e.state.javdl === "code") {
    $("code-input").value = e.state.code || "";
    runCodeSearch(e.state.code, { fromList: true });
    return;
  }
  if (e.state && e.state.javdl === "works" && lastWorks.length) {
    showWorksList();
  }
});

let batchPreview = [];

function renderBatch(items) {
  batchPreview = items || [];
  const box = $("batch-preview");
  const list = $("batch-list");
  const btn = $("batch-confirm");
  if (!batchPreview.length) {
    box.hidden = true;
    list.innerHTML = "";
    btn.disabled = true;
    return;
  }
  box.hidden = false;
  const ready = batchPreview.filter((row) => row.item && row.item.info_hash);
  list.innerHTML = batchPreview.map((row) => {
    const item = row.item;
    if (!item) {
      return `<li><span class="code">${escapeHtml(row.code)}</span><span class="miss">${escapeHtml(row.error || "没有磁链")}</span></li>`;
    }
    const tags = (item.tags || []).map((tag) => `<span class="tag ${escapeHtml(tag)}">${escapeHtml(tag)}</span>`).join("");
    return `<li>
      <span class="code">${escapeHtml(row.code)}</span>
      <span>
        <span class="title">${escapeHtml(item.title || "")}</span>
        <span class="res-meta">${escapeHtml(item.size || "")}${tags}</span>
      </span>
    </li>`;
  }).join("");
  btn.disabled = ready.length === 0;
  btn.textContent = ready.length ? `确认入队（${ready.length}）` : "没有可入队的番号";
}

$("batch-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  setStatus($("batch-status"), "正在查磁链…");
  try {
    const data = await api("/api/downloads/batch/preview", {
      method: "POST",
      body: JSON.stringify({ text: $("batch-input").value }),
    });
    const items = data.items || [];
    renderBatch(items);
    const miss = items.filter((row) => !row.item).length;
    const note = `识别 ${items.length} 个番号` + (miss ? `，${miss} 个没有磁链` : "");
    setStatus($("batch-status"), note, miss ? "" : "good");
  } catch (err) {
    renderBatch([]);
    setStatus($("batch-status"), err.message, "bad");
  } finally {
    btn.disabled = false;
  }
});

$("batch-confirm").addEventListener("click", async () => {
  const ready = batchPreview.filter((row) => row.item && row.item.info_hash);
  const missed = batchPreview.filter((row) => !row.item);
  if (!ready.length) return;
  const btn = $("batch-confirm");
  btn.disabled = true;
  try {
    const data = await api("/api/downloads/batch", {
      method: "POST",
      body: JSON.stringify({
        items: ready.map((row) => ({
          code: row.code,
          info_hash: row.item.info_hash,
          title: row.item.title || "",
        })),
      }),
    });
    const skipped = [
      ...missed.map((row) => `${row.code} ${row.error || "没有磁链"}`),
      ...(data.skipped || []).map((row) => `${row.code} ${row.reason || "跳过"}`),
    ];
    const queued = (data.queued || []).length;
    const lines = [`已入队 ${queued} 个`];
    if (skipped.length) lines.push(`跳过：${skipped.join("；")}`);
    setStatus($("batch-status"), lines.join("。"), queued ? "good" : "bad");
  } catch (err) {
    setStatus($("batch-status"), err.message, "bad");
  } finally {
    const still = batchPreview.filter((row) => row.item && row.item.info_hash).length;
    btn.disabled = still === 0;
  }
});

$("resource-list").addEventListener("click", async (e) => {
  const dl = e.target.closest("[data-dl]");
  const copy = e.target.closest("[data-copy]");
  const hash = (dl || copy)?.dataset.dl || copy?.dataset.copy;
  if (!hash) return;
  const item = lastResources.find((x) => x.info_hash === hash);
  if (!item) return;
  if (copy) {
    try {
      await navigator.clipboard.writeText(item.magnet);
      copy.textContent = "已复制";
      setTimeout(() => { copy.textContent = "复制"; }, 1200);
    } catch {
      prompt("磁链", item.magnet);
    }
    return;
  }
  if (lastSuck) {
    setStatus($("search-status"), "已标 suck，不会再下载", "bad");
    return;
  }
  dl.disabled = true;
  try {
    await startDownload({
      code: dl.dataset.code || detailCode,
      info_hash: item.info_hash,
      title: item.title,
    }, $("search-status"));
  } catch (err) {
    setStatus($("search-status"), err.message, "bad");
  } finally {
    dl.disabled = false;
  }
});

function scrapeLine(j) {
  if (j.scrape_status === "archived" && j.archive_path) {
    return `<p class="status good">已归档 ${escapeHtml(j.archive_path)}</p>`;
  }
  if (j.scrape_status === "scraping") {
    return `<p class="status">正在刮削</p>`;
  }
  if (j.scrape_status === "waiting") {
    return `<p class="status">等待刮削</p>`;
  }
  if (j.scrape_status === "error" && j.scrape_error) {
    return `<p class="status bad">刮削失败：${escapeHtml(j.scrape_error)}</p>`;
  }
  if (j.scrape_status === "skipped") {
    return `<p class="status">不刮削</p>`;
  }
  return "";
}

function downloadError(j) {
  if (!j.error) return "";
  const text = String(j.error);
  const line = text.startsWith("下载失败") ? text : `下载失败：${text}`;
  return `<p class="status bad">${escapeHtml(line)}</p>`;
}

function queueMatches(j) {
  if (queueFilter === "active") return ["queued", "waiting", "active", "downloading", "paused"].includes(j.status);
  if (queueFilter === "complete") return j.status === "complete";
  if (queueFilter === "error") return j.status === "error";
  if (queueFilter === "cancelled") return j.status === "cancelled";
  return true;
}

function queueActions(j) {
  const terminal = ["complete", "error", "cancelled"].includes(j.status);
  const buttons = [];
  if (j.status === "paused") {
    buttons.push(`<button data-act="resume" data-id="${j.id}">继续</button>`);
  } else if (!terminal) {
    buttons.push(`<button class="ghost" data-act="pause" data-id="${j.id}">暂停</button>`);
  }
  if (!terminal) {
    buttons.push(`<button class="ghost" data-act="cancel" data-id="${j.id}">取消</button>`);
  }
  if (j.status === "complete" && j.scrape_status === "error") {
    buttons.push(`<button data-act="rescrape" data-id="${j.id}">重新刮削</button>`);
  }
  if (terminal) {
    buttons.push(`<button class="ghost" data-act="delete" data-id="${j.id}">删除</button>`);
  }
  return buttons.join("");
}

function renderQueue() {
  const list = $("queue-list");
  const empty = $("queue-empty");
  const items = queueItems.filter(queueMatches);
  if (!items.length) {
    empty.hidden = false;
    empty.textContent = queueItems.length ? "这个状态下没有任务" : "还没有任务";
    list.innerHTML = "";
    return;
  }
  empty.hidden = true;
  list.innerHTML = items.map((j) => `
    <li>
      <div class="row">
        <strong><span class="code">${escapeHtml(j.code)}</span>${escapeHtml(j.title)}</strong>
        <span class="state s-${escapeHtml(j.status)}">${escapeHtml(STATUS_LABEL[j.status] || j.status)}</span>
      </div>
      <div class="bar"><span style="width:${Math.min(100, j.progress || 0)}%"></span></div>
      <div class="meta-line">
        <span>${j.progress || 0}%</span>
        <span>${escapeHtml(j.downloaded)} / ${escapeHtml(j.total)}</span>
        <span>${escapeHtml(j.speed)}</span>
        <span>${j.eta ? "ETA " + escapeHtml(j.eta) : ""}</span>
        <span>${j.seeders ? j.seeders + " 种子" : ""}</span>
      </div>
      ${downloadError(j)}
      ${scrapeLine(j)}
      <div class="row-actions">${queueActions(j)}</div>
    </li>`).join("");
}

function applyQueuePayload(data) {
  queueItems = data.items || [];
  renderQueue();
  const follow = $("queue-follow");
  const unread = Number(data.follow_unread || 0);
  if (unread > 0) {
    follow.hidden = false;
    follow.innerHTML = `<a href="#/follow">追更有 ${unread} 条新作</a>`;
  } else {
    follow.hidden = true;
    follow.innerHTML = "";
  }
}

async function refreshQueue() {
  try {
    applyQueuePayload(await api("/api/downloads"));
  } catch (err) {
    $("queue-list").innerHTML = `<li class="status bad">${escapeHtml(err.message)}</li>`;
  }
}

function stopQueueStream() {
  const source = queueSource;
  queueSource = null;
  if (source) source.close();
  clearTimeout(queueRetry);
  queueRetry = null;
  clearInterval(queueSlow);
  queueSlow = null;
}

function startQueueSlow() {
  if (queueSlow || document.hidden || views.queue.hidden) return;
  queueSlow = setInterval(() => {
    if (!views.queue.hidden && !document.hidden) refreshQueue();
  }, 30000);
}

function startQueueStream() {
  if (document.hidden || views.queue.hidden || queueSource) return;
  clearTimeout(queueRetry);
  queueRetry = null;
  if (typeof EventSource === "undefined") {
    startQueueSlow();
    return;
  }
  const source = new EventSource("/api/downloads/events");
  queueSource = source;
  source.onmessage = (event) => {
    sseFailures = 0;
    clearInterval(queueSlow);
    queueSlow = null;
    try {
      applyQueuePayload(JSON.parse(event.data));
    } catch {
      /* ignore a bad event */
    }
  };
  source.onerror = () => {
    if (queueSource !== source) return;
    source.close();
    queueSource = null;
    if (document.hidden || views.queue.hidden) return;
    sseFailures += 1;
    if (sseFailures >= 2) startQueueSlow();
    queueRetry = setTimeout(startQueueStream, sseFailures >= 2 ? 60000 : 5000);
  };
}

$("queue-filter").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-filter]");
  if (!btn) return;
  queueFilter = btn.dataset.filter;
  for (const child of $("queue-filter").querySelectorAll("button")) {
    child.classList.toggle("on", child === btn);
  }
  renderQueue();
});

async function clearQueue(status, message) {
  if (!confirm(message)) return;
  const data = await api("/api/downloads/clear", {
    method: "POST",
    body: JSON.stringify({ status }),
  });
  setStatus($("queue-note"), `已删除 ${data.deleted} 条`, "good");
  await refreshQueue();
}

$("queue-clear-complete").addEventListener("click", () => {
  clearQueue("complete", "删除所有已完成的记录？下载文件不会动。").catch((err) => {
    setStatus($("queue-note"), err.message, "bad");
  });
});

$("queue-clear-finished").addEventListener("click", () => {
  clearQueue("finished", "删除已完成、失败和已取消的记录？进行中的任务会留下。").catch((err) => {
    setStatus($("queue-note"), err.message, "bad");
  });
});

$("queue-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  btn.disabled = true;
  try {
    if (btn.dataset.act === "delete") {
      await api(`/api/downloads/${btn.dataset.id}`, { method: "DELETE" });
    } else {
      await api(`/api/downloads/${btn.dataset.id}/${btn.dataset.act}`, { method: "POST" });
    }
    await refreshQueue();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
});

const FOLLOW_KIND = {
  actress: "女优",
  series: "系列",
  studio: "片商",
  western_performer: "欧美演员",
  western_studio: "欧美片商",
};
const HIT_STATUS = {
  new: "新作",
  queued: "已入队",
  no_magnet: "没有符合规则的磁链",
};

function timeAgo(ts) {
  if (!ts) return "还没检查";
  const mins = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  if (mins < 1) return "刚刚检查";
  if (mins < 60) return `${mins} 分钟前检查`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} 小时前检查`;
  return `${Math.round(mins / 60 / 24)} 天前检查`;
}

function followRules(sub) {
  if (!sub.auto) return "只提醒";
  const rules = [];
  if (sub.want_uc) rules.push("无码破解");
  if (sub.want_c) rules.push("中字");
  if (sub.max_gb) rules.push(`≤ ${sub.max_gb} GB`);
  return rules.length ? `自动下载 · ${rules.join(" / ")}` : "自动下载";
}

function renderFollow(data) {
  const items = data.items || [];
  const kindOf = Object.fromEntries(items.map((sub) => [sub.id, sub.kind]));
  $("follow-count").textContent = items.length ? `${items.length} 个` : "";
  $("follow-none").hidden = items.length > 0;
  $("follow-list").innerHTML = items.map((sub) => `
    <article class="follow-sub">
      <div class="row">
        <strong class="title"><span class="chip">${escapeHtml(FOLLOW_KIND[sub.kind] || sub.kind)}</span>${escapeHtml(sub.name)}</strong>
        <span class="mode${sub.auto ? " on" : ""}">${escapeHtml(followRules(sub))}</span>
      </div>
      ${sub.target ? `<p class="path">${escapeHtml(sub.target)}</p>` : ""}
      ${sub.last_error ? `<p class="status bad">${escapeHtml(sub.last_error)}</p>` : ""}
      <div class="row foot">
        <span class="muted">${escapeHtml(timeAgo(sub.last_check))}</span>
        <span class="row-actions">
          <button type="button" class="ghost small" data-follow-check="${sub.id}">检查</button>
          <button type="button" class="ghost small danger" data-follow-del="${sub.id}">删除</button>
        </span>
      </div>
    </article>`).join("");
  const hits = data.hits || [];
  const unread = hits.filter((hit) => !hit.seen).length;
  $("follow-hit-count").textContent = hits.length ? (unread ? `${unread} 条未读` : `${hits.length} 条`) : "";
  $("follow-empty").hidden = hits.length > 0;
  $("follow-hits").innerHTML = hits.map((hit) => {
    const jav = !String(kindOf[hit.sub_id] || "").startsWith("western");
    const code = jav
      ? `<a class="code" href="#/" data-hit-code="${escapeHtml(hit.code)}">${escapeHtml(hit.code)}</a>`
      : `<span class="code">${escapeHtml(hit.code)}</span>`;
    return `
    <li class="${hit.seen ? "" : "unread"}">
      <div class="row">
        <span class="title">${code} ${escapeHtml(hit.title || "")}</span>
        <span class="state h-${escapeHtml(hit.status)}">${escapeHtml(HIT_STATUS[hit.status] || hit.status)}</span>
      </div>
      ${hit.detail ? `<p class="hint">${escapeHtml(hit.detail)}</p>` : ""}
      ${hit.seen ? "" : `<button type="button" class="ghost small" data-hit-read="${hit.id}">知道了</button>`}
    </li>`;
  }).join("");
}

async function loadFollow() {
  try {
    renderFollow(await api("/api/subscriptions"));
  } catch (err) {
    setStatus($("follow-status"), err.message, "bad");
  }
}

$("follow-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const btn = form.querySelector("button[type='submit']");
  btn.disabled = true;
  setStatus($("follow-status"), "正在记下现有作品…");
  try {
    const data = await api("/api/subscriptions", {
      method: "POST",
      body: JSON.stringify({
        kind: form.kind.value,
        name: form.name.value.trim(),
        target: form.target.value.trim(),
        auto: form.auto.checked,
        want_uc: form.want_uc.checked,
        want_c: form.want_c.checked,
        max_gb: Number(form.max_gb.value || 0),
      }),
    });
    const check = data.check || {};
    const note = check.error
      ? check.error
      : (check.first ? `已记下当前 ${check.known || 0} 部，之后的新作才会提醒` : `新增 ${check.added || 0} 条`);
    setStatus($("follow-status"), note, check.error ? "bad" : "good");
    form.name.value = "";
    form.target.value = "";
    if (!check.error) $("follow-add").open = false;
    await loadFollow();
  } catch (err) {
    setStatus($("follow-status"), err.message, "bad");
  } finally {
    btn.disabled = false;
  }
});

$("follow-check").addEventListener("click", async () => {
  setStatus($("follow-status"), "正在检查…");
  try {
    await api("/api/subscriptions/check", { method: "POST", body: JSON.stringify({ id: "" }) });
    setStatus($("follow-status"), "检查完了", "good");
    await loadFollow();
  } catch (err) {
    setStatus($("follow-status"), err.message, "bad");
  }
});

$("follow-list").addEventListener("click", async (e) => {
  const check = e.target.closest("[data-follow-check]");
  const del = e.target.closest("[data-follow-del]");
  try {
    if (check) {
      setStatus($("follow-status"), "正在检查…");
      await api("/api/subscriptions/check", {
        method: "POST",
        body: JSON.stringify({ id: check.dataset.followCheck }),
      });
      setStatus($("follow-status"), "检查完了", "good");
    } else if (del) {
      await api(`/api/subscriptions/${del.dataset.followDel}`, { method: "DELETE" });
    } else {
      return;
    }
    await loadFollow();
  } catch (err) {
    setStatus($("follow-status"), err.message, "bad");
  }
});

$("follow-hits").addEventListener("click", async (e) => {
  const link = e.target.closest("[data-hit-code]");
  if (link) {
    e.preventDefault();
    openCodeDetail(link.dataset.hitCode);
    return;
  }
  const btn = e.target.closest("[data-hit-read]");
  if (!btn) return;
  try {
    await api(`/api/subscriptions/hits/${btn.dataset.hitRead}/read`, { method: "POST" });
    await loadFollow();
  } catch (err) {
    setStatus($("follow-status"), err.message, "bad");
  }
});

function toggleXunleiFields() {
  const on = $("downloader-select").value === "xunlei";
  $("xunlei-fields").hidden = !on;
}

function toggleNotifyFields() {
  const channel = $("notify-channel").value;
  $("notify-telegram").hidden = channel !== "telegram";
  $("notify-bark").hidden = channel !== "bark";
  $("notify-serverchan").hidden = channel !== "serverchan";
}

async function loadSettings() {
  const s = await api("/api/settings");
  const form = $("settings-form");
  form.proxy_enabled.checked = !!s.proxy_enabled;
  form.proxy_url.value = s.proxy_url || "";
  form.javbus_base.value = s.javbus_base || "";
  form.clm_home.value = s.clm_home || "";
  form.clm_search.value = s.clm_search || "";
  form.clm_search_backup.value = s.clm_search_backup || "";
  form.tpdb_api_key.value = "";
  form.tpdb_api_key.placeholder = s.tpdb_api_key_set ? "已保存，留空不改" : "";
  form.downloader.value = s.downloader === "xunlei" ? "xunlei" : "aria2";
  form.xunlei_url.value = s.xunlei_url || "";
  form.xunlei_username.value = s.xunlei_username || "";
  form.xunlei_password.value = "";
  form.xunlei_password.placeholder = s.xunlei_password_set ? "已保存，留空不改" : "";
  form.xunlei_device_name.value = s.xunlei_device_name || "";
  form.scrape_enabled.checked = s.scrape_enabled !== false;
  form.media_dir.value = s.media_dir || "";
  form.western_media_dir.value = s.western_media_dir || "";
  form.scrape_settle_seconds.value = s.scrape_settle_seconds ?? "";
  form.scrape_min_mb.value = s.scrape_min_mb ?? "";
  form.notify_channel.value = s.notify_channel || "";
  form.notify_telegram_token.value = "";
  form.notify_telegram_token.placeholder = s.notify_telegram_token_set ? "已保存，留空不改" : "";
  form.notify_telegram_chat.value = s.notify_telegram_chat || "";
  form.notify_bark_url.value = "";
  form.notify_bark_url.placeholder = s.notify_bark_set ? "已保存，留空不改" : "https://api.day.app/你的key";
  form.notify_serverchan_key.value = "";
  form.notify_serverchan_key.placeholder = s.notify_serverchan_set ? "已保存，留空不改" : "";
  toggleXunleiFields();
  toggleNotifyFields();
  renderPanelLinks(s);
  $("download-dir").textContent = "下载目录（只读，由运行环境决定）：" + (s.download_dir || "");
  $("tls-status").textContent = s.verify_tls
    ? "查站证书校验：开"
    : "查站证书校验：关。默认保持关闭，要打开请设环境变量 VERIFY_TLS=true。";
  const panelById = Object.fromEntries((s.panels || []).map((item) => [item.id, item.url]));
  try {
    const h = await api("/api/health");
    $("health-box").innerHTML = [
      ["下载器", { ok: true, version: h.downloader || "?" }],
      ["迅雷", h.xunlei, null, "xunlei"],
      ["aria2", h.aria2, null, "aria2"],
      ["JavBus", h.javbus],
      ["磁力猫", h.clm],
      ["ThePornDB", h.tpdb, "token"],
    ].map(([name, x, mode, panelId]) => {
      const ok = mode === "token" ? !!(x && (x.configured || x.ok)) : !!(x && x.ok);
      const label = mode === "token" ? (ok ? "已配置" : "未填写") : (ok ? "正常" : "不通");
      const href = panelId ? panelById[panelId] : "";
      const jump = href
        ? `<a class="jump" href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">打开</a>`
        : "";
      return `
      <div class="pill">
        <span>${name}</span>
        <span class="pill-side">${jump}<span class="dot ${ok ? "ok" : "no"}">${label}${x && x.version ? " · " + escapeHtml(x.version) : ""}</span></span>
      </div>`;
    }).join("");
  } catch {
    $("health-box").innerHTML = "";
  }
}

$("downloader-select").addEventListener("change", toggleXunleiFields);
$("notify-channel").addEventListener("change", toggleNotifyFields);

$("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const status = $("settings-status");
  const body = {
    proxy_enabled: form.proxy_enabled.checked,
    proxy_url: form.proxy_url.value.trim(),
    javbus_base: form.javbus_base.value.trim(),
    clm_home: form.clm_home.value.trim(),
    clm_search: form.clm_search.value.trim(),
    clm_search_backup: form.clm_search_backup.value.trim(),
    tpdb_api_key: form.tpdb_api_key.value.trim(),
    downloader: form.downloader.value,
    xunlei_url: form.xunlei_url.value.trim(),
    xunlei_username: form.xunlei_username.value.trim(),
    xunlei_device_name: form.xunlei_device_name.value.trim(),
    scrape_enabled: form.scrape_enabled.checked,
    media_dir: form.media_dir.value.trim(),
  };
  const settle = form.scrape_settle_seconds.value.trim();
  if (settle !== "") body.scrape_settle_seconds = Number(settle);
  const minMb = form.scrape_min_mb.value.trim();
  if (minMb !== "") body.scrape_min_mb = Number(minMb);
  const westernDir = form.western_media_dir.value.trim();
  if (westernDir) body.western_media_dir = westernDir;
  const pw = form.xunlei_password.value;
  if (pw) body.xunlei_password = pw;
  if (!body.tpdb_api_key) delete body.tpdb_api_key;
  body.notify_channel = form.notify_channel.value;
  body.notify_telegram_chat = form.notify_telegram_chat.value.trim();
  const telegramToken = form.notify_telegram_token.value.trim();
  if (telegramToken) body.notify_telegram_token = telegramToken;
  const bark = form.notify_bark_url.value.trim();
  if (bark) body.notify_bark_url = bark;
  const serverchan = form.notify_serverchan_key.value.trim();
  if (serverchan) body.notify_serverchan_key = serverchan;
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    });
    setStatus(status, "已保存", "good");
    await loadSettings();
  } catch (err) {
    setStatus(status, err.message, "bad");
  }
});

function showLightbox(src) {
  if (!src) return;
  const box = $("lightbox");
  box.querySelector("img").src = src;
  box.hidden = false;
}

function openLightbox(url) {
  if (url) showLightbox(coverSrc(url));
}

$("meta-card").addEventListener("click", (e) => {
  const suck = e.target.closest("[data-suck-mark], [data-suck-clear]");
  if (suck) {
    commitSuck(suck);
    return;
  }
  const img = e.target.closest("img[data-full]");
  if (!img) return;
  openLightbox(img.dataset.full || img.getAttribute("data-full"));
});

$("lightbox").addEventListener("click", () => {
  $("lightbox").hidden = true;
  $("lightbox").querySelector("img").src = "";
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    $("lightbox").hidden = true;
  }
});

function renderWesternWorks(items) {
  const wrap = $("western-works-wrap");
  const list = $("western-works");
  westernItems = items || [];
  if (!westernItems.length) {
    wrap.hidden = true;
    list.innerHTML = "";
    return;
  }
  wrap.hidden = false;
  list.innerHTML = westernItems.map((it) => {
    const when = [it.date, it.duration ? `${it.duration} 分钟` : ""].filter(Boolean).join(" · ");
    return `
    <button type="button" class="work-card${it.library && it.library.present ? " in-library" : ""}${it.suck ? " is-suck" : ""}" data-id="${escapeHtml(it.id)}" data-kind="${escapeHtml(it.kind || westernKind)}">
      <span class="work-cover">
        ${coverImage(it.cover, { lazy: true })}
        ${coverBadge(it)}
      </span>
      <span class="work-site">${escapeHtml(it.site || "")}</span>
      <span class="work-title">${escapeHtml(it.title || "")}</span>
      <span class="work-people">${escapeHtml((it.performers || []).join("、"))}</span>
      <span class="work-date">${escapeHtml(when)}</span>
    </button>`;
  }).join("");
}

function renderWesternMeta(item) {
  const card = $("western-meta");
  westernCurrent = item;
  if (!item) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }
  const tags = (item.tags || []).map((g) => `<span class="tag">${escapeHtml(g)}</span>`).join("");
  const people = (item.performers || []).join("、");
  card.hidden = false;
  card.innerHTML = `
    <div class="meta-main">
      ${coverImage(item.cover || item.background, { className: "cover", full: item.background || item.cover || "" })}
      <div>
        ${libraryFlag(item.library, item.suck)}
        ${suckActions("western", item.id || "", item.title || "", !!(item.library && item.library.present), !!item.suck)}
        <h1>${escapeHtml(item.title || "")}</h1>
        <dl>
          ${dlRow("片商", escapeHtml(item.site || ""))}
          ${dlRow("日期", escapeHtml(item.date || ""))}
          ${dlRow("时长", item.duration ? escapeHtml(item.duration + " 分钟") : "")}
          ${dlRow("演员", escapeHtml(people))}
        </dl>
        ${tags ? `<div class="genre-row">${tags}</div>` : ""}
        ${item.description ? `<p class="summary">${escapeHtml(item.description)}</p>` : ""}
      </div>
    </div>`;
}

function renderWesternResources(items) {
  const wrap = $("western-resources-wrap");
  const list = $("western-resources");
  westernResources = items || [];
  if (!westernResources.length) {
    wrap.hidden = true;
    list.innerHTML = "";
    return;
  }
  wrap.hidden = false;
  list.innerHTML = westernResources.map((it, i) => `
    <article class="res-item${i === 0 ? " top" : ""}">
      <div class="res-rank">${it.rank || i + 1}</div>
      <div class="res-body">
        <div class="res-title">${escapeHtml(it.title || "")}</div>
        <div class="res-meta">
          <span>${escapeHtml(it.size || "?")}</span>
          <span>热度 ${it.heat ?? 0}</span>
          <span>${it.release_date ? "发行 " + escapeHtml(it.release_date) : "发行日未知"}</span>
          <span>${it.date ? "收录 " + escapeHtml(it.date) : ""}</span>
        </div>
      </div>
      <div class="res-actions">
        <button type="button" data-west-dl="${it.info_hash}"${westernCurrent && westernCurrent.suck ? " disabled" : ""}>${downloadLabel(westernCurrent && westernCurrent.library && westernCurrent.library.present, westernCurrent && westernCurrent.suck)}</button>
        <button type="button" class="ghost" data-west-copy="${it.info_hash}">复制</button>
      </div>
    </article>`).join("");
}

function showWesternList() {
  $("western-back").hidden = true;
  $("western-meta").hidden = true;
  $("western-resources-wrap").hidden = true;
  $("western-feed").hidden = false;
  $("western-themes").hidden = false;
  renderWesternWorks(westernItems);
  const n = westernItems.length;
  const latest = westernMode === "latest";
  setStatus(
    $("western-status"),
    n ? (latest ? `最新 ${n} 部` : `找到 ${n} 部`) : (latest ? "没有更多了" : "没有搜到作品"),
    n ? "good" : "bad",
  );
}

function westernParams(page, q) {
  const params = new URLSearchParams();
  params.set("kind", westernKind);
  params.set("page", String(page));
  if (q) params.set("q", q);
  if (westernTheme) params.set("theme", westernTheme);
  return params.toString();
}

function markThemes() {
  document.querySelectorAll("#western-themes button").forEach((btn) => {
    btn.classList.toggle("on", (btn.dataset.theme || "") === westernTheme);
  });
}

async function loadWesternFeed(page) {
  westernMode = "latest";
  westernPage = page;
  $("western-back").hidden = true;
  $("western-meta").hidden = true;
  $("western-resources-wrap").hidden = true;
  $("western-feed").hidden = false;
  $("western-themes").hidden = false;
  showSkeleton("western-works-wrap", "western-works");
  setStatus($("western-status"), "加载最新…");
  try {
    const data = await api(`/api/western/latest?${westernParams(page)}`);
    westernItems = data.items || [];
    renderWesternWorks(westernItems);
    renderPager($("western-pager"), data.page || page, data.last_page || page, (next) => loadWesternFeed(next));
    const n = westernItems.length;
    setStatus(
      $("western-status"),
      data.error || (n ? `最新 ${n} 部` : "没有更多了"),
      n && !data.error ? "good" : "bad",
    );
  } catch (err) {
    $("western-works-wrap").hidden = true;
    setStatus($("western-status"), err.message, "bad");
  }
}

function ensureWesternLatest() {
  if (westernBootstrapped) return;
  westernBootstrapped = true;
  loadWesternFeed(1);
}

async function openWestern(id, kind, known = null) {
  const listed = known || westernItems.find((it) => String(it.id) === String(id)) || null;
  westernCurrent = listed;
  $("western-works-wrap").hidden = true;
  $("western-feed").hidden = true;
  $("western-themes").hidden = true;
  $("western-back").hidden = false;
  renderWesternMeta(listed);
  renderWesternResources([]);
  setStatus($("western-status"), "查询详情和磁链…");
  const magnetParams = new URLSearchParams();
  if (listed && listed.site) magnetParams.set("site", listed.site);
  if (listed && listed.title) magnetParams.set("title", listed.title);
  if (listed && listed.date) magnetParams.set("date", listed.date);
  if (listed && listed.performers && listed.performers.length) {
    magnetParams.set("performers", listed.performers.slice(0, 3).join(","));
  }
  const magnetPath = magnetParams.toString();
  const [detail, magnets] = await Promise.allSettled([
    api(`/api/western/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`),
    magnetPath ? api("/api/resources?" + magnetPath) : Promise.resolve({ items: [] }),
  ]);
  let msg = "";
  let tone = "";
  if (detail.status === "fulfilled") {
    if (detail.value.item) {
      const item = detail.value.item;
      renderWesternMeta({
        ...listed,
        ...item,
        library: item.library || (listed && listed.library) || null,
        suck: !!item.suck,
        kind,
      });
    }
    if (detail.value.error) {
      msg = detail.value.error;
      tone = "bad";
    }
  } else {
    msg = "详情失败：" + detail.reason.message;
    tone = "bad";
  }
  if (magnets.status === "fulfilled") {
    const items = magnets.value.items || [];
    renderWesternResources(items);
    if (magnets.value.error) {
      msg = (msg ? msg + "；" : "") + magnets.value.error;
      tone = "bad";
    } else if (!items.length) {
      msg = (msg ? msg + "；" : "") + "没有搜到磁链";
      tone = "bad";
    } else if (!msg) {
      const matched = magnets.value.matched;
      const when = (listed && listed.date) || "";
      if (matched === "date") {
        msg = `按片商和发行日${when ? " " + when : ""} 找到 ${items.length} 条`;
        tone = "good";
      } else if (matched === "title") {
        msg = `这个发行日没有同日种子，下面 ${items.length} 条是片名或演员对得上的`;
        tone = "good";
      } else {
        msg = "磁力猫里没有这个发行日的磁链";
        tone = "bad";
      }
    }
  } else {
    msg = (msg ? msg + "；" : "") + "磁链搜索失败：" + magnets.reason.message;
    tone = "bad";
  }
  setStatus($("western-status"), msg, tone);
}

async function loadWesternSearch(q, page) {
  westernMode = "search";
  westernPage = page;
  $("western-back").hidden = true;
  $("western-meta").hidden = true;
  $("western-resources-wrap").hidden = true;
  $("western-feed").hidden = false;
  $("western-themes").hidden = false;
  setStatus($("western-status"), "查询中…");
  const data = await api(`/api/western/search?${westernParams(page, q)}`);
  westernItems = data.items || [];
  renderWesternWorks(westernItems);
  renderPager(
    $("western-pager"),
    data.page || page,
    data.last_page || page,
    (next) => loadWesternSearch(q, next).catch((err) => {
      setStatus($("western-status"), err.message, "bad");
    }),
  );
  setStatus(
    $("western-status"),
    data.error || (westernItems.length ? `找到 ${westernItems.length} 部` : "没有搜到作品"),
    westernItems.length && !data.error ? "good" : "bad",
  );
}

$("western-kind").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-kind]");
  if (!btn) return;
  westernKind = btn.dataset.kind;
  markSeg("western-kind", westernKind);
  if (westernMode === "search") {
    $("western-form").requestSubmit();
    return;
  }
  loadWesternFeed(1);
});

$("western-latest").addEventListener("click", () => {
  $("western-input").value = "";
  loadWesternFeed(1);
});

$("western-themes").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-theme]");
  if (!btn || !$("western-themes").contains(btn)) return;
  westernTheme = btn.dataset.theme || "";
  markThemes();
  const q = $("western-input").value.trim();
  if (westernMode === "search" && q) {
    loadWesternSearch(q, 1).catch((err) => setStatus($("western-status"), err.message, "bad"));
    return;
  }
  loadWesternFeed(1);
});

$("western-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("western-input").value.trim();
  if (!q) {
    loadWesternFeed(1);
    return;
  }
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    await loadWesternSearch(q, 1);
  } catch (err) {
    setStatus($("western-status"), err.message, "bad");
  } finally {
    btn.disabled = false;
  }
});

$("western-works").addEventListener("click", (e) => {
  const card = e.target.closest("[data-id]");
  if (!card) return;
  openWestern(card.dataset.id, card.dataset.kind || westernKind);
});

$("western-back-btn").addEventListener("click", () => {
  if (westernItems.length) showWesternList();
  else loadWesternFeed(1);
});

async function openWesternById(id) {
  westernBootstrapped = true;
  location.hash = "#/western";
  $("western-works-wrap").hidden = true;
  $("western-feed").hidden = true;
  $("western-themes").hidden = true;
  $("western-back").hidden = false;
  renderWesternMeta(null);
  renderWesternResources([]);
  setStatus($("western-status"), "查询详情…");
  let error = "";
  for (const kind of ["scene", "movie"]) {
    try {
      const data = await api(`/api/western/${kind}/${encodeURIComponent(id)}`);
      if (data.item) {
        await openWestern(id, kind, { ...data.item, kind });
        return;
      }
      error = data.error || error;
    } catch (err) {
      error = err.message;
    }
  }
  setStatus($("western-status"), error || "ThePornDB 里找不到这部", "bad");
}

$("western-resources").addEventListener("click", async (e) => {
  const dl = e.target.closest("[data-west-dl]");
  const copy = e.target.closest("[data-west-copy]");
  const hash = (dl || copy)?.dataset.westDl || copy?.dataset.westCopy;
  if (!hash) return;
  const item = westernResources.find((x) => x.info_hash === hash);
  if (!item) return;
  if (copy) {
    try {
      await navigator.clipboard.writeText(item.magnet);
      copy.textContent = "已复制";
      setTimeout(() => { copy.textContent = "复制"; }, 1200);
    } catch {
      prompt("磁链", item.magnet);
    }
    return;
  }
  if (westernCurrent && westernCurrent.suck) {
    setStatus($("western-status"), "已标 suck，不会再下载", "bad");
    return;
  }
  const work = westernCurrent || {};
  dl.disabled = true;
  try {
    await startDownload({
      kind: "western",
      tpdb_id: work.id || "",
      tpdb_kind: work.kind || "scene",
      site: work.site || "",
      date: work.date || "",
      performers: work.performers || [],
      work_title: work.title || item.title,
      info_hash: item.info_hash,
      title: item.title,
    }, $("western-status"));
  } catch (err) {
    setStatus($("western-status"), err.message, "bad");
  } finally {
    dl.disabled = false;
  }
});

$("western-meta").addEventListener("click", (e) => {
  const suck = e.target.closest("[data-suck-mark], [data-suck-clear]");
  if (suck) {
    commitSuck(suck);
    return;
  }
  const img = e.target.closest("img[data-full]");
  if (!img) return;
  openLightbox(img.dataset.full || img.getAttribute("data-full"));
});

function monthLabel(month) {
  return /^\d{6}$/.test(month) ? `${month.slice(0, 4)}-${month.slice(4)}` : (month || "未分月");
}

const LIB_SORTS = {
  jav: [["group", "按月份"], ["added", "最近入库"], ["release", "发售日"], ["name", "番号"]],
  western: [["group", "按片商"], ["added", "最近入库"], ["release", "发行日"], ["name", "片名"]],
};

function fillLibrarySort() {
  const select = $("library-sort");
  select.innerHTML = LIB_SORTS[libraryKind].map(([value, label]) => (
    `<option value="${value}"${value === librarySort ? " selected" : ""}>${label}</option>`
  )).join("");
}

function libraryItems() {
  const data = libraryPayload || {};
  const jav = libraryKind === "jav";
  const groups = jav ? (data.jav || []) : (data.western || []);
  return groups.flatMap((group) => (group.items || []).map((item) => ({
    ...item,
    group: jav ? monthLabel(group.month) : (group.studio || "未知片商"),
  })));
}

function libraryMatches(item, words) {
  if (!words.length) return true;
  const hay = [item.code, item.title, item.group, ...(item.actors || [])].join(" ").toLowerCase();
  return words.every((word) => hay.includes(word));
}

function sortLibrary(items) {
  const name = (item) => item.code || item.title || "";
  const byName = (a, b) => name(a).localeCompare(name(b), "zh-CN", { numeric: true });
  if (librarySort === "added") return items.sort((a, b) => (b.added_at || 0) - (a.added_at || 0) || byName(a, b));
  if (librarySort === "release") {
    return items.sort((a, b) => (b.release_date || "").localeCompare(a.release_date || "") || byName(a, b));
  }
  if (librarySort === "name") return items.sort(byName);
  return items;
}

function libraryCard(item) {
  const jav = libraryKind === "jav";
  const name = jav ? item.code : item.title;
  const poster = item.has_poster
    ? `/api/library/poster?kind=${libraryKind}&path=${encodeURIComponent(item.path)}`
    : "";
  const flags = [
    item.has_poster ? "" : '<span class="flag">缺封面</span>',
    item.has_nfo ? "" : '<span class="flag">缺 NFO</span>',
  ].join("");
  const actors = (item.actors || []).join("、");
  const date = item.release_date || "";
  const head = jav
    ? `<span class="code">${escapeHtml(item.code)}</span><span class="work-date">${escapeHtml(date)}</span>`
    : `<span class="work-site">${escapeHtml(item.group)}</span><span class="work-date">${escapeHtml(date)}</span>`;
  const title = jav ? (item.title || "") : name;
  return `
    <article class="lib-card${jav ? "" : " wide"}" tabindex="0"
      ${jav ? `data-code="${escapeHtml(item.code)}"` : ""}
      ${!jav && item.tpdb_id ? `data-tpdb="${escapeHtml(item.tpdb_id)}"` : ""}
      ${poster ? `data-poster="${escapeHtml(poster)}"` : ""}>
      <div class="lib-cover">
        ${poster ? `<img src="${poster}" loading="lazy" alt="" />` : `<div class="lib-ph">${escapeHtml(name)}</div>`}
        ${flags ? `<div class="lib-flags">${flags}</div>` : ""}
        <button type="button" class="lib-copy" data-copy-path="${escapeHtml(item.full_path || "")}">复制路径</button>
      </div>
      <div class="lib-info">
        <div class="lib-line">${head}</div>
        ${title ? `<div class="work-title">${escapeHtml(title)}</div>` : ""}
        ${actors ? `<div class="work-people">${escapeHtml(actors)}</div>` : ""}
        ${librarySuckButton(item)}
      </div>
    </article>`;
}

function librarySuckButton(item) {
  const jav = libraryKind === "jav";
  const key = jav ? item.code : item.tpdb_id;
  if (!key) return "";
  const title = item.title || key;
  return `<button type="button" class="ghost small danger lib-suck" data-suck-mark="${jav ? "jav" : "western"}" data-key="${escapeHtml(key)}" data-title="${escapeHtml(title)}" data-remove="1">标 suck</button>`;
}

function renderSuck() {
  const data = libraryPayload || {};
  $("library-root").textContent = "标过 suck 的片子不会再下载";
  const list = $("library-list");
  const more = $("library-more");
  const words = libraryQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const all = data.suck || [];
  const items = all.filter((item) => {
    if (!words.length) return true;
    const hay = [item.key, item.title, item.kind].join(" ").toLowerCase();
    return words.every((word) => hay.includes(word));
  });
  $("library-count").textContent = words.length ? `${items.length} / ${all.length} 部` : (all.length ? `共 ${all.length} 部` : "");
  if (!items.length) {
    more.hidden = true;
    list.innerHTML = `<p class="empty">${all.length ? "没有对得上的片子" : "还没有标 suck 的片子"}</p>`;
    setStatus($("library-status"), "", "");
    return;
  }
  setStatus($("library-status"), "", "");
  const shown = items.slice(0, libraryLimit);
  list.innerHTML = `<div class="suck-list">${shown.map((item) => `
    <article class="suck-item">
      <div>
        <span class="suck-pill">suck</span>
        <strong>${escapeHtml(item.key)}</strong>
        ${item.title && item.title !== item.key ? `<span class="suck-title">${escapeHtml(item.title)}</span>` : ""}
        <span class="suck-kind">${item.kind === "western" ? "欧美" : "番号"}</span>
      </div>
      <button type="button" class="ghost small" data-suck-clear="${escapeHtml(item.kind)}" data-key="${escapeHtml(item.key)}" data-title="${escapeHtml(item.title || item.key)}">取消</button>
    </article>`).join("")}</div>`;
  const rest = items.length - shown.length;
  more.hidden = rest <= 0;
  more.querySelector("button").textContent = `再显示 ${Math.min(LIB_PAGE, rest)} 部（还剩 ${rest}）`;
}

function renderLibrary() {
  if (libraryKind === "suck") {
    renderSuck();
    return;
  }
  const data = libraryPayload || { jav: [], western: [], jav_root: "", western_root: "" };
  const jav = libraryKind === "jav";
  $("library-root").textContent = jav
    ? data.jav_root || ""
    : (data.western_root || "还没配置欧美归档目录");
  const list = $("library-list");
  const status = $("library-status");
  const more = $("library-more");
  const all = libraryItems();
  const words = libraryQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const items = sortLibrary(all.filter((item) => libraryMatches(item, words)));
  $("library-count").textContent = words.length ? `${items.length} / ${all.length} 部` : (all.length ? `共 ${all.length} 部` : "");
  if (!items.length) {
    list.innerHTML = "";
    more.hidden = true;
    const empty = all.length
      ? "没有对得上的片子"
      : (jav ? "番号库是空的" : (data.western_root ? "还没有欧美片子" : "还没配置欧美归档目录"));
    list.innerHTML = `<p class="empty">${escapeHtml(empty)}</p>`;
    setStatus(status, "", "");
    return;
  }
  setStatus(status, "", "");
  const shown = items.slice(0, libraryLimit);
  const gridClass = `lib-grid${jav ? "" : " wide"}`;
  if (librarySort === "group") {
    const counts = {};
    for (const item of items) counts[item.group] = (counts[item.group] || 0) + 1;
    const sections = [];
    for (const item of shown) {
      const last = sections[sections.length - 1];
      if (last && last.group === item.group) last.items.push(item);
      else sections.push({ group: item.group, items: [item] });
    }
    list.innerHTML = sections.map((section) => `
      <section class="lib-group">
        <h3 class="lib-group-head">${escapeHtml(section.group)}<span>${counts[section.group]}</span></h3>
        <div class="${gridClass}">${section.items.map(libraryCard).join("")}</div>
      </section>`).join("");
  } else {
    list.innerHTML = `<div class="${gridClass}">${shown.map(libraryCard).join("")}</div>`;
  }
  const rest = items.length - shown.length;
  more.hidden = rest <= 0;
  more.querySelector("button").textContent = `再显示 ${Math.min(LIB_PAGE, rest)} 部（还剩 ${rest}）`;
}

function showMoreLibrary() {
  if ($("library-more").hidden) return;
  libraryLimit += LIB_PAGE;
  renderLibrary();
}

async function loadLibrary() {
  if (!libraryPayload) {
    if (libraryKind !== "suck") fillLibrarySort();
    $("library-list").innerHTML = libraryKind === "suck" ? "" : `<div class="lib-grid">${skeletonCards(12)}</div>`;
  }
  try {
    libraryPayload = await api("/api/library");
    renderLibrary();
  } catch (err) {
    if (!libraryPayload) $("library-list").innerHTML = "";
    setStatus($("library-status"), err.message, "bad");
  }
}

function openCodeDetail(code) {
  javBootstrapped = true;
  javMode = "search";
  $("jav-pager").innerHTML = "";
  $("code-input").value = code;
  fromWorks = false;
  showBack(false);
  clearDetail();
  clearWorksView();
  setStatus($("search-status"), "查询中…");
  location.hash = "#/";
  runCodeSearch(code).catch((err) => setStatus($("search-status"), err.message, "bad"));
}

$("library-kind").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-lib]");
  if (!btn) return;
  libraryKind = btn.dataset.lib;
  librarySort = "group";
  libraryLimit = LIB_PAGE;
  for (const child of $("library-kind").querySelectorAll("button")) {
    child.classList.toggle("on", child === btn);
  }
  $("library-sort").hidden = libraryKind === "suck";
  $("library-q").placeholder = libraryKind === "suck" ? "搜番号或标题" : "搜番号、标题或演员";
  if (libraryKind !== "suck") fillLibrarySort();
  if (libraryPayload) renderLibrary();
});

let libraryTyping = null;
$("library-q").addEventListener("input", (e) => {
  clearTimeout(libraryTyping);
  libraryTyping = setTimeout(() => {
    libraryQuery = e.target.value;
    libraryLimit = LIB_PAGE;
    if (libraryPayload) renderLibrary();
  }, 150);
});

$("library-sort").addEventListener("change", (e) => {
  librarySort = e.target.value;
  libraryLimit = LIB_PAGE;
  if (libraryPayload) renderLibrary();
});

$("library-more").querySelector("button").addEventListener("click", showMoreLibrary);
if ("IntersectionObserver" in window) {
  new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting) && !views.library.hidden) showMoreLibrary();
  }, { rootMargin: "600px 0px" }).observe($("library-more"));
}

$("library-rescan").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  setStatus($("library-status"), "正在重扫…");
  try {
    const data = await api("/api/library/refresh", { method: "POST" });
    await loadLibrary();
    setStatus($("library-status"), `已重扫，共 ${data.count} 部`, "good");
  } catch (err) {
    setStatus($("library-status"), err.message, "bad");
  } finally {
    btn.disabled = false;
  }
});

function visibleStatus() {
  if (!views.library.hidden) return $("library-status");
  if (!views.western.hidden) return $("western-status");
  return $("search-status");
}

function refreshSuckOnScreen(kind, key, on, removed) {
  const clearLibrary = on && removed;
  if (kind === "jav") {
    for (const it of lastWorks || []) {
      if (it.code === key) it.suck = on;
    }
    if (!$("works-wrap").hidden) renderWorks(lastWorks);
    if (detailCode === key && lastMeta) {
      const library = clearLibrary ? { present: false } : lastMeta.library;
      renderMeta({ ...lastMeta, suck: on, library });
      renderResources(lastResources);
    }
    return;
  }
  for (const it of westernItems || []) {
    if (String(it.id) === String(key)) it.suck = on;
  }
  if (!$("western-works-wrap").hidden) renderWesternWorks(westernItems);
  if (westernCurrent && String(westernCurrent.id) === String(key)) {
    const library = clearLibrary ? { present: false } : westernCurrent.library;
    westernCurrent = { ...westernCurrent, suck: on, library };
    renderWesternMeta(westernCurrent);
    renderWesternResources(westernResources);
  }
}

async function commitSuck(el) {
  const clearing = el.dataset.suckClear != null;
  const kind = clearing ? el.dataset.suckClear : el.dataset.suckMark;
  const key = el.dataset.key || "";
  const title = el.dataset.title || key;
  const remove = el.dataset.remove === "1";
  const question = clearing
    ? `取消 ${title} 的 suck 标记？`
    : (remove ? `删掉 ${title} 并标 suck？文件会删掉，以后不会再下载。` : `把 ${title} 标 suck？以后不会再下载。`);
  if (!window.confirm(question)) return;
  el.disabled = true;
  const status = visibleStatus();
  try {
    if (clearing) {
      await api("/api/suck", { method: "DELETE", body: JSON.stringify({ kind, key }) });
    } else {
      await api("/api/suck", { method: "POST", body: JSON.stringify({ kind, key, title, remove }) });
    }
    if (libraryPayload || !views.library.hidden) await loadLibrary();
    refreshSuckOnScreen(kind, key, !clearing, remove);
    setStatus(status, clearing ? "已取消 suck" : "已标 suck", "good");
  } catch (err) {
    setStatus(status, err.message, "bad");
  } finally {
    el.disabled = false;
  }
}

$("library-list").addEventListener("click", async (e) => {
  const suck = e.target.closest("[data-suck-mark], [data-suck-clear]");
  if (suck) {
    commitSuck(suck);
    return;
  }
  const btn = e.target.closest("[data-copy-path]");
  if (btn) {
    const path = btn.dataset.copyPath || "";
    try {
      await navigator.clipboard.writeText(path);
      btn.textContent = "已复制";
      setTimeout(() => { btn.textContent = "复制路径"; }, 1200);
    } catch {
      prompt("路径", path);
    }
    return;
  }
  const card = e.target.closest(".lib-card");
  if (!card) return;
  if (card.dataset.code) openCodeDetail(card.dataset.code);
  else if (card.dataset.tpdb) openWesternById(card.dataset.tpdb);
  else if (card.dataset.poster) showLightbox(card.dataset.poster);
});

$("library-list").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || !e.target.classList.contains("lib-card")) return;
  e.target.click();
});

function syncHeaderHeight() {
  const header = document.querySelector("header.top");
  if (header) document.documentElement.style.setProperty("--head-h", `${header.offsetHeight}px`);
}
window.addEventListener("resize", syncHeaderHeight);
syncHeaderHeight();

window.addEventListener("hashchange", route);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopQueueStream();
    return;
  }
  if (!views.queue.hidden) startQueueStream();
});
route();
loadPanelLinks();
