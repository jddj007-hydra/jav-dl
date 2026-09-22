const $ = (id) => document.getElementById(id);
const views = {
  search: $("view-search"),
  western: $("view-western"),
  queue: $("view-queue"),
  settings: $("view-settings"),
};

let lastResources = [];
let lastWorks = [];
let lastWorksQuery = "";
let lastLibrary = null;
let fromWorks = false;
let pollTimer = null;
let javKind = "censored";
let javPage = 1;
let javMode = "latest";
let javBootstrapped = false;
let westernKind = "scene";
let westernPage = 1;
let westernMode = "latest";
let westernBootstrapped = false;
let westernItems = [];
let westernResources = [];
let westernCurrent = null;

function route() {
  const hash = location.hash.replace("#/", "") || "search";
  const name = hash.startsWith("queue")
    ? "queue"
    : hash.startsWith("settings")
      ? "settings"
      : hash.startsWith("western")
        ? "western"
        : "search";
  Object.entries(views).forEach(([k, el]) => { el.hidden = k !== name; });
  document.querySelectorAll("nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.nav === name);
  });
  if (name === "queue") refreshQueue();
  if (name === "settings") loadSettings();
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

function libraryFlag(lib) {
  if (!lib || !lib.present) return "";
  const path = lib.path ? ` · ${escapeHtml(lib.path)}` : "";
  return `<p class="lib-flag">库里已有${path}</p>`;
}

function renderMeta(payload) {
  const card = $("meta-card");
  const meta = payload.metadata;
  lastLibrary = payload.library || null;
  if (!meta) {
    if (lastLibrary && lastLibrary.present) {
      card.hidden = false;
      card.innerHTML = libraryFlag(lastLibrary);
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
        ${libraryFlag(lastLibrary)}
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
    <button type="button" class="work-card${it.library && it.library.present ? " in-library" : ""}" data-code="${escapeHtml(it.code)}">
      ${it.library && it.library.present ? '<span class="lib-badge">已有</span>' : ""}
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
        <button type="button" data-dl="${it.info_hash}">${lastLibrary && lastLibrary.present ? "下载（库里已有）" : "下载"}</button>
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
  clearWorksView();
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
  if (hash.startsWith("queue") || hash.startsWith("settings") || hash.startsWith("western")) return;
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

function scrapeLine(j) {
  if (j.scrape_status === "archived" && j.archive_path) {
    return `<p class="status good">已归档 ${escapeHtml(j.archive_path)}</p>`;
  }
  if (j.scrape_status === "waiting") {
    return `<p class="status">等待刮削</p>`;
  }
  if (j.scrape_status === "error" && j.scrape_error) {
    return `<p class="status bad">刮削失败：${escapeHtml(j.scrape_error)}</p>`;
  }
  return "";
}

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
      ${scrapeLine(j)}
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
      ["ThePornDB", h.tpdb, "token"],
    ].map(([name, x, mode]) => {
      const ok = mode === "token" ? !!(x && (x.configured || x.ok)) : !!(x && x.ok);
      const label = mode === "token" ? (ok ? "已配置" : "未填写") : (ok ? "正常" : "不通");
      return `
      <div class="pill">
        <span>${name}</span>
        <span class="dot ${ok ? "ok" : "no"}">${label}${x && x.version ? " · " + x.version : ""}</span>
      </div>`;
    }).join("");
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
    tpdb_api_key: form.tpdb_api_key.value.trim(),
    downloader: form.downloader.value,
    xunlei_url: form.xunlei_url.value.trim(),
    xunlei_username: form.xunlei_username.value.trim(),
    xunlei_device_name: form.xunlei_device_name.value.trim(),
    scrape_enabled: form.scrape_enabled.checked,
    media_dir: form.media_dir.value.trim(),
  };
  const pw = form.xunlei_password.value;
  if (pw) body.xunlei_password = pw;
  if (!body.tpdb_api_key) delete body.tpdb_api_key;
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
  list.innerHTML = westernItems.map((it) => `
    <button type="button" class="work-card" data-id="${escapeHtml(it.id)}" data-kind="${escapeHtml(it.kind || westernKind)}">
      <img src="${coverSrc(it.cover)}" alt="" />
      <span class="code">${escapeHtml(it.site || "")}</span>
      <span class="work-title">${escapeHtml(it.title || "")}</span>
      <span class="work-people">${escapeHtml((it.performers || []).join("、"))}</span>
      <span class="work-date">${escapeHtml(it.date || "")}</span>
    </button>`).join("");
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
      <img class="cover" src="${coverSrc(item.cover || item.background)}" data-full="${escapeHtml(item.background || item.cover || "")}" alt="" />
      <div>
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
          <span>${escapeHtml(it.date || "")}</span>
        </div>
      </div>
      <div class="res-actions">
        <button type="button" data-west-dl="${it.info_hash}">下载</button>
        <button type="button" class="ghost" data-west-copy="${it.info_hash}">复制</button>
      </div>
    </article>`).join("");
}

function showWesternList() {
  $("western-back").hidden = true;
  $("western-meta").hidden = true;
  $("western-resources-wrap").hidden = true;
  $("western-feed").hidden = false;
  renderWesternWorks(westernItems);
  const n = westernItems.length;
  const latest = westernMode === "latest";
  setStatus(
    $("western-status"),
    n ? (latest ? `最新 ${n} 部` : `找到 ${n} 部`) : (latest ? "没有更多了" : "没有搜到作品"),
    n ? "good" : "bad",
  );
}

async function loadWesternFeed(page) {
  westernMode = "latest";
  westernPage = page;
  $("western-back").hidden = true;
  $("western-meta").hidden = true;
  $("western-resources-wrap").hidden = true;
  $("western-feed").hidden = false;
  setStatus($("western-status"), "加载最新…");
  try {
    const data = await api(`/api/western/latest?kind=${encodeURIComponent(westernKind)}&page=${page}`);
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
    setStatus($("western-status"), err.message, "bad");
  }
}

function ensureWesternLatest() {
  if (westernBootstrapped) return;
  westernBootstrapped = true;
  loadWesternFeed(1);
}

async function openWestern(id, kind) {
  const listed = westernItems.find((it) => String(it.id) === String(id)) || null;
  westernCurrent = listed;
  $("western-works-wrap").hidden = true;
  $("western-feed").hidden = true;
  $("western-back").hidden = false;
  renderWesternMeta(listed);
  renderWesternResources([]);
  setStatus($("western-status"), "查询详情和磁链…");
  const magnetParams = new URLSearchParams();
  if (listed && listed.site) magnetParams.set("site", listed.site);
  if (listed && listed.title) magnetParams.set("title", listed.title);
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
    if (detail.value.item) renderWesternMeta({ ...listed, ...detail.value.item, kind });
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
      msg = matched === "performer"
        ? `片名对不上，下面是演员相关的 ${items.length} 条`
        : matched === "site"
          ? `片名对不上，下面是片商相关的 ${items.length} 条`
          : `找到 ${items.length} 条磁链`;
      tone = matched === "title" || !matched ? "good" : "";
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
  setStatus($("western-status"), "查询中…");
  const data = await api(
    `/api/western/search?kind=${encodeURIComponent(westernKind)}&q=${encodeURIComponent(q)}&page=${page}`,
  );
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

$("western-back-btn").addEventListener("click", showWesternList);

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
  const work = westernCurrent || {};
  dl.disabled = true;
  try {
    await api("/api/downloads", {
      method: "POST",
      body: JSON.stringify({
        kind: "western",
        tpdb_id: work.id || "",
        tpdb_kind: work.kind || "scene",
        site: work.site || "",
        date: work.date || "",
        performers: work.performers || [],
        work_title: work.title || item.title,
        info_hash: item.info_hash,
        title: item.title,
      }),
    });
    location.hash = "#/queue";
  } catch (err) {
    setStatus($("western-status"), err.message, "bad");
  } finally {
    dl.disabled = false;
  }
});

$("western-meta").addEventListener("click", (e) => {
  const img = e.target.closest("img[data-full]");
  if (!img) return;
  openLightbox(img.dataset.full || img.getAttribute("data-full"));
});

window.addEventListener("hashchange", route);
route();
pollTimer = setInterval(() => {
  if (!views.queue.hidden) refreshQueue();
}, 2000);
