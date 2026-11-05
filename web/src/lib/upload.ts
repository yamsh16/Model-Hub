import { API_BASE, ApiError, SIGNED_OUT_EVENT, getToken, setToken } from "./api";
import type { Weights } from "./types";

export const WEIGHT_EXTENSIONS = [".safetensors", ".gguf"];

/**
 * A quick check before uploading, so an obviously wrong file fails instantly instead of after
 * minutes of transfer. The server repeats this and scans the file's contents: it is the authority.
 */
export function checkWeightsFile(file: { name: string; size: number }, maxBytes?: number): string | null {
  // Windows ignores trailing dots and spaces, so the server judges "m.pt." as "m.pt"; do the same.
  const name = file.name.trim().replace(/[ .]+$/, "").toLowerCase();
  if (!WEIGHT_EXTENSIONS.some((ext) => name.endsWith(ext))) {
    return "Only .safetensors and .gguf files are accepted. Pickle-based formats (.pt, .pkl, .bin) can run code when loaded, so convert the file to safetensors on your own machine first.";
  }
  if (file.size === 0) return "That file is empty.";
  if (maxBytes && file.size > maxBytes) return "That file is larger than the upload limit.";
  return null;
}

export type Upload = { promise: Promise<Weights>; abort: () => void };

/**
 * Send a weights file to the backend as the raw request body (so it streams to disk there),
 * reporting progress as a 0..1 fraction. fetch() cannot report upload progress, hence XMLHttpRequest.
 */
export function uploadWeights(file: File, name: string, onProgress: (fraction: number) => void): Upload {
  const xhr = new XMLHttpRequest();
  const query = new URLSearchParams({ name, filename: file.name });
  const promise = new Promise<Weights>((resolve, reject) => {
    xhr.open("POST", `${API_BASE}/weights?${query}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    const token = getToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: { detail?: unknown } = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // not JSON: fall through to the generic message
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as unknown as Weights);
      if (xhr.status === 401 && token) {
        setToken(null);
        window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
      }
      reject(new ApiError(typeof body.detail === "string" ? body.detail : "Upload failed.", xhr.status));
    };
    xhr.onerror = () => reject(new TypeError("Network error")); // shown as "could not reach the API"
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    xhr.send(file);
  });
  return { promise, abort: () => xhr.abort() };
}
