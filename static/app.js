const $ = (s) => document.querySelector(s);
const state = { books: [], current: null, savedOffset: 0, dirty: false, timer: null,
                blocks: [], prefix: [0], rendered: 0 };
const BLOCK_CHARS = 2000;   // 每块约 2000 字，避免单个巨型文本节点拖垮排版

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.hidden = true; }, 1800);
}

function splitIntoBlocks(text) {
  const blocks = [];
  let i = 0;
  while (i < text.length) {
    let end = Math.min(i + BLOCK_CHARS, text.length);
    if (end < text.length) {
      const nl = text.lastIndexOf("\n", end);
      if (nl > i + BLOCK_CHARS / 2) end = nl + 1;   // 尽量在换行处切
    }
    blocks.push({ start: i, text: text.slice(i, end) });
    i = end;
  }
  return blocks;
}

function appendBlocks(until) {
  const box = $("#reader-text");
  if (until <= state.rendered) return;
  const frag = document.createDocumentFragment();
  for (let i = state.rendered; i < until; i++) {
    const b = state.blocks[i];
    const d = document.createElement("div");
    d.className = "blk";
    d.textContent = b.text;
    b.el = d;
    frag.appendChild(d);
  }
  box.appendChild(frag);
  state.rendered = until;
}

function scheduleRest() {
  if (state.rendered >= state.blocks.length) return;
  const tick = () => {
    if (state.rendered >= state.blocks.length) return;
    appendBlocks(Math.min(state.blocks.length, state.rendered + 150));
    if (state.rendered < state.blocks.length) setTimeout(tick, 16);
  };
  setTimeout(tick, 16);
}

function blockIndexAt(offset) {
  // prefix[i] <= offset < prefix[i+1]
  let lo = 0, hi = state.blocks.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (state.prefix[mid] <= offset) lo = mid; else hi = mid - 1;
  }
  return lo;
}

// ---------- 设置 ----------
const THEMES = { light: "#f5f2ea", sepia: "#f2e7d3", night: "#121212", green: "#dfeadb" };

function applySettings(s) {
  document.documentElement.style.setProperty("--fs", s.fontSize + "px");
  document.documentElement.style.setProperty("--lh", s.lineHeight);
  document.body.dataset.theme = s.theme;
  document.body.dataset.font = s.font || "sans";
  document.querySelectorAll("#fonts button").forEach((b) => {
    b.classList.toggle("on", (b.dataset.font || "sans") === (s.font || "sans"));
  });
  document.body.style.background = THEMES[s.theme] || THEMES.light;
}

function loadSettings() {
  const raw = localStorage.getItem("reader-settings");
  const s = raw ? JSON.parse(raw) : { fontSize: 19, lineHeight: 1.8, theme: "light", font: "sans" };
  $("#font-size").value = s.fontSize;
  $("#line-height").value = s.lineHeight;
  applySettings(s);
  return s;
}

function saveSettings(patch) {
  const s = loadSettings();
  const next = { ...s, ...patch };
  localStorage.setItem("reader-settings", JSON.stringify(next));
  applySettings(next);
}

// ---------- 列表 ----------
async function loadBooks() {
  const q = $("#search").value.trim();
  const data = await api(`/api/books?q=${encodeURIComponent(q)}`);
  state.books = data.books;
  renderList();
}

function renderList() {
  const ul = $("#book-list");
  ul.innerHTML = "";
  $("#count").textContent = `${state.books.length} 本`;
  for (const b of state.books) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="name"></span><span class="ext">${b.ext}</span>`;
    li.querySelector(".name").textContent = b.name;
    li.onclick = () => openBook(b);
    if (b.ext === "txt") {
      const del = document.createElement("button");
      del.className = "del";
      del.textContent = "删";
      del.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm(`删除《${b.name}》？文件会从磁盘移除`)) return;
        try {
          const r = await api(`/api/books/${b.id}`, { method: "DELETE" });
          toast(r.deleted_file ? "已删除" : "已从索引删除（文件未删）");
          loadBooks();
        } catch (err) { toast("删除失败：" + err.message); }
      };
      li.appendChild(del);
    }
    ul.appendChild(li);
  }
}

// ---------- 阅读 ----------
async function openBook(book) {
  if (book.ext !== "txt") { toast("非 txt，请用 Kindle 推送"); return; }
  const detail = await api(`/api/books/${book.id}`);
  const data = await api(`/api/books/${book.id}/text`);
  state.current = detail;
  state.savedOffset = detail.progress || 0;
  state.dirty = false;

  $("#book-title").textContent = detail.name;
  const box = $("#reader-text");
  state.blocks = splitIntoBlocks(data.text);
  state.prefix = [0];
  for (const b of state.blocks) state.prefix.push(state.prefix[state.prefix.length - 1] + b.text.length);

  box.replaceChildren();
  state.rendered = 0;
  const targetIdx = blockIndexAt(Math.min(state.savedOffset, state.prefix[state.prefix.length - 1]));
  appendBlocks(Math.min(state.blocks.length, Math.max(40, targetIdx + 20)));
  scheduleRest();
  document.body.dataset.view = "reader";
  $("#btn-back").hidden = false;
  scrollToOffset(state.savedOffset);
  startAutosave();
  updateProgressLabel();
}

function scrollToOffset(offset) {
  const box = $("#reader-text");
  if (!state.blocks.length) return;
  const n = Math.max(0, Math.min(offset, state.prefix[state.prefix.length - 1]));
  const bi = blockIndexAt(n);
  if (bi >= state.rendered) appendBlocks(Math.min(state.blocks.length, bi + 20));
  const blk = state.blocks[bi];
  if (!blk || !blk.el) return;
  const local = Math.max(0, Math.min(n - blk.start, blk.text.length));
  const range = document.createRange();
  range.setStart(blk.el.firstChild, local);
  range.collapse(true);
  const rect = range.getBoundingClientRect();
  box.scrollTop += rect.top - box.getBoundingClientRect().top - 8;
}

function currentOffset() {
  const box = $("#reader-text");
  if (!state.blocks.length) return 0;
  const rect = box.getBoundingClientRect();
  const targetY = rect.top + 8;

  // 先二分定位"当前视口顶部属于哪一块"
  let lo = 0, hi = Math.max(0, state.rendered - 1), bi = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const r = state.blocks[mid].el.getBoundingClientRect();
    if (r.top <= targetY) { bi = mid; lo = mid + 1; } else hi = mid - 1;
  }

  const blk = state.blocks[bi];
  const node = blk.el.firstChild;
  if (!node) return state.prefix[bi];
  let a = 0, b = node.length;
  const range = document.createRange();
  while (a < b) {
    const mid = (a + b) >> 1;
    range.setStart(node, mid);
    range.setEnd(node, Math.min(mid + 1, node.length));
    const r = range.getBoundingClientRect();
    if (r.bottom < targetY) a = mid + 1; else b = mid;
  }
  return state.prefix[bi] + a;
}

function updateProgressLabel() {
  const total = state.prefix.length ? state.prefix[state.prefix.length - 1] : 0;
  const cur = currentOffset();
  const pct = total ? ((cur / total) * 100).toFixed(1) : "0";
  $("#progress-label").textContent = `${pct}% · ${cur} / ${total} 字`;
}

async function saveProgress(force = false) {
  if (!state.current || !state.blocks.length) return;
  const offset = currentOffset();
  if (!force && Math.abs(offset - state.savedOffset) < 50) return;
  state.savedOffset = offset;
  try {
    await api(`/api/books/${state.current.id}/progress`, {
      method: "PUT", body: JSON.stringify({ char_offset: offset })
    });
    state.dirty = false;
  } catch (_) { state.dirty = true; }
}

function startAutosave() {
  clearInterval(state.timer);
  state.timer = setInterval(() => { saveProgress(); updateProgressLabel(); }, 5000);
}

// ---------- 交互 ----------
$("#search").addEventListener("input", () => { clearTimeout(window._q); window._q = setTimeout(loadBooks, 150); });
$("#btn-scan").onclick = async () => {
  toast("扫描中…");
  const r = await api("/api/scan", { method: "POST" });
  toast(`共 ${r.total}，新增 ${r.added}`);
  loadBooks();
};
$("#btn-back").onclick = async () => {
  await saveProgress(true);
  document.body.dataset.view = "list";
  $("#btn-back").hidden = true;
  clearInterval(state.timer);
  loadBooks();
};
$("#btn-settings").onclick = (e) => {
  e.stopPropagation();
  $("#panel").hidden = !$("#panel").hidden;
};
// 点面板外部关闭，避免挡住其它按钮
document.addEventListener("click", (e) => {
  const panel = $("#panel");
  if (panel.hidden) return;
  if (panel.contains(e.target) || e.target.closest("#btn-settings")) return;
  panel.hidden = true;
});
$("#btn-close-panel").onclick = () => { $("#panel").hidden = true; };
$("#font-size").oninput = (e) => saveSettings({ fontSize: +e.target.value });
$("#line-height").oninput = (e) => saveSettings({ lineHeight: +e.target.value });

document.querySelectorAll("#fonts button").forEach((b) => {
  b.onclick = () => saveSettings({ font: b.dataset.font });
});

for (const [name, color] of Object.entries(THEMES)) {
  const b = document.createElement("button");
  b.style.background = color;
  b.title = name;
  b.onclick = () => saveSettings({ theme: name });
  $("#themes").appendChild(b);
}

$("#btn-search-book").onclick = async () => {
  if (!state.current) return;
  const q = prompt("书内搜索（空格分隔多个词）");
  if (!q) return;
  const r = await api(`/api/books/${state.current.id}/search?q=${encodeURIComponent(q)}`);
  if (!r.hits.length) { toast("没找到"); return; }
  const term = q.split(/\s+/)[0];
  let idx = -1;
  for (const blk of state.blocks) {
    const local = blk.text.indexOf(term);
    if (local >= 0) { idx = blk.start + local; break; }
  }
  if (idx >= 0) scrollToOffset(idx);
  toast(`命中 ${r.hits.length} 行（第 ${r.hits[0].line_no} 行起）`);
};

$("#btn-search-content").onclick = async () => {
  const q = prompt("全库搜内容（空格分隔，多个词需同时出现）");
  if (!q) return;
  toast("搜索中…");
  try {
    const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
    if (!r.results.length) { toast("没有匹配的书"); return; }
    state.books = r.results.map((x) => x.book);
    renderList();
    toast(`命中 ${r.results.length} 本`);
  } catch (e) { toast("搜索失败：" + e.message); }
};

// 关闭/切后台时保存
document.addEventListener("visibilitychange", () => { if (document.hidden) saveProgress(true); });
window.addEventListener("pagehide", () => { saveProgress(true); });
let scrollTimer = null;
$("#reader-text").addEventListener("scroll", () => {
  if (state.rendered < state.blocks.length) {
    const box = document.querySelector("#reader-text");
    if (box.scrollHeight - box.scrollTop - box.clientHeight < 1200) {
      appendBlocks(Math.min(state.blocks.length, state.rendered + 200));
    }
  }
  if (scrollTimer) return;
  scrollTimer = setTimeout(() => { scrollTimer = null; updateProgressLabel(); }, 400);
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
loadSettings();
loadBooks();
