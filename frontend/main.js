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
    renderEndpoints(await res.json());
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
  tsTargetBox.hidden = tsLeakBox.hidden = true;
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
  } catch (err) {
    tsLeakBox.hidden = true;
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

// ----- 3. Chat -----
const messages = document.getElementById("messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");

function addMessage(text, who) {
  const div = document.createElement("div");
  div.className = `msg msg-${who}`;
  div.textContent = text; // textContent (not innerHTML) so user input can't inject HTML
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;

  addMessage(text, "user");
  chatInput.value = "";

  // TODO: replace this placeholder with a real call to the attached model.
  setTimeout(() => addMessage("No model attached yet — this is a placeholder reply.", "model"), 400);
});
