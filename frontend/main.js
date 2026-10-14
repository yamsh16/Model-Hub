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
