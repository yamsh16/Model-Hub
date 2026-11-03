"use client";

import { useRef, useState } from "react";
import { apiFetch } from "@/lib/api";
import { errorMessage, formatBytes, formatCount } from "@/lib/format";
import type { Weights } from "@/lib/types";
import { checkWeightsFile, uploadWeights, type Upload } from "@/lib/upload";
import { useWeights } from "@/lib/useWeights";
import { useAuth } from "../AuthProvider";
import { Button, Field, inputClass, Status, type StatusMessage } from "../ui";

/** Upload model weights (safetensors / GGUF). The server scans each file before keeping it. */
export default function WeightsPanel() {
  const { user } = useAuth();
  const { weights, reload } = useWeights();
  const [status, setStatus] = useState<StatusMessage>(null);
  const [progress, setProgress] = useState<number | null>(null); // null = not uploading
  const current = useRef<Upload | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const file = fileInput.current?.files?.[0];
    const name = String(new FormData(form).get("name") ?? "").trim();
    if (!user) return setStatus({ text: "Sign in first (top right) to upload a model.", kind: "error" });
    if (!file) return setStatus({ text: "Choose a .safetensors or .gguf file.", kind: "error" });
    const problem = checkWeightsFile(file);
    if (problem) return setStatus({ text: problem, kind: "error" });

    setProgress(0);
    setStatus({ text: "Uploading…" });
    const upload = uploadWeights(file, name || file.name, setProgress);
    current.current = upload;
    try {
      const w = await upload.promise;
      setStatus({
        text: `Saved "${w.name}" (${formatBytes(w.size_bytes)}). The file was scanned and is structurally valid.`,
        kind: "success",
      });
      form.reset();
      await reload();
    } catch (err) {
      const cancelled = err instanceof DOMException && err.name === "AbortError";
      setStatus(cancelled ? { text: "Upload cancelled." } : { text: errorMessage(err), kind: "error" });
    } finally {
      current.current = null;
      setProgress(null);
    }
  }

  async function remove(id: string) {
    try {
      await apiFetch(`/weights/${id}`, { method: "DELETE" });
    } finally {
      await reload();
    }
  }

  const uploading = progress !== null;
  return (
    <div>
      <h2 className="mb-1 text-xl font-bold">Upload model weights</h2>
      <p className="mb-4 text-sm text-muted">
        <span className="font-mono">.safetensors</span> and <span className="font-mono">.gguf</span> only — formats that hold data, not code.
        Every file is checked on the server before it is kept. Uploaded models are stored and described here;
        chatting with them and scoring them arrives with sandboxed serving.
      </p>
      <form onSubmit={submit}>
        <Field label="Weights file" htmlFor="w-file">
          <input
            ref={fileInput}
            id="w-file"
            type="file"
            accept={".safetensors,.gguf"}
            disabled={uploading}
            className="block w-full text-sm file:mr-3 file:rounded-lg file:border file:border-[#b8bfc9] file:bg-white file:px-3 file:py-2"
          />
        </Field>
        <Field label="Name" hint="(optional, defaults to the file name)" htmlFor="w-name">
          <div className="flex gap-2.5">
            <input id="w-name" name="name" type="text" placeholder="my-finetune" disabled={uploading} className={inputClass} />
            {uploading ? (
              <Button variant="outline" onClick={() => current.current?.abort()}>
                Cancel
              </Button>
            ) : (
              <Button type="submit">Upload</Button>
            )}
          </div>
        </Field>
      </form>
      {uploading && (
        <progress aria-label="Upload progress" value={progress} max={1} className="mt-3.5 h-2 w-full accent-teal" />
      )}
      <Status message={status} />

      <ul className="mt-3.5 flex flex-col gap-2" aria-label="Uploaded models">
        {weights.map((w) => (
          <WeightsItem key={w.id} w={w} onRemove={() => remove(w.id)} />
        ))}
      </ul>
    </div>
  );
}

function WeightsItem({ w, onRemove }: { w: Weights; onRemove: () => void }) {
  const dtypes = Object.entries(w.dtypes)
    .map(([k, n]) => `${k} ×${n}`)
    .join(", ");
  return (
    <li className="rounded-lg bg-card px-3.5 py-2.5 text-sm">
      <div className="flex items-center justify-between gap-2.5">
        <span className="font-semibold">{w.name}</span>
        <span className="text-muted">
          {w.format} · {formatBytes(w.size_bytes)}
        </span>
        <button type="button" onClick={onRemove} className="px-2 py-1 text-[0.85rem] text-muted hover:text-[#b3261e]">
          Remove
        </button>
      </div>
      <p className="mt-1 text-muted">
        {w.architecture ? `${w.architecture} · ` : ""}
        {formatCount(w.parameter_count)} parameters · {w.tensor_count} tensors · {dtypes}
      </p>
      <p className="mt-1 break-all font-mono text-xs text-muted">sha256 {w.sha256}</p>
      {w.warnings.map((warning, i) => (
        <p key={i} className="mt-1 text-[0.85rem] text-orange">
          ⚠ {warning}
        </p>
      ))}
    </li>
  );
}
