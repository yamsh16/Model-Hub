"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { apiFetch, apiJson } from "@/lib/api";
import { errorMessage, formatBytes, formatCount } from "@/lib/format";
import type { ServeStatus, Weights } from "@/lib/types";
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
        Every file is checked on the server before it is kept. A GGUF model can then be started in an isolated
        sandbox (no network, capped memory and CPU) and chatted with from the Chat page. Safetensors files are
        stored but cannot be run yet. Pickle-based files (.pt, .pkl, .bin and similar) are refused outright because
        they can run code when loaded: convert them to safetensors on your own machine first.
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
          <WeightsItem key={w.id} w={w} onRemove={() => remove(w.id)} onServeChange={reload} />
        ))}
      </ul>
    </div>
  );
}

function WeightsItem({ w, onRemove, onServeChange }: { w: Weights; onRemove: () => void; onServeChange: () => void }) {
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
      {w.format === "gguf" ? (
        <ServeControls w={w} onChange={onServeChange} />
      ) : (
        <p className="mt-2 text-[0.85rem] text-muted">Stored. Running safetensors models is not supported yet.</p>
      )}
    </li>
  );
}

/** Start / stop the sandbox for one GGUF model. Starting loads the model, so it can take a while. */
function ServeControls({ w, onChange }: { w: Weights; onChange: () => void }) {
  const [serve, setServe] = useState<ServeStatus | null>(null);
  const [template, setTemplate] = useState("chatml");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<StatusMessage>(null);

  useEffect(() => {
    let cancelled = false;
    apiJson<ServeStatus>(`/weights/${w.id}/serve`)
      .then((s) => {
        if (cancelled) return;
        setServe(s);
        if (s.chat_template) setTemplate(s.chat_template);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [w.id, w.serve_state]);

  async function start() {
    setBusy(true);
    setMessage({ text: "Starting the sandbox and loading the model…" });
    try {
      setServe(
        await apiJson<ServeStatus>(`/weights/${w.id}/serve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ chat_template: template }),
        }),
      );
      setMessage(null);
      onChange();
    } catch (err) {
      setMessage({ text: errorMessage(err), kind: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    try {
      await apiFetch(`/weights/${w.id}/serve`, { method: "DELETE" });
      setMessage(null);
      onChange();
    } finally {
      setBusy(false);
    }
  }

  if (!serve) return null;
  const running = serve.state === "running";
  return (
    <div className="mt-2.5 border-t border-line pt-2.5">
      <div className="flex flex-wrap items-center gap-2.5">
        <span className={`rounded-md px-2 py-0.5 text-[0.8rem] font-semibold ${running ? "bg-teal-soft text-teal" : "bg-white text-muted"}`}>
          {running ? `Running · ${serve.chat_template}` : serve.state === "starting" ? "Starting…" : "Not running"}
        </span>
        {!running && (
          <label className="flex items-center gap-1.5 text-[0.85rem] text-muted">
            Chat format
            <select
              aria-label={`Chat format for ${w.name}`}
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
              disabled={busy}
              className="rounded-md border border-[#b8bfc9] bg-white px-2 py-1 text-[0.85rem] text-ink"
            >
              {serve.templates.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
        )}
        {running ? (
          <>
            <Link href="/chat" className="text-[0.85rem] font-semibold text-teal hover:underline">
              Open chat
            </Link>
            <button type="button" onClick={stop} disabled={busy} className="text-[0.85rem] text-muted hover:text-[#b3261e]">
              Stop
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={start}
            disabled={busy}
            className="rounded-md bg-ink px-3 py-1 text-[0.85rem] font-semibold text-white disabled:opacity-50"
          >
            Start
          </button>
        )}
      </div>
      <p className="mt-1.5 text-[0.8rem] text-muted">
        Runs isolated: no network, {serve.memory} memory, {serve.cpus} CPUs. Stops by itself after{" "}
        {Math.round(serve.idle_stop_seconds / 60)} min without use. The chat format must match the model: the
        template inside the file is ignored for safety.
      </p>
      <Status message={message} />
    </div>
  );
}
