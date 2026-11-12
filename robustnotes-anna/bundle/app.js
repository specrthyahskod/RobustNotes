import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

const STORAGE_KEY = "robustnotes:studio:v2";
const MAX_STORAGE_BYTES = 200_000;
const MAX_ITEMS = 50;
const PAGE = { notebook: { width: 900, height: 1273 }, whiteboard: { width: 1280, height: 900 } };
const $ = (id) => document.getElementById(id);
const els = Object.fromEntries(["app", "document-list", "search", "new-document", "export-workspace", "dashboard", "document-view", "canvas-toolbar", "text-toolbar", "page-rail", "page-list", "canvas-viewport", "page-canvas", "canvas-overlays", "text-editor-wrap", "text-editor", "document-crumb", "save-state", "storage-label", "save-dot", "rename-document", "favorite-document", "open-ai", "more-actions", "add-page", "duplicate-page", "clear-page", "undo", "redo", "export-page", "print-notebook", "study-cards", "analyze-text", "canvas-zoom", "zoom-number", "zoom-slider", "zoom-out", "zoom-in", "zoom-fit", "canvas-page-count", "canvas-status", "subtool", "ink-size", "size-label", "custom-color", "image-input", "pdf-input", "open-pdf", "ocr", "text-color", "text-size", "ai-panel", "ai-messages", "ai-input", "send-ai", "close-ai", "open-ai", "voice-input", "toast", "empty-state", "import-workspace"].map((id) => [id.replace(/-([a-z])/g, (_, c) => c.toUpperCase()), $(id)]));
els.saveDot = document.querySelector(".save-dot");

let anna = null;
let workspace = { version: 2, docs: [], revisions: [] };
let activeDocId = null;
let activePageId = null;
let filter = "all";
let selectedTool = "pen";
let inkColor = "#172b3c";
let zoom = 84;
let pointer = null;
let undoStack = [];
let redoStack = [];
let storageExists = false;
let storageEtag;
let saveTimer = 0;
let toastTimer = 0;
let previewMode = false;
const pdfPreviewUrls = new Map();
let musicUrl = null;
let musicQueue = [];
let musicIndex = 0;
let musicRepeat = false;
let copiedItems = [];
let selectedItemIds = new Set();
let editingTextId = null;
const imageCache = new Map();

const ctx = els.pageCanvas.getContext("2d", { alpha: false, desynchronized: true });

function toast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => els.toast.classList.remove("show"), 3200);
}

function saveStatus(message, saved = false) {
  els.saveState.textContent = message;
  els.storageLabel.textContent = previewMode ? "Browser preview · not synced" : "Private Anna account storage";
  els.saveDot.classList.toggle("saved", saved);
}

function createId() { return crypto.randomUUID(); }
function activeDoc() { return workspace.docs.find((doc) => doc.id === activeDocId) ?? null; }
function activePage() { return activeDoc()?.pages?.find((page) => page.id === activePageId) ?? null; }

function newPage(type, title) {
  return { id: createId(), title, template: type === "whiteboard" ? "blank" : "dot-grid", items: [], sourceText: "", pdfName: "", pdfPath: "" };
}

function newDocument(type) {
  const labels = { notebook: "My notebook", text: "Untitled notes.txt", whiteboard: "My whiteboard" };
  const now = new Date().toISOString();
  const doc = { id: createId(), type, title: labels[type], createdAt: now, updatedAt: now, favorite: false, tags: [], folder: "", body: "", bodyHtml: "", revisions: [], pages: type === "text" ? [] : [newPage(type, "Page 1")] };
  workspace.docs.unshift(doc);
  activeDocId = doc.id;
  activePageId = doc.pages[0]?.id ?? null;
  filter = "all";
  document.querySelectorAll(".filter-button").forEach((button) => button.classList.toggle("active", button.dataset.filter === "all"));
  render();
  saveNow();
  return doc;
}

function docSnippet(doc) {
  if (doc.type === "text") return doc.body.trim().slice(0, 86) || "Text file";
  return doc.pages?.map((page) => page.sourceText || page.items?.map((item) => item.text || "").join(" ")).join(" ").trim().slice(0, 86) || (doc.type === "whiteboard" ? "Open canvas" : `${doc.pages?.length ?? 0} pages`);
}

function renderDocList() {
  const term = els.search.value.trim().toLocaleLowerCase();
  const docs = [...workspace.docs].filter((doc) => (filter === "all" || doc.type === filter) && (!term || `${doc.title} ${doc.body} ${doc.folder} ${(doc.tags || []).join(" ")} ${docSnippet(doc)}`.toLocaleLowerCase().includes(term)));
  docs.sort((a, b) => Number(b.favorite) - Number(a.favorite) || b.updatedAt.localeCompare(a.updatedAt));
  els.documentList.replaceChildren();
  for (const doc of docs) {
    const button = document.createElement("button");
    button.className = `note-item${doc.id === activeDocId ? " active" : ""}`;
    button.type = "button";
    const title = document.createElement("span");
    title.className = "note-item-title";
    const kind = document.createElement("span");
    kind.className = "note-kind";
    kind.textContent = doc.type === "notebook" ? "▤" : doc.type === "whiteboard" ? "◇" : "‹›";
    title.append(kind, document.createTextNode(`${doc.favorite ? "★ " : ""}${doc.title || "Untitled"}`));
    const meta = document.createElement("span");
    meta.className = "note-item-meta";
    meta.textContent = `${doc.folder ? `${doc.folder} · ` : ""}${docSnippet(doc)}`;
    button.append(title, meta);
    button.addEventListener("click", () => openDocument(doc.id));
    els.documentList.append(button);
  }
}

function render() {
  const doc = activeDoc();
  els.dashboard.hidden = Boolean(doc);
  els.documentView.hidden = !doc;
  els.emptyState.hidden = workspace.docs.length > 0;
  renderDocList();
  if (!doc) return;
  els.documentCrumb.textContent = doc.title;
  els.renameDocument.disabled = false;
  els.favoriteDocument.disabled = false;
  els.favoriteDocument.textContent = doc.favorite ? "★" : "☆";
  els.favoriteDocument.title = doc.favorite ? "Remove from favorites" : "Add to favorites";
  els.openPdf.hidden = !activePage()?.pdfName;
  els.openAi.disabled = false;
  els.moreActions.disabled = false;
  const isCanvas = doc.type !== "text";
  els.canvasToolbar.hidden = !isCanvas;
  els.textToolbar.hidden = doc.type !== "text";
  els.pageRail.hidden = doc.type !== "notebook";
  els.canvasViewport.hidden = !isCanvas;
  els.textEditorWrap.hidden = doc.type !== "text";
  if (doc.type === "text") {
    if (els.textEditor.innerText !== doc.body) els.textEditor.innerHTML = sanitizeRichHtml(doc.bodyHtml || escapeHtml(doc.body).replace(/\n/g, "<br>"));
    els.canvasPageCount.textContent = "Text file";
  } else {
    renderPages();
    setZoom(zoom);
    drawPage();
    drawOverlays();
  }
}

function openDocument(id) {
  flushPendingSave();
  activeDocId = id;
  const doc = activeDoc();
  activePageId = doc?.pages?.[0]?.id ?? null;
  undoStack = [];
  redoStack = [];
  selectedItemIds = new Set();
  render();
}

function serializeWorkspace() { return { version: 2, docs: workspace.docs, revisions: workspace.revisions.slice(-5) }; }

async function saveNow() {
  clearTimeout(saveTimer);
  const value = serializeWorkspace();
  if (new TextEncoder().encode(JSON.stringify(value)).length > 200_000) {
    saveStatus("Storage limit reached", false);
    toast("Anna app storage is full. Export your workspace backup, then remove old pages or images.");
    return false;
  }
  try {
    if (anna) {
      if (storageExists && !storageEtag) throw new Error("Cannot safely update without the workspace ETag. Reload and try again.");
      const result = await anna.storage.set({ scope: "app", key: STORAGE_KEY, value, ...(storageExists ? { if_match: storageEtag } : {}) });
      storageExists = true;
      storageEtag = result?.etag;
      if (!storageEtag) storageEtag = (await anna.storage.get({ scope: "app", key: STORAGE_KEY }))?.etag;
    } else {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    }
    saveStatus("All changes saved", true);
    return true;
  } catch (error) {
    saveStatus("Save failed · retry", false);
    toast(`Could not save: ${error?.message || "storage unavailable"}`);
    return false;
  }
}

function queueSave() {
  const doc = activeDoc();
  if (doc) doc.updatedAt = new Date().toISOString();
  saveStatus("Saving…", false);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 500);
}

function flushPendingSave() { if (saveTimer) void saveNow(); }

async function loadWorkspace() {
  try {
    let value;
    let migrated = false;
    if (anna) {
      const result = await anna.storage.get({ scope: "app", key: STORAGE_KEY });
      storageExists = result?.exists === true;
      storageEtag = result?.etag;
      value = storageExists ? result.value : null;
    } else {
      previewMode = true;
      value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    }
    if (!value && anna) {
      const legacy = await anna.storage.get({ scope: "app", key: "robustnotes:workspace:v1" });
      if (legacy?.exists) {
        const old = typeof legacy.value === "string" ? JSON.parse(legacy.value) : legacy.value;
        if (Array.isArray(old?.notes)) {
          value = { version: 2, docs: old.notes.map((note) => ({ id: note.id || createId(), type: "text", title: note.title || "Untitled note", body: note.body || "", bodyHtml: "", createdAt: note.createdAt || new Date().toISOString(), updatedAt: note.updatedAt || new Date().toISOString(), favorite: false, tags: [], folder: "", pages: [], revisions: [] })), revisions: [] };
          migrated = true;
          toast("Your Anna notes were migrated into the RobustNotes workspace.");
        }
      }
    }
    if (value && value.version === 2 && Array.isArray(value.docs)) {
      workspace.docs = value.docs.filter((doc) => doc && typeof doc.id === "string" && ["notebook", "text", "whiteboard"].includes(doc.type));
      workspace.revisions = Array.isArray(value.revisions) ? value.revisions.slice(-5) : [];
      if (workspace.docs.length && workspace.docs[0].id) activeDocId = workspace.docs[0].id;
      activePageId = activeDoc()?.pages?.[0]?.id ?? null;
      if (anna && migrated) { storageExists = false; storageEtag = undefined; setTimeout(() => void saveNow(), 100); }
    }
    saveStatus("Ready", Boolean(value));
  } catch (error) {
    previewMode = !anna;
    saveStatus("Storage unavailable", false);
    toast(`Could not load workspace: ${error?.message || "request failed"}`);
  }
  render();
}

function makePageSnapshot() {
  const page = activePage();
  if (!page) return;
  undoStack.push(JSON.stringify(page.items));
  if (undoStack.length > 40) undoStack.shift();
  redoStack = [];
  updateUndoButtons();
}

function restoreItems(serialized, destination) {
  const page = activePage();
  if (!page) return;
  destination.push(JSON.stringify(page.items));
  page.items = JSON.parse(serialized);
  selectedItemIds = new Set();
  updateUndoButtons();
  drawPage(); drawOverlays(); queueSave();
}

function updateUndoButtons() { els.undo.disabled = !undoStack.length; els.redo.disabled = !redoStack.length; }
function undo() { if (undoStack.length) restoreItems(undoStack.pop(), redoStack); }
function redo() { if (redoStack.length) restoreItems(redoStack.pop(), undoStack); }

function pageSize() { return PAGE[activeDoc()?.type] ?? PAGE.notebook; }

function fitCanvas() {
  const size = pageSize();
  const available = Math.max(260, els.canvasViewport.clientWidth - 58);
  const fit = Math.min(100, (available / size.width) * 100);
  zoom = Math.max(40, Math.min(140, Math.round(fit * 0.99)));
  setZoom(zoom);
}

function setZoom(value) {
  zoom = Math.max(40, Math.min(200, Number(value) || 84));
  els.zoomNumber.value = String(zoom);
  els.zoomSlider.value = String(Math.min(140, zoom));
  els.canvasZoom.textContent = `${zoom}%`;
  const size = pageSize();
  const width = Math.round(size.width * zoom / 100);
  els.canvasWrap.style.width = `${width}px`;
  els.canvasWrap.style.height = `${Math.round(width * size.height / size.width)}px`;
  drawPage(); drawOverlays();
}

els.canvasWrap = document.querySelector(".canvas-wrap");

function drawPaper() {
  const doc = activeDoc();
  const page = activePage();
  const { width, height } = pageSize();
  ctx.fillStyle = "#fffefa";
  ctx.fillRect(0, 0, width, height);
  if (doc?.type === "whiteboard" || page?.template === "blank") return;
  const template = page?.template || "dot-grid";
  ctx.save();
  ctx.strokeStyle = "#d8e2e8";
  ctx.fillStyle = "#d4dfe6";
  ctx.lineWidth = 1;
  if (template === "dot-grid") {
    for (let x = 32; x < width - 20; x += 24) for (let y = 35; y < height - 20; y += 24) { ctx.beginPath(); ctx.arc(x, y, 1.05, 0, Math.PI * 2); ctx.fill(); }
  } else if (template === "ruled" || template === "cornell" || template === "meeting") {
    for (let y = 58; y < height - 24; y += 28) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke(); }
    if (template === "cornell") { ctx.beginPath(); ctx.moveTo(width * .29, 0); ctx.lineTo(width * .29, height); ctx.stroke(); }
    if (template === "meeting") {
      ctx.fillStyle = "#8093a4"; ctx.font = "15px system-ui";
      ctx.fillText("TOPIC", 52, 38); ctx.fillText("DATE", width * .55, 38);
      ctx.beginPath(); ctx.moveTo(width * .52, 8); ctx.lineTo(width * .52, 45); ctx.stroke();
    }
  } else if (template === "planner") {
    ctx.fillStyle = "#eef4f6"; ctx.fillRect(24, 25, width - 48, 62);
    ctx.fillStyle = "#3c5262"; ctx.font = "bold 20px system-ui"; ctx.fillText("WEEKLY PLANNER", 42, 64);
    const colW = (width - 48) / 2;
    for (let i = 0; i < 6; i++) {
      const col = i % 2; const row = Math.floor(i / 2);
      const x = 24 + col * colW; const y = 104 + row * 345;
      ctx.strokeRect(x, y, colW, 320); ctx.fillStyle = "#e8eff2"; ctx.fillRect(x, y, colW, 30);
      ctx.fillStyle = "#546b7b"; ctx.font = "bold 13px system-ui"; ctx.fillText(["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "WEEKEND"][i], x + 10, y + 20);
      ctx.strokeStyle = "#d8e2e8"; for (let ry = y + 60; ry < y + 310; ry += 48) { ctx.beginPath(); ctx.moveTo(x, ry); ctx.lineTo(x + colW, ry); ctx.stroke(); }
    }
  }
  ctx.restore();
}

function drawItem(item) {
  if (item.type === "image") {
    let image = imageCache.get(item.id);
    if (!image) { image = new Image(); imageCache.set(item.id, image); image.onload = () => { if (activePage()?.items.includes(item)) drawPage(); }; image.src = item.data; }
    if (image.complete && image.naturalWidth) ctx.drawImage(image, item.x, item.y, item.w, item.h);
    return;
  }
  if (item.type === "stroke") {
    if (!item.points?.length) return;
    ctx.save(); ctx.globalCompositeOperation = item.tool === "eraser" ? "destination-out" : "source-over";
    ctx.strokeStyle = item.color; ctx.fillStyle = item.color; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.globalAlpha = item.tool === "highlighter" ? .24 : 1;
    ctx.shadowColor = item.tool === "laser" ? item.color : "transparent"; ctx.shadowBlur = item.tool === "laser" ? 18 : 0;
    if (item.points.length === 1) { const point = item.points[0]; ctx.beginPath(); ctx.arc(point.x, point.y, Math.max(1.1, item.size * .5), 0, Math.PI * 2); ctx.fill(); }
    else for (let i = 1; i < item.points.length; i++) {
      const previous = item.points[i - 1]; const current = item.points[i];
      const start = i === 1 ? previous : { x: (item.points[i - 2].x + previous.x) / 2, y: (item.points[i - 2].y + previous.y) / 2 };
      const end = { x: (previous.x + current.x) / 2, y: (previous.y + current.y) / 2 };
      ctx.lineWidth = item.size * (.4 + current.p * .6); ctx.beginPath(); ctx.moveTo(start.x, start.y); ctx.quadraticCurveTo(previous.x, previous.y, end.x, end.y); ctx.stroke();
    }
    ctx.restore(); return;
  }
  if (item.type === "text") {
    if (editingTextId === item.id) return;
    ctx.save(); ctx.fillStyle = item.color || "#172b3c"; ctx.font = `${item.style === "cursive" ? "italic " : ""}${item.size || 18}px ${item.style === "cursive" ? "cursive" : "system-ui"}`; ctx.textBaseline = "top";
    const lineHeight = (item.size || 18) * 1.35; let y = item.y;
    for (const paragraph of String(item.text || "").split("\n")) {
      let line = "";
      for (const word of paragraph.split(/\s+/)) {
        const candidate = line ? `${line} ${word}` : word;
        if (line && ctx.measureText(candidate).width > item.w) { ctx.fillText(line, item.x, y); y += lineHeight; line = word; }
        else line = candidate;
      }
      if (line) { ctx.fillText(line, item.x, y); y += lineHeight; }
    }
    ctx.restore(); return;
  }
  ctx.save(); ctx.strokeStyle = item.color || inkColor; ctx.fillStyle = item.color || inkColor; ctx.lineWidth = item.size || 3;
  if (item.type === "line") { ctx.beginPath(); ctx.moveTo(item.x1, item.y1); ctx.lineTo(item.x2, item.y2); ctx.stroke(); }
  if (item.type === "shape") {
    const left = Math.min(item.x1, item.x2); const top = Math.min(item.y1, item.y2); const w = Math.abs(item.x2 - item.x1); const h = Math.abs(item.y2 - item.y1);
    ctx.beginPath();
    if (item.shape === "ellipse") ctx.ellipse(left + w / 2, top + h / 2, Math.max(1, w / 2), Math.max(1, h / 2), 0, 0, Math.PI * 2);
    else if (item.shape === "triangle") { ctx.moveTo(left + w / 2, top); ctx.lineTo(left + w, top + h); ctx.lineTo(left, top + h); ctx.closePath(); }
    else ctx.rect(left, top, w, h);
    ctx.stroke();
  }
  if (item.type === "ruler") {
    const x1 = item.x1; const y1 = item.y1; const dx = item.x2 - x1; const dy = item.y2 - y1; const length = Math.hypot(dx, dy) || 1; const ux = dx / length; const uy = dy / length;
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(item.x2, item.y2); ctx.stroke();
    for (let d = 0; d <= length; d += 20) { const major = Math.round(d) % 100 === 0; const tick = major ? 15 : 7; ctx.beginPath(); ctx.moveTo(x1 + ux * d - uy * tick, y1 + uy * d + ux * tick); ctx.lineTo(x1 + ux * d + uy * tick, y1 + uy * d - ux * tick); ctx.stroke(); }
    ctx.font = "14px system-ui"; ctx.fillText(`${(length / 100).toFixed(1)} cm`, x1 + 8, y1 - 12);
  }
  if (item.type === "compass") {
    const radius = Math.hypot(item.x2 - item.x1, item.y2 - item.y1);
    ctx.beginPath(); ctx.arc(item.x1, item.y1, radius, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(item.x1, item.y1); ctx.lineTo(item.x2, item.y2); ctx.stroke();
  }
  if (item.type === "laser") { ctx.globalAlpha = .9; ctx.shadowColor = item.color; ctx.shadowBlur = 17; ctx.beginPath(); ctx.arc(item.x2, item.y2, 7, 0, Math.PI * 2); ctx.fill(); }
  ctx.restore();
}

function drawPage() {
  const size = pageSize();
  if (els.pageCanvas.width !== size.width || els.pageCanvas.height !== size.height) { els.pageCanvas.width = size.width; els.pageCanvas.height = size.height; }
  drawPaper();
  const page = activePage();
  for (const item of page?.items ?? []) {
    drawItem(item);
    if (selectedItemIds.has(item.id)) {
      const bounds = itemBounds(item);
      if (bounds) { ctx.save(); ctx.strokeStyle = "#13b8c8"; ctx.fillStyle = "#13b8c8"; ctx.lineWidth = 2; ctx.setLineDash([6, 4]); ctx.strokeRect(bounds.x - 6, bounds.y - 6, bounds.w + 12, bounds.h + 12); ctx.setLineDash([]); if (item.type === "image") ctx.fillRect(bounds.x + bounds.w - 5, bounds.y + bounds.h - 5, 10, 10); ctx.restore(); }
    }
  }
  if (pointer?.preview) drawItem(pointer.preview);
}

function renderPages() {
  const doc = activeDoc();
  if (!doc || doc.type === "text") return;
  els.pageList.replaceChildren();
  doc.pages.forEach((page, index) => {
    const button = document.createElement("button"); button.className = `page-thumb${page.id === activePageId ? " active" : ""}`; button.draggable = true;
    const thumb = document.createElement("canvas"); thumb.width = 90; thumb.height = Math.round(90 * pageSize().height / pageSize().width);
    drawThumbnail(thumb, page);
    const label = document.createElement("span"); label.textContent = page.title || `Page ${index + 1}`; button.append(thumb, label);
    button.addEventListener("click", () => { flushPendingSave(); activePageId = page.id; undoStack = []; redoStack = []; render(); });
    button.addEventListener("dblclick", (event) => { event.preventDefault(); const title = prompt("Rename page", page.title); if (title?.trim()) { page.title = title.trim().slice(0, 80); renderPages(); queueSave(); } });
    button.addEventListener("dragstart", (event) => event.dataTransfer.setData("text/plain", page.id));
    button.addEventListener("dragover", (event) => event.preventDefault());
    button.addEventListener("drop", (event) => { event.preventDefault(); movePage(event.dataTransfer.getData("text/plain"), page.id); });
    els.pageList.append(button);
  });
  els.canvasPageCount.textContent = `${doc.pages.length} ${doc.pages.length === 1 ? "page" : "pages"}`;
}

function drawThumbnail(canvas, page) {
  const target = canvas.getContext("2d"); const full = pageSize(); const sx = canvas.width / full.width; const sy = canvas.height / full.height;
  target.fillStyle = "#fffefa"; target.fillRect(0, 0, canvas.width, canvas.height);
  target.strokeStyle = "#d7e2e8"; target.lineWidth = .5;
  if (page.template === "dot-grid") { target.fillStyle = "#d6e0e7"; for (let x = 4; x < canvas.width; x += 5) for (let y = 4; y < canvas.height; y += 5) { target.beginPath(); target.arc(x, y, .3, 0, Math.PI * 2); target.fill(); } }
  if (["ruled", "cornell", "meeting"].includes(page.template)) for (let y = 8; y < canvas.height; y += 6) { target.beginPath(); target.moveTo(0, y); target.lineTo(canvas.width, y); target.stroke(); }
  for (const item of page.items || []) {
    target.save(); target.strokeStyle = item.color || "#173044"; target.fillStyle = item.color || "#173044"; target.lineWidth = Math.max(.5, (item.size || 2) * sx); target.lineCap = "round";
    if (item.type === "stroke") { target.beginPath(); item.points?.forEach((point, index) => index ? target.lineTo(point.x * sx, point.y * sy) : target.moveTo(point.x * sx, point.y * sy)); target.stroke(); }
    else if (item.type === "line") { target.beginPath(); target.moveTo(item.x1 * sx, item.y1 * sy); target.lineTo(item.x2 * sx, item.y2 * sy); target.stroke(); }
    else if (item.type === "shape") target.strokeRect(Math.min(item.x1, item.x2) * sx, Math.min(item.y1, item.y2) * sy, Math.abs(item.x2 - item.x1) * sx, Math.abs(item.y2 - item.y1) * sy);
    else if (item.type === "text") { target.font = `${Math.max(4, (item.size || 14) * sx)}px cursive`; target.fillText(item.text.slice(0, 25), item.x * sx, item.y * sy + 5); }
    target.restore();
  }
}

function movePage(sourceId, targetId) {
  const doc = activeDoc(); const from = doc.pages.findIndex((page) => page.id === sourceId); const to = doc.pages.findIndex((page) => page.id === targetId);
  if (from < 0 || to < 0 || from === to) return;
  const [page] = doc.pages.splice(from, 1); doc.pages.splice(to, 0, page); queueSave(); renderPages();
}

function pointerPoint(event) {
  const rect = els.pageCanvas.getBoundingClientRect();
  return { x: (event.clientX - rect.left) * els.pageCanvas.width / rect.width, y: (event.clientY - rect.top) * els.pageCanvas.height / rect.height, p: Math.max(.12, Math.min(1, event.pressure || (event.buttons ? .5 : 1))) };
}

function getItemAt(x, y) {
  const page = activePage();
  for (let i = (page?.items.length ?? 0) - 1; i >= 0; i--) {
    const item = page.items[i];
    if (item.type === "text") { if (x >= item.x && x <= item.x + item.w && y >= item.y && y <= item.y + item.h) return item; }
    if (item.type === "image") { if (x >= item.x && x <= item.x + item.w && y >= item.y && y <= item.y + item.h) return item; }
    if (item.type === "stroke" && item.points?.some((point) => Math.hypot(point.x - x, point.y - y) < Math.max(12, item.size * 2))) return item;
  }
  return null;
}

function startPointer(event) {
  if (event.button !== 0 || !activePage()) return;
  event.preventDefault(); els.pageCanvas.setPointerCapture(event.pointerId);
  const point = pointerPoint(event); const size = Number(els.inkSize.value);
  if (selectedTool === "lasso") {
    pointer = { id: event.pointerId, start: point, points: [point], selected: [], existing: null };
    const hit = getItemAt(point.x, point.y);
    if (hit) {
      makePageSnapshot();
      if (!selectedItemIds.has(hit.id)) selectedItemIds = new Set([hit.id]);
      pointer.existing = hit; pointer.start = point; pointer.resize = hit.type === "image" && point.x >= hit.x + hit.w - 28 && point.y >= hit.y + hit.h - 28;
      pointer.before = structuredClone(hit);
    }
    return;
  }
  if (selectedTool === "text") { addCanvasText(point); return; }
  if (selectedTool === "eraser") { makePageSnapshot(); pointer = { id: event.pointerId, tool: "eraser", last: point }; eraseAt(point); return; }
  if (selectedTool === "pen" || selectedTool === "highlighter" || selectedTool === "laser") {
    makePageSnapshot(); const color = selectedTool === "laser" ? "#ff38b8" : inkColor;
    const item = { id: createId(), type: "stroke", tool: selectedTool, color, size, points: [point] };
    activePage().items.push(item); pointer = { id: event.pointerId, tool: "freehand", item }; drawPage(); return;
  }
  if (["line", "shape", "scale", "compass"].includes(selectedTool)) {
    makePageSnapshot(); const type = selectedTool === "line" ? "line" : selectedTool === "shape" ? "shape" : selectedTool === "scale" ? "ruler" : "compass";
    const shape = els.subtool.value; pointer = { id: event.pointerId, tool: "geometry", start: point, preview: { id: createId(), type, x1: point.x, y1: point.y, x2: point.x, y2: point.y, shape: ["rectangle-shape", "ellipse", "triangle"].includes(shape) ? shape.replace("-shape", "") : "rectangle", color: inkColor, size } }; drawPage();
  }
}

function movePointer(event) {
  if (!pointer || pointer.id !== event.pointerId) return;
  const point = pointerPoint(event);
  if (pointer.tool === "freehand") {
    const samples = event.getCoalescedEvents?.() ?? [event];
    for (const sample of samples) {
      const next = pointerPoint(sample); const previous = pointer.item.points.at(-1);
      if (Math.hypot(next.x - previous.x, next.y - previous.y) >= .65) pointer.item.points.push(next);
    }
    drawPage();
  }
  else if (pointer.tool === "eraser") { eraseAt(point); }
  else if (pointer.tool === "geometry") { pointer.preview.x2 = point.x; pointer.preview.y2 = point.y; drawPage(); }
  else if (selectedTool === "lasso" && pointer.existing) { moveItem(pointer.existing, pointer.before, point, pointer.resize); drawPage(); drawOverlays(); }
  else if (selectedTool === "lasso") { pointer.points.push(point); drawPage(); drawLassoPreview(); }
}

function endPointer(event) {
  if (!pointer || pointer.id !== event.pointerId) return;
  if (pointer.tool === "geometry") { activePage().items.push(pointer.preview); }
  else if (selectedTool === "lasso" && !pointer.existing) selectLasso(pointer.points);
  else if (selectedTool === "lasso" && pointer.existing) { queueSave(); }
  else if (pointer.tool === "freehand" || pointer.tool === "eraser") queueSave();
  pointer = null; drawPage(); drawOverlays();
}

function moveItem(item, before, point, resizing = false) {
  const dx = point.x - pointer.start.x; const dy = point.y - pointer.start.y;
  if (resizing && item.type === "image") { const ratio = before.w / Math.max(1, before.h); item.w = Math.max(50, before.w + dx); item.h = item.w / ratio; }
  else if (item.type === "stroke") item.points = before.points.map((p) => ({ ...p, x: p.x + dx, y: p.y + dy }));
  else if (item.type === "text" || item.type === "image") { item.x = before.x + dx; item.y = before.y + dy; }
  else { item.x1 = before.x1 + dx; item.y1 = before.y1 + dy; item.x2 = before.x2 + dx; item.y2 = before.y2 + dy; }
}

function eraseAt(point) {
  const page = activePage(); const index = page?.items.findIndex((item) => item.type === "stroke" && item.points.some((p) => Math.hypot(p.x - point.x, p.y - point.y) < 22));
  if (index >= 0) { page.items.splice(index, 1); drawPage(); }
}

function drawLassoPreview() {
  const canvas = els.pageCanvas; const overlay = canvas.getContext("2d");
  if (!pointer?.points?.length) return;
  overlay.save(); overlay.strokeStyle = "#16aebe"; overlay.setLineDash([7, 5]); overlay.lineWidth = 2; overlay.beginPath();
  const start = pointer.points[0]; overlay.moveTo(start.x, start.y);
  if (els.subtool.value === "rectangle") { const current = pointer.points.at(-1); overlay.rect(start.x, start.y, current.x - start.x, current.y - start.y); }
  else pointer.points.forEach((p) => overlay.lineTo(p.x, p.y));
  overlay.stroke(); overlay.restore();
}

function selectLasso(points) {
  if (points.length < 2) return;
  const start = points[0]; const end = points.at(-1); const left = Math.min(start.x, end.x); const right = Math.max(start.x, end.x); const top = Math.min(start.y, end.y); const bottom = Math.max(start.y, end.y);
  const selected = activePage().items.filter((item) => { const bounds = itemBounds(item); return bounds && bounds.x <= right && bounds.x + bounds.w >= left && bounds.y <= bottom && bounds.y + bounds.h >= top; });
  selectedItemIds = new Set(selected.map((item) => item.id));
  drawPage();
  if (selected.length) toast(`${selected.length} item${selected.length > 1 ? "s" : ""} selected · drag to move`);
}

function itemBounds(item) {
  if (item.type === "text" || item.type === "image") return { x: item.x, y: item.y, w: item.w, h: item.h };
  if (item.type === "stroke" && item.points?.length) {
    const xs = item.points.map((p) => p.x), ys = item.points.map((p) => p.y);
    return { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
  }
  if (Number.isFinite(item.x1)) return { x: Math.min(item.x1, item.x2), y: Math.min(item.y1, item.y2), w: Math.abs(item.x2 - item.x1), h: Math.abs(item.y2 - item.y1) };
  return null;
}

function addCanvasText(point) {
  makePageSnapshot();
  const page = activePage(); const item = { type: "text", id: createId(), x: point.x, y: point.y, w: 260, h: 58, text: "Type here", color: els.textColor.value, size: Number(els.textSize.value) };
  page.items.push(item); drawPage(); drawOverlays(); queueSave();
  const node = [...els.canvasOverlays.querySelectorAll(".canvas-text-item")].find((element) => element.dataset.id === item.id);
  node?.focus();
  if (node) { const range = document.createRange(); range.selectNodeContents(node); const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); }
}

function drawOverlays() {
  els.canvasOverlays.replaceChildren();
  const doc = activeDoc(); const page = activePage(); if (!doc || !page || doc.type === "text") return;
  const size = pageSize();
  for (const item of page.items) if (item.type === "text") {
    const node = document.createElement("div"); node.className = "canvas-text-item"; node.contentEditable = "true"; node.spellcheck = true; node.dataset.id = item.id; node.textContent = item.text;
    node.style.left = `${item.x / size.width * 100}%`; node.style.top = `${item.y / size.height * 100}%`; node.style.width = `${item.w / size.width * 100}%`; node.style.minHeight = `${item.h / size.height * 100}%`; node.style.color = "transparent"; node.style.fontSize = `${item.size * zoom / 100}px`;
    if (item.style === "cursive") { node.style.fontFamily = "cursive"; node.style.fontStyle = "italic"; }
    node.addEventListener("input", () => { item.text = node.innerText; item.h = Math.max(42, node.offsetHeight * size.height / els.canvasWrap.clientHeight); queueSave(); });
    node.addEventListener("focus", () => { editingTextId = item.id; node.style.color = item.color; drawPage(); });
    node.addEventListener("blur", () => { item.text = node.innerText; editingTextId = null; node.style.color = "transparent"; drawPage(); queueSave(); });
    node.addEventListener("pointerdown", (event) => {
      if (selectedTool === "lasso" && event.button === 0) {
        event.preventDefault(); event.stopPropagation(); node.setPointerCapture(event.pointerId); makePageSnapshot();
        pointer = { id: event.pointerId, tool: "item", start: pointerPoint(event), existing: item, before: structuredClone(item) };
      } else if (selectedTool === "text") { event.stopPropagation(); node.focus(); }
      else event.stopPropagation();
    });
    node.addEventListener("pointermove", movePointer);
    node.addEventListener("pointerup", endPointer);
    node.addEventListener("dblclick", (event) => { event.stopPropagation(); setTool("text"); node.focus(); });
    els.canvasOverlays.append(node);
  }
}

function renderCanvasSize() { setZoom(zoom); }

function setTool(tool) {
  selectedTool = tool;
  document.querySelectorAll(".tool-button").forEach((button) => button.classList.toggle("selected", button.dataset.tool === tool));
  const values = { lasso: ["free", "rectangle"], shape: ["rectangle-shape", "ellipse", "triangle"], "": ["dot-grid", "ruled", "cornell", "meeting", "planner"] };
  let options = values[tool];
  if (!options && ["pen", "highlighter", "eraser", "laser", "line", "text", "scale", "compass"].includes(tool)) options = [];
  const templateOptions = [["dot-grid", "Dot grid"], ["ruled", "Ruled"], ["cornell", "Cornell notes"], ["meeting", "Meeting notes"], ["planner", "Weekly planner"]];
  const toolOptions = tool === "lasso" ? [["free", "Freehand lasso"], ["rectangle", "Rectangular lasso"]] : tool === "shape" ? [["rectangle-shape", "Rectangle"], ["ellipse", "Ellipse"], ["triangle", "Triangle"]] : [];
  const combined = [...toolOptions, ...templateOptions.map(([value, label]) => [`template:${value}`, `Paper · ${label}`])];
  els.subtool.replaceChildren(...combined.map(([value, label]) => { const option = document.createElement("option"); option.value = value; option.textContent = label; return option; }));
  els.subtool.hidden = combined.length === 0;
  if (tool === "pen") els.canvasStatus.textContent = "Pen · pressure-sensitive when using a compatible stylus";
  else if (tool === "lasso") els.canvasStatus.textContent = "Select an item to move it, or drag on empty space to select";
  else els.canvasStatus.textContent = `${tool[0].toUpperCase()}${tool.slice(1)} selected`;
}

function setPaper(value) {
  if (!value.startsWith("template:")) return;
  const page = activePage(); if (!page) return;
  page.template = value.slice("template:".length); drawPage(); renderPages(); queueSave();
}

async function addImage(file) {
  if (!file || !file.type.startsWith("image/")) { toast("Choose a supported image file."); return; }
  if (file.size > 8_000_000) { toast("Choose an image smaller than 8 MB."); return; }
  if (!activePage()) return;
  const image = await createImageBitmap(file); const scale = Math.min(1, 800 / Math.max(image.width, image.height));
  const temp = document.createElement("canvas"); temp.width = Math.max(1, Math.round(image.width * scale)); temp.height = Math.max(1, Math.round(image.height * scale));
  temp.getContext("2d").drawImage(image, 0, 0, temp.width, temp.height); image.close?.();
  let data = temp.toDataURL("image/jpeg", .68);
  while (data.length > 120_000 && temp.width > 180) { temp.width = Math.round(temp.width * .8); temp.height = Math.round(temp.height * .8); temp.getContext("2d").drawImage(image, 0, 0, temp.width, temp.height); data = temp.toDataURL("image/jpeg", .58); }
  image.close?.();
  makePageSnapshot(); const scaleToPage = Math.min(1, 520 / temp.width, 420 / temp.height); const width = temp.width * scaleToPage; const height = temp.height * scaleToPage; const size = pageSize();
  activePage().items.push({ type: "image", id: createId(), data, x: (size.width - width) / 2, y: (size.height - height) / 2, w: width, h: height, sourceName: file.name });
  drawPage(); queueSave(); toast("Image added · use the lasso to move it; insert at a smaller size if storage is full.");
}

async function importPdf(file) {
  if (!file) return;
  if (file.size > 15_000_000) { toast("For this Anna version, import a PDF smaller than 15 MB."); return; }
  const previewUrl = URL.createObjectURL(file);
  const doc = activeDoc(); if (!doc || doc.type !== "notebook") { toast("PDF pages can be added to notebooks."); return; }
  doc.pdfAttachment = { name: file.name, size: file.size };
  const page = newPage("notebook", `PDF · ${file.name}`);
  pdfPreviewUrls.set(page.id, previewUrl);
  page.pdfName = file.name; page.sourceText = "PDF attached for reference. Open the PDF using the attachment button; this browser edition does not bundle the desktop Qt PDF renderer.";
  if (anna?.files?.upload_init && anna?.files?.upload_finalize) {
    try {
      const path = `robustnotes/attachments/${doc.id}/${createId()}.pdf`;
      const init = await anna.files.upload_init({ path, content_type: "application/pdf", size: file.size, metadata: { name: file.name, app: "robustnotes" } });
      const uploadUrl = init?.put_url ?? init?.upload_url ?? init?.url;
      if (!uploadUrl) throw new Error("Anna did not return a PDF upload URL.");
      const response = await fetch(uploadUrl, { method: "PUT", headers: init.headers || { "Content-Type": "application/pdf" }, body: file });
      if (!response.ok) throw new Error(`PDF upload failed (${response.status}).`);
      const etag = response.headers.get("etag") || init.etag;
      await anna.files.upload_finalize({ path, etag, size_bytes: file.size, metadata: { name: file.name, app: "robustnotes" } });
      page.pdfPath = path;
      page.sourceText = "PDF attached in Anna storage. Use Open PDF to view it. Canvas annotations are saved as page ink; the browser edition does not flatten PDF pages into its canvas yet.";
    } catch (error) {
      page.sourceText = `PDF “${file.name}” was attached for this session only. Anna could not save its PDF file: ${error?.message || "upload failed"}.`;
    }
  } else page.sourceText = `PDF “${file.name}” is open for this session only. Anna preview does not persist imported PDF files.`;
  doc.pages.push(page); activePageId = page.id; drawPage(); render(); queueSave();
  toast(page.pdfPath ? "PDF attached to Anna storage. It can be reopened from this notebook." : "PDF preview is attached for this session; it could not be stored in Anna.");
}

async function openCurrentPdf() {
  const current = activePage(); let url = current ? pdfPreviewUrls.get(current.id) : null;
  if (anna && current?.pdfPath) {
    try { const result = await anna.files.download_url({ path: current.pdfPath }); url = result?.url ?? result?.get_url ?? url; }
    catch (error) { toast(`Could not reopen PDF: ${error?.message || "download link failed"}`); }
  }
  if (url) window.open(url, "_blank", "noopener,noreferrer"); else toast("This PDF must be selected again to preview it.");
}

function addPage() {
  const doc = activeDoc(); if (!doc || doc.type === "text") return;
  const page = newPage(doc.type, `Page ${doc.pages.length + 1}`); doc.pages.push(page); activePageId = page.id; undoStack = []; redoStack = []; selectedItemIds = new Set(); render(); queueSave();
}

function duplicatePage() {
  const doc = activeDoc(); const page = activePage(); if (!doc || !page) return;
  const copy = structuredClone(page); copy.id = createId(); copy.title += " copy"; copy.items = structuredClone(page.items); doc.pages.splice(doc.pages.indexOf(page) + 1, 0, copy); activePageId = copy.id; selectedItemIds = new Set(); render(); queueSave();
}

function clearPage() {
  const page = activePage(); if (!page || !confirm("Clear all ink, text, and images from this page?")) return;
  makePageSnapshot(); page.items = []; drawPage(); drawOverlays(); queueSave();
}

function deleteSelection() {
  const page = activePage(); if (!page || !selectedItemIds.size) return;
  makePageSnapshot(); page.items = page.items.filter((item) => !selectedItemIds.has(item.id));
  for (const id of selectedItemIds) imageCache.delete(id);
  selectedItemIds = new Set(); drawPage(); drawOverlays(); queueSave();
}

function copySelection(cut = false) {
  const page = activePage(); if (!page) return;
  copiedItems = page.items.filter((item) => selectedItemIds.has(item.id)).map((item) => structuredClone(item));
  if (cut && copiedItems.length) deleteSelection();
  else if (copiedItems.length) toast(`${copiedItems.length} item${copiedItems.length > 1 ? "s" : ""} copied`);
}

function pasteSelection() {
  const page = activePage(); if (!page || !copiedItems.length) return false;
  makePageSnapshot(); const copies = copiedItems.map((item) => { const clone = structuredClone(item); clone.id = createId(); if (clone.type === "stroke") clone.points = clone.points.map((point) => ({ ...point, x: point.x + 24, y: point.y + 24 })); else if ("x" in clone) { clone.x += 24; clone.y += 24; } else { clone.x1 += 24; clone.y1 += 24; clone.x2 += 24; clone.y2 += 24; } return clone; });
  page.items.push(...copies); copiedItems = structuredClone(copies); selectedItemIds = new Set(copies.map((item) => item.id)); drawPage(); drawOverlays(); queueSave(); return true;
}

function renameDocument() {
  const doc = activeDoc(); if (!doc) return;
  const title = prompt("Rename this item", doc.title); if (title === null || !title.trim()) return;
  doc.title = title.trim().slice(0, 120); doc.updatedAt = new Date().toISOString(); render(); queueSave();
}

function organizeDocument() {
  const doc = activeDoc(); if (!doc) return;
  const tags = prompt("Tags, separated by commas", (doc.tags || []).join(", ")); if (tags === null) return;
  const folder = prompt("Folder name (optional)", doc.folder || ""); if (folder === null) return;
  doc.tags = [...new Set(tags.split(",").map((tag) => tag.trim().replace(/^#/, "")).filter(Boolean))].slice(0, 20); doc.folder = folder.trim().slice(0, 100); queueSave(); render();
}

function addRevision() {
  const doc = activeDoc(); if (!doc) return;
  const snapshot = JSON.stringify({ body: doc.body, pages: doc.pages });
  doc.revisions = [...(doc.revisions || []), { savedAt: new Date().toISOString(), snapshot }].slice(-5); queueSave(); toast("A local workspace revision was saved.");
}

function restoreRevision() {
  const doc = activeDoc(); if (!doc?.revisions?.length) { toast("No saved versions for this item yet."); return; }
  const choice = prompt(`Saved versions:\n${doc.revisions.map((item, i) => `${i + 1}. ${new Date(item.savedAt).toLocaleString()}`).join("\n")}\n\nEnter a version number to restore:`);
  const index = Number(choice) - 1; if (!Number.isInteger(index) || !doc.revisions[index]) return;
  const backup = JSON.stringify({ body: doc.body, pages: doc.pages }); doc.revisions.push({ savedAt: new Date().toISOString(), snapshot: backup });
  const restored = JSON.parse(doc.revisions[index].snapshot); doc.body = restored.body; doc.pages = restored.pages; activePageId = doc.pages?.[0]?.id ?? null; render(); queueSave();
}

function moreMenu() {
  const action = prompt("More actions: type one\norganize · save version · restore version · duplicate · delete · focus · calculator · music · study cards · import backup");
  if (action === "organize") organizeDocument();
  if (action === "save version") addRevision();
  if (action === "restore version") restoreRevision();
  if (action === "duplicate") duplicateDocument();
  if (action === "delete") deleteDocument();
  if (action === "focus") toggleFocus();
  if (action === "calculator") calculator();
  if (action === "music") openMusic();
  if (action === "study cards") { openAi(); void askAi("", "cards"); }
  if (action === "import backup") els.importWorkspace.click();
}

function duplicateDocument() {
  const doc = activeDoc(); if (!doc) return;
  const copy = structuredClone(doc); copy.id = createId(); copy.title = `${doc.title} copy`; copy.createdAt = new Date().toISOString(); copy.updatedAt = copy.createdAt; workspace.docs.unshift(copy); activeDocId = copy.id; activePageId = copy.pages?.[0]?.id ?? null; render(); queueSave();
}

function deleteDocument() {
  const doc = activeDoc(); if (!doc || !confirm(`Delete “${doc.title}” and its pages?`)) return;
  workspace.docs = workspace.docs.filter((item) => item.id !== doc.id); activeDocId = workspace.docs[0]?.id ?? null; activePageId = activeDoc()?.pages?.[0]?.id ?? null; render(); queueSave();
}

function toggleFocus() { document.body.classList.toggle("focus-mode"); }

function calculator() {
  const expression = prompt("Quick calculator · use numbers and + − * / parentheses"); if (!expression) return;
  if (!/^[\d+*/().%\s-]+$/.test(expression)) { toast("Only basic arithmetic is allowed."); return; }
  try { const answer = Function(`"use strict"; return (${expression})`)(); if (!Number.isFinite(answer)) throw new Error(); toast(`${expression} = ${answer}`); }
  catch { toast("That calculation is not valid."); }
}

async function openMusic() {
  const picker = document.createElement("input"); picker.type = "file"; picker.accept = "audio/*"; picker.multiple = true;
  picker.addEventListener("change", () => {
    const files = [...(picker.files || [])]; if (!files.length) return;
    musicQueue.forEach((entry) => URL.revokeObjectURL(entry.url)); musicQueue = files.map((file) => ({ name: file.name, url: URL.createObjectURL(file) })); musicIndex = 0;
    document.getElementById("study-music")?.remove();
    const panel = document.createElement("section"); panel.id = "study-music"; panel.className = "music-float";
    const head = document.createElement("header"); const title = document.createElement("strong"); title.textContent = "Study Music";
    const close = document.createElement("button"); close.textContent = "×"; close.title = "Close player"; close.addEventListener("click", () => { audio.pause(); panel.remove(); }); head.append(title, close);
    const trackLabel = document.createElement("div"); trackLabel.className = "music-track-label";
    const audio = document.createElement("audio"); audio.controls = true;
    const controls = document.createElement("div"); controls.className = "music-controls";
    const previous = document.createElement("button"); previous.textContent = "|◀"; previous.title = "Previous";
    const next = document.createElement("button"); next.textContent = "▶|"; next.title = "Next";
    const shuffle = document.createElement("button"); shuffle.textContent = "Shuffle";
    const repeat = document.createElement("button"); repeat.textContent = "Repeat";
    const choose = document.createElement("button"); choose.textContent = "Add tracks";
    const list = document.createElement("div"); list.className = "music-queue";
    const load = (index, play = true) => { musicIndex = (index + musicQueue.length) % musicQueue.length; const current = musicQueue[musicIndex]; trackLabel.textContent = `${musicIndex + 1} / ${musicQueue.length} · ${current.name}`; audio.src = current.url; list.querySelectorAll("button").forEach((button, i) => button.classList.toggle("active", i === musicIndex)); if (play) audio.play().catch(() => toast("Press Play to start audio.")); };
    const renderQueue = () => { list.replaceChildren(); musicQueue.forEach((item, index) => { const button = document.createElement("button"); button.textContent = item.name; button.addEventListener("click", () => load(index)); list.append(button); }); load(musicIndex); };
    previous.addEventListener("click", () => load(musicIndex - 1)); next.addEventListener("click", () => load(musicIndex + 1));
    shuffle.addEventListener("click", () => { if (musicQueue.length > 1) { const current = musicQueue[musicIndex]; musicQueue = musicQueue.filter((_, i) => i !== musicIndex).sort(() => Math.random() - .5); musicQueue.unshift(current); musicIndex = 0; renderQueue(); } });
    repeat.addEventListener("click", () => { musicRepeat = !musicRepeat; audio.loop = musicRepeat; repeat.classList.toggle("active", musicRepeat); });
    audio.addEventListener("ended", () => { if (!musicRepeat && musicQueue.length > 1) load(musicIndex + 1); });
    choose.addEventListener("click", () => picker.click()); controls.append(previous, next, shuffle, repeat, choose);
    panel.append(head, trackLabel, audio, controls, list); document.body.append(panel); renderQueue();
  }); picker.click();
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportPage() { els.pageCanvas.toBlob((blob) => blob && downloadBlob(blob, `${(activeDoc()?.title || "page").replace(/[^\w-]+/g, "-")}-${activePage()?.title || "page"}.png`)); }

function printNotebook() {
  const doc = activeDoc(); if (!doc || doc.type !== "notebook") return;
  const printWindow = window.open("", "_blank"); if (!printWindow) { toast("Allow pop-ups to print this notebook."); return; }
  printWindow.opener = null;
  const pages = doc.pages.map((page) => {
    const canvas = document.createElement("canvas"); canvas.width = PAGE.notebook.width; canvas.height = PAGE.notebook.height;
    const prior = activePageId; activePageId = page.id; const old = els.pageCanvas.toDataURL(); drawPage(); canvas.getContext("2d").drawImage(els.pageCanvas, 0, 0); activePageId = prior; drawPage();
    return `<img src="${canvas.toDataURL("image/png")}" alt="${escapeHtml(page.title)}">`;
  }).join("");
  printWindow.document.write(`<!doctype html><title>${escapeHtml(doc.title)}</title><style>@page{size:A4;margin:0}body{margin:0}.page{width:210mm;height:297mm;page-break-after:always}img{width:100%;height:100%;object-fit:contain}</style>${pages}`);
  printWindow.document.close(); printWindow.focus(); setTimeout(() => printWindow.print(), 450);
}

function escapeHtml(text) { return String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]); }

function sanitizeRichHtml(html) {
  const parsed = new DOMParser().parseFromString(String(html), "text/html");
  const allowed = new Set(["B", "STRONG", "I", "EM", "U", "SPAN", "BR", "DIV", "P", "PRE", "CODE", "FONT"]);
  const walk = (node) => {
    for (const child of [...node.childNodes]) {
      if (child.nodeType !== Node.ELEMENT_NODE) continue;
      if (!allowed.has(child.tagName)) { child.replaceWith(document.createTextNode(child.textContent || "")); continue; }
      const color = child.style.color; const size = child.style.fontSize;
      [...child.attributes].forEach((attribute) => child.removeAttribute(attribute.name));
      if (color && /^(#[\da-f]{3,8}|rgba?\(\s*[\d.,% ]+\))$/i.test(color)) child.style.color = color;
      if (size && /^\d{1,2}px$/.test(size)) child.style.fontSize = size;
      walk(child);
    }
  };
  walk(parsed.body);
  return parsed.body.innerHTML;
}

function exportWorkspace() {
  const plain = JSON.stringify({ app: "RobustNotes for Anna", version: 2, exportedAt: new Date().toISOString(), workspace: serializeWorkspace() }, null, 2);
  const password = prompt("Optional: enter a password to encrypt this backup. Leave blank for a regular JSON file.");
  if (password === null) return;
  if (!password) { downloadBlob(new Blob([plain], { type: "application/json" }), "robustnotes-workspace.json"); return; }
  encryptBackup(plain, password).then((data) => downloadBlob(new Blob([JSON.stringify(data)], { type: "application/json" }), "robustnotes-encrypted-backup.json")).catch(() => toast("Backup encryption failed."));
}

async function encryptBackup(text, password) {
  const salt = crypto.getRandomValues(new Uint8Array(16)); const iv = crypto.getRandomValues(new Uint8Array(12));
  const material = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: 310_000, hash: "SHA-256" }, material, { name: "AES-GCM", length: 256 }, false, ["encrypt"]);
  const cipher = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(text));
  return { format: "robustnotes-aes-gcm-v1", salt: bytesToBase64(salt), iv: bytesToBase64(iv), data: bytesToBase64(new Uint8Array(cipher)) };
}

function bytesToBase64(bytes) { let binary = ""; bytes.forEach((byte) => binary += String.fromCharCode(byte)); return btoa(binary); }
function base64ToBytes(text) { return Uint8Array.from(atob(text), (char) => char.charCodeAt(0)); }

async function importWorkspace(file) {
  if (!file) return;
  try {
    const parsed = JSON.parse(await file.text()); let imported;
    if (parsed.format === "robustnotes-aes-gcm-v1") {
      const password = prompt("Enter the backup password:"); if (password === null) return;
      const salt = base64ToBytes(parsed.salt); const iv = base64ToBytes(parsed.iv);
      const material = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
      const key = await crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: 310_000, hash: "SHA-256" }, material, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
      const decoded = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, base64ToBytes(parsed.data)); imported = JSON.parse(new TextDecoder().decode(decoded));
    } else imported = parsed.workspace ?? parsed;
    if (!Array.isArray(imported.docs)) throw new Error("This file does not contain a RobustNotes workspace.");
    if (!confirm(`Import ${imported.docs.length} workspace item(s)? This replaces the current Anna workspace.`)) return;
    workspace = { version: 2, docs: imported.docs, revisions: imported.revisions || [] }; activeDocId = workspace.docs[0]?.id ?? null; activePageId = activeDoc()?.pages?.[0]?.id ?? null; render(); await saveNow(); toast("Workspace imported");
  } catch (error) { toast(`Could not import backup: ${error?.message || "invalid file/password"}`); }
}

function formatText(format) { document.execCommand(format, false); els.textEditor.focus(); }

function openAi() { els.aiPanel.hidden = !els.aiPanel.hidden; updateAiContext(); }

function updateAiContext() {
  const doc = activeDoc(); const page = activePage();
  els.aiContext.textContent = doc ? `${doc.title}${page ? ` · ${page.title}` : ""}` : "Workspace assistant";
}

function canvasDataUrl() {
  if (!activeDoc() || activeDoc().type === "text") return null;
  drawPage(); return els.pageCanvas.toDataURL("image/jpeg", .72);
}

function appendAiMessage(role, text) { const item = document.createElement("div"); item.className = `ai-message ${role}`; item.textContent = text; els.aiMessages.append(item); els.aiMessages.scrollTop = els.aiMessages.scrollHeight; }

async function askAi(question, mode = "chat") {
  if (!anna) { toast("AI is available when this workspace is running inside Anna."); return; }
  const doc = activeDoc(); if (!doc) { toast("Open a note or page first."); return; }
  const page = activePage();
  const context = doc.type === "text" ? doc.body : `${page?.sourceText || ""}\n${page?.items?.filter((item) => item.type === "text").map((item) => item.text).join("\n") || ""}`;
  let promptText = question;
  if (mode === "ocr") promptText = "Transcribe the visible handwriting accurately. Return only the transcription, preserving line breaks. Mark uncertain words as [unclear].";
  if (mode === "cards") promptText = `Create five concise question-and-answer study cards from this page. Format each as Q: ... A: ...\n${context || question}`;
  appendAiMessage("user", mode === "ocr" ? "Recognize handwriting on this page" : question);
  const content = [{ type: "text", text: `${promptText}\n\nNotebook context:\n${context.slice(0, 12000) || "(no extracted text; use the page image if present)"}` }];
  const imageData = doc.type !== "text" ? canvasDataUrl() : null;
  if (imageData) { const [header, data] = imageData.split(","); content.push({ type: "image", data, mimeType: header.match(/data:(.*?);/)?.[1] || "image/jpeg" }); }
  try {
    const result = await anna.llm.complete({ messages: [{ role: "user", content }] });
    const text = result?.content?.text ?? result?.text ?? result?.choices?.[0]?.message?.content ?? "No response text was returned.";
    appendAiMessage("assistant", String(text));
    if (mode === "ocr" && page) {
      const transcript = String(text).trim();
      const selected = page.items.filter((item) => selectedItemIds.has(item.id) && item.type === "stroke");
      if (selected.length && confirm("Replace the selected handwriting with editable cursive text? Choose Cancel to keep the ink and save only the transcription.")) {
        const bounds = selected.map(itemBounds).filter(Boolean);
        const x = Math.min(...bounds.map((bound) => bound.x)); const y = Math.min(...bounds.map((bound) => bound.y));
        const width = Math.max(260, Math.max(...bounds.map((bound) => bound.x + bound.w)) - x);
        page.items = page.items.filter((item) => !selectedItemIds.has(item.id));
        page.items.push({ id: createId(), type: "text", style: "cursive", x, y, w: width, h: 72, text: transcript, color: inkColor, size: 22 });
        selectedItemIds = new Set(); drawPage(); drawOverlays();
      }
      page.sourceText = transcript; queueSave(); toast("OCR text saved for search and review. Verify the transcription.");
    }
  } catch (error) { appendAiMessage("assistant", `AI request failed: ${error?.message || "request failed"}`); }
}

async function analyzeText() {
  const doc = activeDoc();
  if (!doc || !anna) { toast("Text insights need to run inside Anna."); return; }
  const toolId = window.__ANNA_TOOL_IDS__?.["notes-core"] || "tool-dev-robustnotes-anna";
  try {
    const result = await anna.tools.invoke({ tool_id: toolId, method: "analyze_note", args: { text: doc.body.slice(0, 12000) } });
    const data = result?.data ?? result;
    toast(`${data.words} words · ~${data.reading_minutes} min · ${data.headings.length} headings`);
  } catch (error) { toast(`Text analysis failed: ${error?.message || "tool unavailable"}`); }
}

function startVoiceInput() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) { toast("Speech recognition is not available in this browser."); return; }
  const recognition = new Recognition(); recognition.lang = navigator.language || "en-US"; recognition.interimResults = false;
  recognition.onresult = (event) => { els.aiInput.value = `${els.aiInput.value} ${event.results[0][0].transcript}`.trim(); };
  recognition.onerror = (event) => toast(`Voice input error: ${event.error}`);
  recognition.start(); toast("Listening… your browser may send audio to its speech service.");
}

function renderAfterUndo() { drawPage(); drawOverlays(); queueSave(); }

els.newDocument.addEventListener("click", () => newDocument("notebook"));
document.querySelectorAll("[data-create]").forEach((button) => button.addEventListener("click", () => newDocument(button.dataset.create)));
document.querySelectorAll(".filter-button").forEach((button) => button.addEventListener("click", () => { filter = button.dataset.filter; document.querySelectorAll(".filter-button").forEach((item) => item.classList.toggle("active", item === button)); renderDocList(); }));
els.search.addEventListener("input", renderDocList);
els.renameDocument.addEventListener("click", renameDocument);
els.favoriteDocument.addEventListener("click", () => { const doc = activeDoc(); if (doc) { doc.favorite = !doc.favorite; render(); queueSave(); } });
els.moreActions.addEventListener("click", moreMenu);
els.exportWorkspace.addEventListener("click", exportWorkspace);
els.importWorkspace.addEventListener("change", () => { void importWorkspace(els.importWorkspace.files?.[0]); els.importWorkspace.value = ""; });
els.addPage.addEventListener("click", addPage);
els.duplicatePage.addEventListener("click", duplicatePage);
els.openPdf.addEventListener("click", () => void openCurrentPdf());
els.clearPage.addEventListener("click", clearPage);
els.undo.addEventListener("click", undo); els.redo.addEventListener("click", redo);
els.exportPage.addEventListener("click", exportPage); els.printNotebook.addEventListener("click", printNotebook);
els.studyCards.addEventListener("click", () => { openAi(); void askAi("", "cards"); });
els.analyzeText.addEventListener("click", () => void analyzeText());
els.canvasZoom.addEventListener("click", fitCanvas); els.zoomFit.addEventListener("click", fitCanvas);
els.zoomNumber.addEventListener("change", () => setZoom(els.zoomNumber.value)); els.zoomNumber.addEventListener("keydown", (event) => { if (event.key === "Enter") { setZoom(els.zoomNumber.value); els.zoomNumber.blur(); } });
els.zoomSlider.addEventListener("input", () => setZoom(els.zoomSlider.value));
els.zoomOut.addEventListener("click", () => setZoom(zoom - 10)); els.zoomIn.addEventListener("click", () => setZoom(zoom + 10));
els.inkSize.addEventListener("input", () => { els.sizeLabel.textContent = els.inkSize.value; });
document.querySelectorAll(".tool-button").forEach((button) => button.addEventListener("click", () => setTool(button.dataset.tool)));
document.querySelectorAll(".swatches button").forEach((button) => button.addEventListener("click", () => { inkColor = button.dataset.color; els.customColor.value = button.dataset.color; document.querySelectorAll(".swatches button").forEach((item) => item.classList.toggle("selected", item === button)); }));
els.customColor.addEventListener("input", () => { inkColor = els.customColor.value; document.querySelectorAll(".swatches button").forEach((item) => item.classList.remove("selected")); });
els.subtool.addEventListener("change", () => { if (els.subtool.value.startsWith("template:")) setPaper(els.subtool.value); });
els.pageCanvas.addEventListener("pointerdown", startPointer); els.pageCanvas.addEventListener("pointermove", movePointer); els.pageCanvas.addEventListener("pointerup", endPointer); els.pageCanvas.addEventListener("pointercancel", endPointer);
els.imageInput.addEventListener("change", () => { void addImage(els.imageInput.files?.[0]); els.imageInput.value = ""; });
document.addEventListener("paste", (event) => { if (activeDoc()?.type === "notebook") { const file = [...(event.clipboardData?.items || [])].find((item) => item.type.startsWith("image/"))?.getAsFile(); if (file) void addImage(file); } });
els.pdfInput.addEventListener("change", () => { void importPdf(els.pdfInput.files?.[0]); els.pdfInput.value = ""; });
els.textEditor.addEventListener("input", () => { const doc = activeDoc(); if (doc) { doc.body = els.textEditor.innerText; doc.bodyHtml = sanitizeRichHtml(els.textEditor.innerHTML); queueSave(); renderDocList(); } });
els.textColor.addEventListener("input", () => {
  const page = activePage();
  const selected = page?.items.filter((item) => selectedItemIds.has(item.id) && item.type === "text") ?? [];
  if (selected.length) { selected.forEach((item) => item.color = els.textColor.value); drawPage(); drawOverlays(); queueSave(); }
  else document.execCommand("foreColor", false, els.textColor.value);
});
els.textSize.addEventListener("change", () => { document.execCommand("fontSize", false, "4"); document.querySelectorAll("#text-editor font[size='4']").forEach((node) => { node.removeAttribute("size"); node.style.fontSize = `${els.textSize.value}px`; }); });
document.querySelectorAll("[data-format]").forEach((button) => button.addEventListener("click", () => formatText(button.dataset.format)));
els.openAi.addEventListener("click", openAi); els.closeAi.addEventListener("click", () => { els.aiPanel.hidden = true; });
els.sendAi.addEventListener("click", () => { const text = els.aiInput.value.trim(); if (text) { els.aiInput.value = ""; void askAi(text); } });
els.aiInput.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); els.sendAi.click(); } });
els.voiceInput.addEventListener("click", startVoiceInput);
els.ocr.addEventListener("click", () => { if (confirm("Handwriting OCR sends a snapshot of this page to Anna’s selected AI model. Continue?")) void askAi("", "ocr"); });
document.getElementById("print-notebook").addEventListener("contextmenu", (event) => { event.preventDefault(); void askAi("", "cards"); });
document.addEventListener("keydown", (event) => {
  const editingText = event.target instanceof HTMLElement && event.target.matches("input,textarea,[contenteditable=true]");
  if (editingText && ((event.ctrlKey || event.metaKey) && ["z", "y"].includes(event.key.toLowerCase()))) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") { event.preventDefault(); undo(); }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") { event.preventDefault(); redo(); }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); flushPendingSave(); }
  if (event.key === "F11") { event.preventDefault(); toggleFocus(); }
  if (event.key === "Escape") { document.body.classList.remove("focus-mode"); els.aiPanel.hidden = true; }
  if (editingText) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c" && selectedItemIds.size) { event.preventDefault(); copySelection(); return; }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "x" && selectedItemIds.size) { event.preventDefault(); copySelection(true); return; }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v" && copiedItems.length && pasteSelection()) { event.preventDefault(); return; }
  if (["Backspace", "Delete"].includes(event.key) && selectedItemIds.size) { event.preventDefault(); deleteSelection(); return; }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "n" && activeDoc()?.type !== "text") { event.preventDefault(); addPage(); return; }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") { event.preventDefault(); els.search.focus(); return; }
  const keys = { p: "pen", h: "highlighter", e: "eraser", z: "laser", i: "line", s: "shape", l: "lasso" };
  if (keys[event.key.toLowerCase()]) setTool(keys[event.key.toLowerCase()]);
});

window.addEventListener("resize", () => { if (activeDoc()?.type !== "text" && !els.documentView.hidden) setZoom(zoom); });

try { anna = await AnnaAppRuntime.connect(); await anna.window.set_title({ title: "RobustNotes Studio" }); }
catch { previewMode = true; }
await loadWorkspace();
setTool("pen");
if (activeDoc() && activeDoc().type !== "text") { setZoom(84); setTimeout(fitCanvas, 40); }
