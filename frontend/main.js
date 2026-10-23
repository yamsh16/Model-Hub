// Model Hub — front-end behaviour.
// Nothing talks to a server yet: these handlers only update the page.
// When the backend exists, the TODOs below are where the real requests go.

// ----- 1. Drag-and-drop file upload -----
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const fileList = document.getElementById("file-list");

const ALLOWED = [".pt", ".h5", ".onnx", ".safetensors", ".pkl", ".gguf"];

function showFiles(files) {
  const names = [...files].map((f) => f.name);
  const rejected = names.filter((n) => !ALLOWED.some((ext) => n.toLowerCase().endsWith(ext)));

  if (rejected.length) {
    fileList.textContent = `Unsupported: ${rejected.join(", ")}`;
    return;
  }
  fileList.textContent = names.join(", ");
  // TODO: upload the files to the backend for analysis.
}

// The browser opens dropped files by default; preventDefault stops that.
["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("is-dragging");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("is-dragging");
  })
);
dropzone.addEventListener("drop", (e) => showFiles(e.dataTransfer.files));
fileInput.addEventListener("change", () => showFiles(fileInput.files));

// ----- 2. Connect by endpoint -----
// The backend runs separately from the static frontend (see README), so it needs its own origin.
const API_BASE = "http://localhost:8001";

const endpointForm = document.getElementById("endpoint-form");
const endpointStatus = document.getElementById("endpoint-status");
const endpointList = document.getElementById("endpoint-list");

function setStatus(text, kind) {
  endpointStatus.textContent = text;
  endpointStatus.className = "form-status" + (kind ? ` is-${kind}` : "");
}

function renderEndpoints(endpoints) {
  endpointList.innerHTML = "";
  for (const ep of endpoints) {
    const li = document.createElement("li");

    const label = document.createElement("span");
    label.textContent = ep.name;

    const meta = document.createElement("span");
    meta.className = "ep-meta";
    meta.textContent = `${ep.model} · ${ep.latency_ms} ms`;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => deleteEndpoint(ep.id));

    li.append(label, meta, remove);
    endpointList.appendChild(li);
  }
}

async function loadEndpoints() {
  try {
    const res = await fetch(`${API_BASE}/endpoints`);
    if (!res.ok) return; // backend may not be running yet; fail quietly on load
    const eps = await res.json();
    renderEndpoints(eps);
    syncChatEndpoints(eps);
  } catch {
    // Backend unreachable — leave the list empty rather than block the page.
  }
}

async function deleteEndpoint(id) {
  try {
    await fetch(`${API_BASE}/endpoints/${id}`, { method: "DELETE" });
  } finally {
    loadEndpoints();
  }
}

endpointForm.addEventListener("submit", async (e) => {
  e.preventDefault(); // stop the form from reloading the page

  const name = document.getElementById("ep-name").value.trim();
  const url = document.getElementById("ep-url").value.trim();
  const model = document.getElementById("ep-model").value.trim();
  const api_key = document.getElementById("ep-key").value.trim();
  if (!name || !url || !model) return;

  const submitBtn = endpointForm.querySelector("button[type=submit]");
  submitBtn.disabled = true;
  setStatus("Connecting…");

  try {
    const res = await fetch(`${API_BASE}/endpoints`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, url, model, api_key: api_key || null }),
    });
    const body = await res.json();

    if (!res.ok) {
      setStatus(body.detail || "Could not attach that endpoint.", "error");
      return;
    }

    setStatus(`Attached "${body.name}" (${body.latency_ms} ms).`, "success");
    endpointForm.reset();
    loadEndpoints();
  } catch {
    setStatus("Could not reach the Model Hub API. Is the backend running?", "error");
  } finally {
    submitBtn.disabled = false;
  }
});

loadEndpoints();

// ----- 2b. Test set: CSV upload, target column, leakage check -----
const tsFile = document.getElementById("ts-file");
const tsTargetBox = document.getElementById("ts-target-box");
const tsTarget = document.getElementById("ts-target");
const tsLeakBox = document.getElementById("ts-leak-box");
const tsTrain = document.getElementById("ts-train");
const tsStatus = document.getElementById("ts-status");
const tsWarning = document.getElementById("ts-warning");
const tsEvalBox = document.getElementById("ts-eval-box");
const tsEndpoint = document.getElementById("ts-endpoint");
const tsRun = document.getElementById("ts-run");

let testsetId = null;

function setTsStatus(text, kind) {
  tsStatus.textContent = text;
  tsStatus.className = "form-status" + (kind ? ` is-${kind}` : "");
}

async function apiJson(path, options) {
  const res = await fetch(`${API_BASE}${path}`, options);
  const body = await res.json();
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Request failed.");
  return body;
}

tsFile.addEventListener("change", async () => {
  const file = tsFile.files[0];
  testsetId = null;
  tsTargetBox.hidden = tsLeakBox.hidden = tsEvalBox.hidden = true;
  tsWarning.textContent = "";
  if (!file) return setTsStatus("");

  const form = new FormData();
  form.append("file", file);
  setTsStatus("Uploading…");
  try {
    const ts = await apiJson("/testsets", { method: "POST", body: form });
    testsetId = ts.id;
    tsTarget.innerHTML = "";
    const placeholder = new Option("Choose a column…", "");
    placeholder.disabled = placeholder.selected = true;
    tsTarget.add(placeholder);
    for (const c of ts.columns) tsTarget.add(new Option(`${c.name} (${c.unique} distinct)`, c.name));
    tsTargetBox.hidden = false;
    setTsStatus(`Loaded ${ts.rows} rows, ${ts.columns.length} columns. Pick the target column.`, "success");
  } catch (err) {
    setTsStatus(err instanceof TypeError ? "Could not reach the Model Hub API. Is the backend running?" : err.message, "error");
  }
});

tsTarget.addEventListener("change", async () => {
  tsWarning.textContent = "";
  try {
    const ts = await apiJson(`/testsets/${testsetId}/target`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ column: tsTarget.value }),
    });
    const classes = Object.entries(ts.classes).map(([k, n]) => `${k}: ${n}`).join(", ");
    setTsStatus(`Target "${ts.target}" — ${classes}`, "success");
    tsLeakBox.hidden = false;
    await fillEndpointSelect();
  } catch (err) {
    tsLeakBox.hidden = tsEvalBox.hidden = true;
    setTsStatus(err.message, "error");
  }
});

tsTrain.addEventListener("change", async () => {
  const file = tsTrain.files[0];
  tsWarning.textContent = "";
  if (!file || !testsetId) return;

  const form = new FormData();
  form.append("train_file", file);
  try {
    const r = await apiJson(`/testsets/${testsetId}/leakage`, { method: "POST", body: form });
    if (r.warning) {
      tsWarning.textContent = `⚠ ${r.warning}`;
      tsWarning.className = "form-status is-error";
    } else {
      tsWarning.textContent = `No overlap between ${r.train_rows} training rows and the test set.`;
      tsWarning.className = "form-status is-success";
    }
  } catch (err) {
    tsWarning.textContent = err.message;
    tsWarning.className = "form-status is-error";
  }
});

// ----- 2c. Evaluate: run the test set through an endpoint and show real metrics -----
async function fillEndpointSelect() {
  const eps = await apiJson("/endpoints");
  tsEndpoint.innerHTML = "";
  for (const ep of eps) tsEndpoint.add(new Option(`${ep.name} (${ep.model})`, ep.id));
  tsEvalBox.hidden = eps.length === 0;
  if (eps.length === 0) setTsStatus("Attach an endpoint above to evaluate this test set.");
}

const pct = (x) => `${(x * 100).toFixed(1)}%`;

function fillTable(table, header, rows) {
  table.innerHTML = "";
  for (const [i, cells] of [header, ...rows].entries()) {
    const tr = table.insertRow();
    for (const c of cells) {
      const cell = document.createElement(i === 0 ? "th" : "td");
      cell.textContent = c; // textContent: class labels come from user data
      tr.appendChild(cell);
    }
  }
}

function renderReport(r) {
  document.getElementById("stat-accuracy").textContent = pct(r.accuracy);
  document.getElementById("stat-accuracy-note").textContent =
    `Macro F1 ${r.f1_macro.toFixed(3)} · ROC-AUC ${r.roc_auc === null ? "n/a (endpoint returns labels only)" : r.roc_auc.toFixed(3)}`;

  const scope = r.sampled ? `a random ${r.n} of ${r.total_rows} rows` : `all ${r.n} rows`;
  const bad = r.unparsed ? ` ${r.unparsed} replies named no single class and were counted wrong.` : "";
  document.getElementById("eval-summary").textContent = `Target "${r.target}", scored on ${scope}.${bad}`;

  fillTable(
    document.getElementById("eval-confusion"),
    ["", ...r.classes],
    r.confusion_matrix.map((row, i) => [r.classes[i], ...row])
  );
  fillTable(
    document.getElementById("eval-classes"),
    ["Class", "Precision", "Recall", "F1", "Rows"],
    r.per_class.map((c) => [c.label, c.precision.toFixed(3), c.recall.toFixed(3), c.f1.toFixed(3), c.support])
  );
  document.getElementById("eval-panel").hidden = false;
  document.getElementById("report").scrollIntoView({ behavior: "smooth" });
}

tsRun.addEventListener("click", async () => {
  if (!testsetId || !tsEndpoint.value) return;
  tsRun.disabled = true;
  setTsStatus("Running the test set through the endpoint… this can take a minute.");
  try {
    const report = await apiJson(`/testsets/${testsetId}/evaluate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint_id: tsEndpoint.value }),
    });
    renderReport(report);
    setTsStatus(`Done — accuracy ${pct(report.accuracy)}. See the report below.`, "success");
  } catch (err) {
    setTsStatus(err instanceof TypeError ? "Could not reach the Model Hub API. Is the backend running?" : err.message, "error");
  } finally {
    tsRun.disabled = false;
  }
});

// ----- 2d. Training log: CSV upload / MLflow import -> real loss curve -----
const lossChart = document.getElementById("loss-chart");
const logFile = document.getElementById("log-file");
const logStatus = document.getElementById("log-status");
const chartNote = document.getElementById("chart-note");
const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(name, attrs, text) {
  const el = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}

const fmt = (n) => (Math.abs(n) >= 100 || Number.isInteger(n) ? String(Math.round(n)) : n.toPrecision(3));

// Split a series into runs of consecutive numbers so gaps (null) break the line.
function segments(xs, ys, px, py) {
  const out = [];
  let cur = [];
  ys.forEach((y, i) => {
    if (y === null) {
      if (cur.length) out.push(cur);
      cur = [];
    } else {
      cur.push(`${px(xs[i]).toFixed(1)},${py(y).toFixed(1)}`);
    }
  });
  if (cur.length) out.push(cur);
  return out;
}

function renderLossChart(log) {
  const lines = [
    { name: log.train_loss, cls: "line-train", label: "legend-train", fallback: "Training" },
    { name: log.val_loss, cls: "line-val", label: "legend-val", fallback: "Validation" },
  ].filter((l) => l.name);
  for (const l of [
    { label: "legend-train", fallback: "Training" },
    { label: "legend-val", fallback: "Validation" },
  ]) {
    const found = lines.find((x) => x.label === l.label);
    document.getElementById(l.label).textContent = found ? found.name : `${l.fallback} (not in log)`;
  }

  lossChart.innerHTML = "";
  if (lines.length === 0) {
    chartNote.textContent = `No loss column found. Columns: ${Object.keys(log.series).join(", ")}`;
    return;
  }

  const L = 52, R = 490, T = 10, B = 170; // plot box inside the 500x200 viewBox
  const all = lines.flatMap((l) => log.series[l.name]).filter((v) => v !== null);
  let yMin = Math.min(...all), yMax = Math.max(...all);
  if (yMin === yMax) { yMin -= 0.5; yMax += 0.5; }
  const xMin = Math.min(...log.x), xMax = Math.max(...log.x);
  const px = (x) => (xMax === xMin ? (L + R) / 2 : L + ((x - xMin) / (xMax - xMin)) * (R - L));
  const py = (y) => B - ((y - yMin) / (yMax - yMin)) * (B - T);

  lossChart.append(svgEl("path", { d: `M${L} ${T} V${B} H${R}`, fill: "none", class: "axis" }));
  lossChart.append(
    svgEl("text", { x: 4, y: T + 8, class: "tick-label" }, fmt(yMax)),
    svgEl("text", { x: 4, y: B, class: "tick-label" }, fmt(yMin)),
    svgEl("text", { x: L, y: 192, class: "tick-label" }, fmt(xMin)),
    svgEl("text", { x: R, y: 192, class: "tick-label", "text-anchor": "end" }, fmt(xMax)),
    svgEl("text", { x: (L + R) / 2, y: 198, class: "axis-label", "text-anchor": "middle" }, log.x_name)
  );
  for (const l of lines) {
    const ys = log.series[l.name];
    for (const seg of segments(log.x, ys, px, py)) {
      // A lone point has no line to draw, so show it as a dot.
      if (seg.length === 1) {
        const [cx, cy] = seg[0].split(",");
        lossChart.append(svgEl("circle", { cx, cy, r: 3, class: l.cls + " dot" }));
      } else {
        lossChart.append(svgEl("polyline", { points: seg.join(" "), fill: "none", class: l.cls }));
      }
    }
  }
  lossChart.setAttribute(
    "aria-label",
    `Loss over ${log.x_name}: ${lines.map((l) => l.name).join(" and ")}`
  );
  const last = (name) => [...log.series[name]].reverse().find((v) => v !== null);
  const summary = lines.map((l) => `${l.name} ends at ${fmt(last(l.name))}`).join(", ");
  chartNote.textContent = `${log.filename}: ${log.x.length} points. ${summary}.`;
}

function setLogStatus(text, kind) {
  logStatus.textContent = text;
  logStatus.className = "form-status" + (kind ? ` is-${kind}` : "");
}

function networkMessage(err) {
  return err instanceof TypeError ? "Could not reach the Model Hub API. Is the backend running?" : err.message;
}

logFile.addEventListener("change", async () => {
  const file = logFile.files[0];
  if (!file) return setLogStatus("");
  const form = new FormData();
  form.append("file", file);
  setLogStatus("Reading log…");
  try {
    const log = await apiJson("/logs", { method: "POST", body: form });
    renderLossChart(log);
    setLogStatus(`Loaded ${log.x.length} ${log.x_name}s, ${Object.keys(log.series).length} metrics.`, "success");
  } catch (err) {
    setLogStatus(networkMessage(err), "error");
  }
});

document.getElementById("mlflow-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button[type=submit]");
  const token = document.getElementById("ml-token").value.trim();
  btn.disabled = true;
  setLogStatus("Importing from MLflow…");
  try {
    const log = await apiJson("/logs/mlflow", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tracking_uri: document.getElementById("ml-uri").value.trim(),
        run_id: document.getElementById("ml-run").value.trim(),
        token: token || null,
      }),
    });
    renderLossChart(log);
    document.getElementById("ml-token").value = "";
    setLogStatus(`Imported ${Object.keys(log.series).length} metrics over ${log.x.length} steps.`, "success");
  } catch (err) {
    setLogStatus(networkMessage(err), "error");
  } finally {
    btn.disabled = false;
  }
});

// ----- 3. Chat -----
// The server keeps no chat state; the page sends the whole conversation each turn and keeps
// the history per endpoint in localStorage, so it survives a reload.
const messages = document.getElementById("messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatEndpoint = document.getElementById("chat-endpoint");
const chatClear = document.getElementById("chat-clear");
const chatStatus = document.getElementById("chat-status");
const chatSend = chatForm.querySelector("button[type=submit]");

const HISTORY_KEY = "modelhub-chat-";
let history = []; // [{role: "user" | "assistant", content}]
let chatBusy = false;

function loadHistory() {
  try {
    history = JSON.parse(localStorage.getItem(HISTORY_KEY + chatEndpoint.value)) || [];
  } catch {
    history = [];
  }
  messages.innerHTML = "";
  for (const m of history) addMessage(m.content, m.role === "user" ? "user" : "model");
}

function saveHistory() {
  try {
    localStorage.setItem(HISTORY_KEY + chatEndpoint.value, JSON.stringify(history));
  } catch {
    // storage full or blocked: chat still works, it just won't survive a reload
  }
}

function addMessage(text, who) {
  const div = document.createElement("div");
  div.className = `msg msg-${who}`;
  div.textContent = text; // textContent (not innerHTML) so user input can't inject HTML
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

function syncChatEndpoints(eps) {
  const previous = chatEndpoint.value;
  chatEndpoint.innerHTML = "";
  for (const ep of eps) chatEndpoint.add(new Option(`${ep.name} (${ep.model})`, ep.id));
  chatEndpoint.value = eps.some((ep) => ep.id === previous) ? previous : eps[0]?.id ?? "";
  const none = eps.length === 0;
  chatInput.disabled = chatSend.disabled = chatClear.disabled = none;
  chatInput.placeholder = none ? "Attach a model first" : "Message your model";
  if (none) {
    messages.innerHTML = "";
    history = [];
  } else if (chatEndpoint.value !== previous) {
    loadHistory();
  }
}

// Read an SSE response body, calling onEvent(string) for each `data:` payload.
async function readSse(res, onEvent) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data:")) onEvent(line.slice(5).trim());
      }
    }
  }
}

chatEndpoint.addEventListener("change", loadHistory);
chatClear.addEventListener("click", () => {
  history = [];
  saveHistory();
  messages.innerHTML = "";
  chatStatus.textContent = "";
});

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text || chatBusy || !chatEndpoint.value) return;

  chatBusy = true;
  chatSend.disabled = true;
  chatStatus.textContent = "";
  chatStatus.className = "form-status";
  chatInput.value = "";

  const endpointId = chatEndpoint.value;
  history.push({ role: "user", content: text });
  addMessage(text, "user");
  const bubble = addMessage("…", "model");
  let reply = "";

  try {
    const res = await fetch(`${API_BASE}/endpoints/${endpointId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history, stream: true }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : "The model did not answer.");
    }
    await readSse(res, (data) => {
      if (data === "[DONE]") return;
      const evt = JSON.parse(data);
      if (evt.error) throw new Error(evt.error);
      reply += evt.delta;
      bubble.textContent = reply;
      messages.scrollTop = messages.scrollHeight;
    });
    if (!reply) throw new Error("The model sent an empty reply.");
    history.push({ role: "assistant", content: reply });
  } catch (err) {
    // Keep whatever streamed in; drop the unanswered user turn only if nothing came back.
    if (reply) history.push({ role: "assistant", content: reply });
    else {
      history.pop();
      bubble.remove();
    }
    chatStatus.textContent =
      err instanceof TypeError ? "Could not reach the Model Hub API. Is the backend running?" : err.message;
    chatStatus.className = "form-status is-error";
  } finally {
    // Only persist if the user didn't switch endpoints mid-reply.
    if (chatEndpoint.value === endpointId) saveHistory();
    chatBusy = false;
    chatSend.disabled = !chatEndpoint.value;
    chatInput.focus();
  }
});
