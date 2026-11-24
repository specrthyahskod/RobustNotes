// Run `anna-app dev` first, then start Chrome with CDP enabled on port 9332.
// This smoke suite uses the harness's in-memory storage, never account data.
const CDP_URL = process.env.ROBUSTNOTES_CDP_URL || "http://127.0.0.1:9332";
const HARNESS_URL = process.env.ROBUSTNOTES_HARNESS_URL || "http://localhost:5180/";

const targets = await fetch(`${CDP_URL}/json`).then((response) => response.json());
const target = targets.find((entry) => entry.type === "page" && entry.url === HARNESS_URL);
if (!target) throw new Error(`Open ${HARNESS_URL} in the test browser first.`);

const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let sequence = 0;
const pending = new Map();
const exceptions = [];
socket.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.method === "Runtime.exceptionThrown") {
    const details = message.params.exceptionDetails;
    exceptions.push(details.exception?.description || details.text || "Unknown runtime exception");
  }
  if (!message.id || !pending.has(message.id)) return;
  const handlers = pending.get(message.id);
  pending.delete(message.id);
  message.error ? handlers.reject(new Error(message.error.message)) : handlers.resolve(message.result);
};

function cdp(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
async function app(code) {
  const expression = `(()=>{const d=document.querySelector("iframe")?.contentDocument;if(!d)return null;${code}})()`;
  const response = await cdp("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
  return response.result.value;
}

await cdp("Runtime.enable");
await cdp("Page.enable");
await cdp("Page.reload", { ignoreCache: true });
await delay(700);
for (let attempt = 0; attempt < 40; attempt += 1) {
  const status = await app('return d.querySelector("#save-state")?.textContent');
  if (status === "Ready" || status === "Preview ready") break;
  await delay(200);
}

const results = [];
async function test(name, expression) {
  try {
    const actual = await app(expression);
    results.push({ name, passed: actual === true, actual });
  } catch (error) {
    results.push({ name, passed: false, error: String(error) });
  }
}
async function act(expression) { return app(`${expression};return true`); }

for (const [name, expression] of [
  ["brand title", 'd.title==="RobustNotes"'],
  ["logo loaded", 'd.querySelector(".brand img").complete&&d.querySelector(".brand img").naturalWidth>0'],
  ["application shell", '!!d.querySelector("#app")'],
  ["sidebar visible", '!d.querySelector(".sidebar").hidden'],
  ["search enabled", '!d.querySelector("#search").disabled'],
  ["new-document button enabled", '!d.querySelector("#new-document").disabled'],
  ["fresh landing visible", '!d.querySelector("#empty-state").hidden'],
  ["dashboard initially hidden", 'd.querySelector("#dashboard").hidden'],
  ["editor initially hidden", 'd.querySelector("#document-view").hidden'],
  ["startup status leaves connecting", '!d.querySelector("#storage-label").textContent.includes("Connecting")'],
]) await test(`startup: ${name}`, `return ${expression}`);

await act('d.querySelector("#empty-state [data-create=notebook]").click()');
for (const [name, expression] of [
  ["notebook editor visible", '!d.querySelector("#document-view").hidden'],
  ["notebook canvas visible", '!d.querySelector("#canvas-viewport").hidden'],
  ["notebook pages rail visible", '!d.querySelector("#page-rail").hidden'],
  ["canvas toolbar visible", '!d.querySelector("#canvas-toolbar").hidden'],
  ["text toolbar hidden", 'd.querySelector("#text-toolbar").hidden'],
  ["notebook title set", 'd.querySelector("#document-crumb").textContent==="My notebook"'],
  ["initial page exists", 'd.querySelectorAll(".page-thumb").length===1'],
  ["page count says one", 'd.querySelector("#canvas-page-count").textContent==="1 page"'],
  ["pen is selected", 'd.querySelector("[data-tool=pen]").classList.contains("selected")'],
  ["AI button enabled", '!d.querySelector("#open-ai").disabled'],
]) await test(`notebook: ${name}`, `return ${expression}`);

await act('d.querySelector("#add-page").click()');
for (const [name, expression] of [
  ["adding a page updates thumbnails", 'd.querySelectorAll(".page-thumb").length===2'],
  ["adding a page updates count", 'd.querySelector("#canvas-page-count").textContent==="2 pages"'],
  ["new page becomes active", 'd.querySelector(".page-thumb.active span").textContent==="Page 2"'],
]) await test(`pages: ${name}`, `return ${expression}`);
await act('d.querySelector("#duplicate-page").click()');
for (const [name, expression] of [
  ["duplicating adds thumbnail", 'd.querySelectorAll(".page-thumb").length===3'],
  ["duplicating updates count", 'd.querySelector("#canvas-page-count").textContent==="3 pages"'],
  ["duplicate is labelled", 'd.querySelector(".page-thumb.active span").textContent.includes("copy")'],
  ["clear-page control exists", '!!d.querySelector("#clear-page")'],
]) await test(`pages: ${name}`, `return ${expression}`);
await act('d.querySelectorAll(".page-thumb")[0].click()');
await test("navigation selects clicked page", 'return d.querySelectorAll(".page-thumb")[0].classList.contains("active")');
await test("navigation preserves page count", 'return d.querySelectorAll(".page-thumb").length===3');

for (const tool of ["highlighter", "laser", "eraser", "line", "shape", "lasso", "text", "scale", "compass", "pen"]) {
  await act(`d.querySelector('[data-tool="${tool}"]').click()`);
  await test(`tool selection: ${tool}`, `return d.querySelector('[data-tool="${tool}"]').classList.contains("selected")`);
}
await act('d.querySelector("[data-tool=shape]").click()');
await test("shape menu offers ellipse", 'return [...d.querySelector("#subtool").options].some((option)=>option.value==="ellipse")');
await act('d.querySelector("[data-tool=lasso]").click()');
await test("lasso menu offers rectangle", 'return [...d.querySelector("#subtool").options].some((option)=>option.value==="rectangle")');
await act('d.querySelector("[data-tool=pen]").click();d.querySelectorAll(".swatches button")[1].click()');
await test("preset ink selection updates", 'return !!d.querySelector(".swatches button.selected")');
await act('const color=d.querySelector("#custom-color");color.value="#bb22aa";color.dispatchEvent(new Event("input",{bubbles:true}))');
await test("custom color clears preset swatch", 'return !d.querySelector(".swatches button.selected")');
await act('const size=d.querySelector("#ink-size");size.value="9";size.dispatchEvent(new Event("input",{bubbles:true}))');
await test("ink size label updates", 'return d.querySelector("#size-label").textContent==="9"');
await act('const zoom=d.querySelector("#zoom-number"),old=Number(zoom.value);d.querySelector("#zoom-in").click();window.__zoomInWorked=Number(zoom.value)>old');
await test("zoom-in changes zoom", 'return window.__zoomInWorked===true');
await act('const zoom=d.querySelector("#zoom-number");zoom.value="120";zoom.dispatchEvent(new Event("change",{bubbles:true}))');
await test("typed zoom is accepted", 'return d.querySelector("#zoom-number").value==="120"');
await act('d.querySelector("#zoom-fit").click()');
await test("fit zoom remains in allowed range", 'return Number(d.querySelector("#zoom-number").value)>=40&&Number(d.querySelector("#zoom-number").value)<=140');
await act('const menu=d.querySelector("#subtool");menu.value="template:ruled";menu.dispatchEvent(new Event("change",{bubbles:true}))');
await test("paper template can be selected", 'return d.querySelector("#subtool").value==="template:ruled"');
await act('d.dispatchEvent(new KeyboardEvent("keydown",{key:"p",bubbles:true}))');
await test("pen keyboard shortcut works", 'return d.querySelector("[data-tool=pen]").classList.contains("selected")');
await test("canvas backing store is initialized", 'return d.querySelector("#page-canvas").width>0&&d.querySelector("#page-canvas").height>0');

const point = JSON.parse(await app('const canvas=d.querySelector("#page-canvas").getBoundingClientRect(),view=d.querySelector("#canvas-viewport").getBoundingClientRect(),frame=window.parent.document.querySelector("iframe").getBoundingClientRect(),host=window.parent;const left=Math.max(canvas.left,view.left,0),right=Math.min(canvas.right,view.right,host.innerWidth-frame.left),top=Math.max(canvas.top,view.top,0),bottom=Math.min(canvas.bottom,view.bottom,host.innerHeight-frame.top);const x=(left+right)/2,y=top+Math.min(50,(bottom-top)/3);return JSON.stringify({x:frame.left+x,y:frame.top+y,localX:x,localY:y,hit:d.elementFromPoint(x,y)?.id})'));
await test("pointer test targets canvas", `return ${JSON.stringify(point.hit)}==="page-canvas"`);
await app('window.__inkBefore=d.querySelector("#page-canvas").toDataURL();return true');
for (const params of [
  { type: "mouseMoved", x: point.x, y: point.y },
  { type: "mousePressed", x: point.x, y: point.y, button: "left", buttons: 1, clickCount: 1 },
  { type: "mouseMoved", x: point.x + 45, y: point.y + 35, button: "left", buttons: 1 },
  { type: "mouseReleased", x: point.x + 45, y: point.y + 35, button: "left", buttons: 0 },
]) await cdp("Input.dispatchMouseEvent", params);
await delay(200);
await test("real pointer stroke changes canvas", 'return d.querySelector("#page-canvas").toDataURL()!==window.__inkBefore');
await test("stroke enables undo", 'return !d.querySelector("#undo").disabled');
await act('d.querySelector("#undo").click()');
await test("undo enables redo", 'return !d.querySelector("#redo").disabled');
await act('d.querySelector("#redo").click()');
await test("redo restores undo history", 'return !d.querySelector("#undo").disabled');

await act('d.querySelector("#dashboard [data-create=text]").click()');
for (const [name, expression] of [
  ["text editor visible", '!d.querySelector("#text-editor-wrap").hidden'],
  ["text toolbar visible", '!d.querySelector("#text-toolbar").hidden'],
  ["canvas toolbar hidden", 'd.querySelector("#canvas-toolbar").hidden'],
]) await test(`text file: ${name}`, `return ${expression}`);
await act('const editor=d.querySelector("#text-editor");editor.innerText="Biology revision: cell structure";editor.dispatchEvent(new InputEvent("input",{bubbles:true,inputType:"insertText"}))');
await test("text editor accepts input", 'return d.querySelector("#text-editor").innerText.includes("cell structure")');
await act('const search=d.querySelector("#search");search.value="Biology";search.dispatchEvent(new Event("input",{bubbles:true}))');
await test("search finds matching note", 'return d.querySelectorAll(".note-item").length===1');
await act('const search=d.querySelector("#search");search.value="no-match";search.dispatchEvent(new Event("input",{bubbles:true}))');
await test("search filters unmatched note", 'return d.querySelectorAll(".note-item").length===0');
await act('const search=d.querySelector("#search");search.value="";search.dispatchEvent(new Event("input",{bubbles:true}));d.querySelector("#open-ai").click()');
await test("AI panel opens", 'return !d.querySelector("#ai-panel").hidden');
await act('d.querySelector("#close-ai").click()');
await test("AI panel closes", 'return d.querySelector("#ai-panel").hidden');
await act('d.querySelector("#analyze-text").click()');
await delay(1500);
await test("notes-core analysis responds", 'return d.querySelector("#toast").textContent.includes("words")');

await act('d.querySelector("#dashboard [data-create=whiteboard]").click()');
await delay(100);
for (const [name, expression] of [
  ["whiteboard canvas visible", '!d.querySelector("#canvas-viewport").hidden'],
  ["whiteboard page rail hidden", 'd.querySelector("#page-rail").hidden'],
  ["whiteboard title correct", 'd.querySelector("#document-crumb").textContent==="My whiteboard"'],
  ["whiteboard starts with one page", 'd.querySelector("#canvas-page-count").textContent==="1 page"'],
  ["new whiteboard page fits viewport", 'd.querySelector(".canvas-wrap").getBoundingClientRect().width<=d.querySelector("#canvas-viewport").clientWidth-5'],
]) await test(`whiteboard: ${name}`, `return ${expression}`);
await act('d.querySelector("#add-page").click()');
await test("whiteboard can add pages", 'return d.querySelector("#canvas-page-count").textContent==="2 pages"');
for (const [name, expression] of [
  ["search remains available", '!!d.querySelector("#search")'],
  ["canvas tools remain available", 'd.querySelectorAll("#canvas-toolbar button").length>=10'],
  ["PDF import remains available", '!!d.querySelector("#pdf-input")'],
  ["OCR control remains available", '!!d.querySelector("#ocr")'],
]) await test(`core controls: ${name}`, `return ${expression}`);

await delay(4000);
await test("autosave completes without ETag error", 'return d.querySelector("#save-state").textContent==="All changes saved"');
const storedJson = await app('return d.defaultView.eval(\'import("/static/anna-apps/_sdk/latest/index.js").then(async m=>{const api=await m.AnnaAppRuntime.connect(),r=await api.storage.get({scope:"app",key:"robustnotes:studio:v2"});return JSON.stringify({hasValue:Object.prototype.hasOwnProperty.call(r,"value"),docs:r.value?.docs?.length,activeType:r.value?.docs?.find(x=>x.id===r.value.activeDocId)?.type,activePageId:r.value?.activePageId,activePageCount:r.value?.docs?.find(x=>x.id===r.value.activeDocId)?.pages?.length})})\')');
const stored = JSON.parse(storedJson);
for (const [name, passed] of [
  ["workspace value was written to SDK storage", stored.hasValue === true],
  ["all three document types were saved", stored.docs === 3],
  ["active whiteboard navigation was saved", stored.activeType === "whiteboard"],
  ["active whiteboard pages were saved", stored.activePageCount === 2 && typeof stored.activePageId === "string"],
]) results.push({ name: `storage: ${name}`, passed, actual: stored });

const runtimeErrors = exceptions.filter((message) => message && !message.includes("ResizeObserver loop"));
const failed = results.filter((result) => !result.passed);
console.log(JSON.stringify({ total: results.length, passed: results.length - failed.length, failed, runtimeErrors }, null, 2));
socket.close();
if (failed.length || runtimeErrors.length) process.exitCode = 1;
