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

// ----- 2. Connect by endpoint / Hugging Face repo -----
document.getElementById("endpoint-form").addEventListener("submit", (e) => {
  e.preventDefault(); // stop the form from reloading the page
  const value = document.getElementById("endpoint").value.trim();
  if (!value) return;
  fileList.textContent = `Connecting to ${value}…`;
  // TODO: send the endpoint to the backend.
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
