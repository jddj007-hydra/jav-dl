const $ = (id) => document.getElementById(id);
const views = {
  search: $("view-search"),
  queue: $("view-queue"),
  settings: $("view-settings"),
};

let lastResources = [];
let lastWorks = [];
let lastWorksQuery = "";
let fromWorks = false;
let pollTimer = null;

function route() {
  const hash = location.hash.replace("#/", "") || "search";
  const name = hash.startsWith("queue") ? "queue" : hash.startsWith("settings") ? "settings" : "search";
  Object.entries(views).forEach(([k, el]) => { el.hidden = k !== name; });
  document.querySelectorAll("nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.nav === name);
  });
  if (name === "queue") refreshQueue();
  if (name === "settings") loadSettings();
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

function setStatus(el, msg, kind) {
  if (!msg) { el.hidden = true; el.textContent = ""; return; }
  el.hidden = false;
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
}

function coverSrc(url) {
  if (!url) return "";
  return "/api/img?url=" + encodeURIComponent(url);
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

function renderMeta(payload) {
  const card = $("meta-card");
  const meta = payload.metadata;
  if (!meta) {
    card.hidden = true;
    return;
  }
  const actors = normalizeActors(meta.actors);
  const genres = (meta.genres || []).map((g) => `<span class="tag">${escapeHtml(g)}</span>`).join("");
  const actorHtml = actors.length
    ? `<div class="actor-row">${actors.map((a) => `
        <div class="actor-card">
          ${a.photo ? `<img src="${coverSrc(a.photo)}" alt="${escapeHtml(a.name)}" />` : `<div class="actor-ph"></div>`}
          <span>${escapeHtml(a.name)}</span>
        </div>`).join("")}</div>`
    : "";
  const samples = meta.samples || [];
  const previewHtml = samples.length
    ? `<div class="preview-row">${samples.map((s) => `
        <img src="${coverSrc(s.thumb || s.full)}" data-full="${escapeHtml(s.full || s.thumb || "")}" alt="预览" />
      `).join("")}</div>`
    : "";
  card.hidden = false;
  card.innerHTML = `
    <div class="meta-main">
      <img class="cover" src="${coverSrc(meta.cover)}" data-full="${escapeHtml(meta.cover || "")}" alt="" />
      <div>
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
    <button type="button" class="work-card" data-code="${escapeHtml(it.code)}">
      <img src="${coverSrc(it.cover)}" alt="" />
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
        <button type="button" data-dl="${it.info_hash}">下载</button>
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
  $("code-input").value = lastWorksQuery;
  renderWorks(lastWorks);
  const n = lastWorks.length;
  setStatus(
    $("search-status"),
    n ? `找到 ${n} 部作品，点一张看磁链` : "没有搜到作品",
    n ? "good" : "bad",
  );
}

async function runCodeSearch(code, { fromList = false } = {}) {
  fromWorks = fromList;
  showBack(fromList && lastWorks.length > 0);
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
  if (hash.startsWith("queue") || hash.startsWith("settings")) return;
  if (e.state && e.state.javdl === "code") {
    $("code-input").value = e.state.code || "";
    runCodeSearch(e.state.code, { fromList: true });
    return;
  }
  if (e.state && e.state.javdl === "works" && lastWorks.length) {
    showWorksList();
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
  dl.disabled = true;
  try {
    await api("/api/downloads", {
      method: "POST",
      body: JSON.stringify({
        code: $("code-input").value.trim(),
        info_hash: item.info_hash,
        title: item.title,
      }),
    });
    location.hash = "#/queue";
  } catch (err) {
    setStatus($("search-status"), err.message, "bad");
  } finally {
    dl.disabled = false;
  }
});

function renderQueue(items) {
  const list = $("queue-list");
  const empty = $("queue-empty");
  if (!items.length) {
    empty.hidden = false;
    list.innerHTML = "";
    return;
  }
  empty.hidden = true;
  list.innerHTML = items.map((j) => `
    <li>
      <div class="row">
        <strong><span class="code">${escapeHtml(j.code)}</span>${escapeHtml(j.title)}</strong>
        <span>${escapeHtml(j.status)}</span>
      </div>
      <div class="bar"><span style="width:${Math.min(100, j.progress || 0)}%"></span></div>
      <div class="meta-line">
        <span>${j.progress || 0}%</span>
        <span>${escapeHtml(j.downloaded)} / ${escapeHtml(j.total)}</span>
        <span>${escapeHtml(j.speed)}</span>
        <span>${j.eta ? "ETA " + escapeHtml(j.eta) : ""}</span>
        <span>${j.seeders ? j.seeders + " 种子" : ""}</span>
      </div>
      ${j.error ? `<p class="status bad">${escapeHtml(j.error)}</p>` : ""}
      <div class="row-actions">
        ${j.status === "paused" ? `<button data-act="resume" data-id="${j.id}">继续</button>` : `<button class="ghost" data-act="pause" data-id="${j.id}">暂停</button>`}
        <button class="ghost" data-act="cancel" data-id="${j.id}">取消</button>
      </div>
    </li>`).join("");
}

async function refreshQueue() {
  try {
    const data = await api("/api/downloads");
    renderQueue(data.items || []);
  } catch (err) {
    $("queue-list").innerHTML = `<li class="status bad">${escapeHtml(err.message)}</li>`;
  }
}

$("queue-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  btn.disabled = true;
  try {
    await api(`/api/downloads/${btn.dataset.id}/${btn.dataset.act}`, { method: "POST" });
    await refreshQueue();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
});

function toggleXunleiFields() {
  const on = $("downloader-select").value === "xunlei";
  $("xunlei-fields").hidden = !on;
}

async function loadSettings() {
  const s = await api("/api/settings");
  const form = $("settings-form");
  form.proxy_enabled.checked = !!s.proxy_enabled;
  form.proxy_url.value = s.proxy_url || "";
  form.javbus_base.value = s.javbus_base || "";
  form.clm_home.value = s.clm_home || "";
  form.clm_search.value = s.clm_search || "";
  form.downloader.value = s.downloader === "xunlei" ? "xunlei" : "aria2";
  form.xunlei_url.value = s.xunlei_url || "";
  form.xunlei_username.value = s.xunlei_username || "";
  form.xunlei_password.value = "";
  form.xunlei_password.placeholder = s.xunlei_password_set ? "已保存，留空不改" : "";
  form.xunlei_device_name.value = s.xunlei_device_name || "";
  toggleXunleiFields();
  $("download-dir").textContent = "下载目录（只读，由运行环境决定）：" + (s.download_dir || "");
  try {
    const h = await api("/api/health");
    $("health-box").innerHTML = [
      ["下载器", { ok: true, version: h.downloader || "?" }],
      ["迅雷", h.xunlei],
      ["aria2", h.aria2],
      ["JavBus", h.javbus],
      ["磁力猫", h.clm],
    ].map(([name, x]) => `
      <div class="pill">
        <span>${name}</span>
        <span class="dot ${x.ok ? "ok" : "no"}">${x.ok ? "正常" : "不通"}${x.version ? " · " + x.version : ""}</span>
      </div>`).join("");
  } catch {
    $("health-box").innerHTML = "";
  }
}

$("downloader-select").addEventListener("change", toggleXunleiFields);

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
    downloader: form.downloader.value,
    xunlei_url: form.xunlei_url.value.trim(),
    xunlei_username: form.xunlei_username.value.trim(),
    xunlei_device_name: form.xunlei_device_name.value.trim(),
  };
  const pw = form.xunlei_password.value;
  if (pw) body.xunlei_password = pw;
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

function openLightbox(url) {
  if (!url) return;
  const box = $("lightbox");
  const img = box.querySelector("img");
  img.src = coverSrc(url);
  box.hidden = false;
}

$("meta-card").addEventListener("click", (e) => {
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

window.addEventListener("hashchange", route);
route();
pollTimer = setInterval(() => {
  if (!views.queue.hidden) refreshQueue();
}, 2000);
