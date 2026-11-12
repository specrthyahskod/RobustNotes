import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

const STORAGE_KEY = "robustnotes:workspace:v1";
const MAX_NOTES = 40;
const MAX_STORAGE_BYTES = 200_000;
const $ = (id) => document.getElementById(id);

const els = {
  list: $("note-list"), search: $("search"), title: $("note-title"), body: $("note-body"),
  newButton: $("new-note"), emptyNew: $("empty-new"), deleteButton: $("delete-note"),
  exportButton: $("export"), editor: $("editor"), empty: $("empty-state"),
  saveState: $("save-state"), storageLabel: $("storage-label"), updated: $("updated-label"),
  count: $("word-count"), dot: document.querySelector(".save-dot"), toast: $("toast"),
  insights: $("insights"), insightPanel: $("insight-panel"),
};

let anna = null;
let notes = [];
let activeId = null;
let saveTimer = 0;
let toastTimer = 0;
let previewMode = false;
let storageExists = false;
let storageEtag;

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => els.toast.classList.remove("show"), 2600);
}

function setSaveState(label, saved = false) {
  els.saveState.textContent = label;
  els.storageLabel.textContent = previewMode ? "Preview saved in this browser" : "Saved to your Anna account";
  els.dot.classList.toggle("saved", saved);
}

function freshNote() {
  const now = new Date().toISOString();
  return { id: crypto.randomUUID(), title: "", body: "", createdAt: now, updatedAt: now };
}

function activeNote() { return notes.find((note) => note.id === activeId) ?? null; }

function renderList() {
  const term = els.search.value.trim().toLocaleLowerCase();
  const matching = notes.filter((note) => `${note.title} ${note.body}`.toLocaleLowerCase().includes(term));
  els.list.replaceChildren();
  for (const note of matching) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `note-item${note.id === activeId ? " active" : ""}`;
    button.setAttribute("aria-current", note.id === activeId ? "page" : "false");
    const title = document.createElement("span");
    title.className = "note-item-title";
    title.textContent = note.title.trim() || "Untitled note";
    const meta = document.createElement("span");
    meta.className = "note-item-meta";
    meta.textContent = note.body.trim().split(/\s+/).filter(Boolean).slice(0, 8).join(" ") || "No text yet";
    button.append(title, meta);
    button.addEventListener("click", () => { flushSave(); activeId = note.id; render(); });
    els.list.append(button);
  }
  if (!matching.length && term) {
    const empty = document.createElement("p");
    empty.className = "note-item-meta";
    empty.textContent = "No matching notes";
    els.list.append(empty);
  }
}

function render() {
  const note = activeNote();
  els.editor.hidden = !note;
  els.empty.hidden = Boolean(note);
  els.deleteButton.disabled = !note;
  els.insights.disabled = !note || !note.body.trim();
  renderList();
  if (!note) return;
  if (els.title.value !== note.title) els.title.value = note.title;
  if (els.body.value !== note.body) els.body.value = note.body;
  const words = note.body.trim().split(/\s+/).filter(Boolean).length;
  els.count.textContent = `${words} ${words === 1 ? "word" : "words"} · ${Math.max(1, Math.ceil(words / 220))} min read`;
  els.updated.textContent = note.body.trim() ? `Edited ${new Date(note.updatedAt).toLocaleString()}` : "Start with an idea";
}

function serialize() {
  return { version: 1, notes };
}

async function persist() {
  clearTimeout(saveTimer);
  const payload = serialize();
  if (new TextEncoder().encode(JSON.stringify(payload)).length > MAX_STORAGE_BYTES) {
    setSaveState("Storage limit reached", false);
    showToast("This Anna workspace is full. Export a backup and remove older notes.");
    return false;
  }
  try {
    if (anna) {
      if (storageExists && !storageEtag) throw new Error("Cannot safely update this note collection without its storage version. Reload and try again.");
      const result = await anna.storage.set({ scope: "app", key: STORAGE_KEY, value: payload, ...(storageExists ? { if_match: storageEtag } : {}) });
      storageExists = true;
      storageEtag = result?.etag;
      if (!storageEtag) {
        const current = await anna.storage.get({ scope: "app", key: STORAGE_KEY });
        storageEtag = current?.etag;
      }
    } else {
      localStorage.setItem(STORAGE_KEY, payload);
    }
    setSaveState("All changes saved", true);
    return true;
  } catch (error) {
    setSaveState("Could not save · try again", false);
    showToast(`Save failed: ${error?.message || "storage unavailable"}`);
    return false;
  }
}

function scheduleSave() {
  const note = activeNote();
  if (!note) return;
  note.title = els.title.value;
  note.body = els.body.value;
  note.updatedAt = new Date().toISOString();
  setSaveState("Saving…", false);
  renderList();
  const words = note.body.trim().split(/\s+/).filter(Boolean).length;
  els.count.textContent = `${words} ${words === 1 ? "word" : "words"} · ${Math.max(1, Math.ceil(words / 220))} min read`;
  els.updated.textContent = note.body.trim() ? `Edited ${new Date(note.updatedAt).toLocaleString()}` : "Start with an idea";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(persist, 450);
}

function flushSave() {
  if (saveTimer) void persist();
}

async function loadNotes() {
  let raw = null;
  if (anna) {
    try {
      const result = await anna.storage.get({ scope: "app", key: STORAGE_KEY });
      storageExists = result?.exists === true;
      storageEtag = result?.etag;
      raw = storageExists ? result.value : null;
    } catch (error) {
      setSaveState("Anna storage unavailable", false);
      showToast(`Could not load Anna storage: ${error?.message || "request failed"}`);
    }
  } else {
    previewMode = true;
    els.storageLabel.textContent = "Preview saved in this browser";
    try { raw = localStorage.getItem(STORAGE_KEY); } catch { /* Browser storage may be disabled. */ }
  }
  if (raw) {
    try {
      const data = typeof raw === "string" ? JSON.parse(raw) : raw;
      if (data?.version === 1 && Array.isArray(data.notes)) {
        notes = data.notes.filter((note) => typeof note?.id === "string" && typeof note?.body === "string" && typeof note?.title === "string").slice(0, MAX_NOTES);
      }
    } catch {
      showToast("Saved notes could not be read. Exported backups are not affected.");
    }
  }
  if (notes.length) activeId = notes[0].id;
  setSaveState("Ready", Boolean(raw));
  render();
}

async function createNote() {
  if (notes.length >= MAX_NOTES) { showToast(`This workspace supports up to ${MAX_NOTES} notes.`); return; }
  flushSave();
  const note = freshNote();
  notes.unshift(note);
  activeId = note.id;
  render();
  els.title.focus();
  await persist();
}

async function deleteNote() {
  if (!activeId) return;
  const removingId = activeId;
  notes = notes.filter((note) => note.id !== removingId);
  activeId = notes[0]?.id ?? null;
  render();
  if (await persist()) showToast("Note deleted");
}

function exportNotes() {
  const content = JSON.stringify({ app: "RobustNotes for Anna", version: 1, exportedAt: new Date().toISOString(), notes }, null, 2);
  const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `robustnotes-backup-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  showToast("Notes backup downloaded");
}

async function showInsights() {
  const note = activeNote();
  if (!note?.body.trim()) return;
  try {
    const toolId = window.__ANNA_TOOL_IDS__?.["notes-core"] || "tool-dev-robustnotes-anna";
    const result = await anna.tools.invoke({ tool_id: toolId, method: "analyze_note", args: { text: note.body } });
    const data = result?.data ?? result;
    const headings = Array.isArray(data.headings) && data.headings.length ? data.headings.join(" · ") : "No headings found";
    els.insightPanel.replaceChildren();
    const strong = document.createElement("strong");
    strong.textContent = `${data.words} words`;
    const metrics = document.createTextNode(` · about ${data.reading_minutes} min to read`);
    const lineBreak = document.createElement("br");
    const headingLine = document.createElement("span");
    headingLine.textContent = headings;
    els.insightPanel.append(strong, metrics, lineBreak, headingLine);
    els.insightPanel.hidden = false;
  } catch (error) {
    showToast(`Insights are unavailable: ${error?.message || "tool could not run"}`);
  }
}

els.newButton.addEventListener("click", () => void createNote());
els.emptyNew.addEventListener("click", () => void createNote());
els.deleteButton.addEventListener("click", () => void deleteNote());
els.exportButton.addEventListener("click", exportNotes);
els.insights.addEventListener("click", () => void showInsights());
els.search.addEventListener("input", renderList);
els.title.addEventListener("input", scheduleSave);
els.body.addEventListener("input", scheduleSave);
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); flushSave(); }
});

try {
  anna = await AnnaAppRuntime.connect();
  await anna.window.set_title({ title: "RobustNotes" });
  els.storageLabel.textContent = "Anna account storage";
} catch {
  previewMode = true;
  els.storageLabel.textContent = "Preview saved in this browser";
}
await loadNotes();
