"use client";

import { useState } from "react";
import { apiFetch, apiJson } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import type { Endpoint } from "@/lib/types";
import { useAuth } from "../AuthProvider";
import { Button, Field, inputClass, Status, type StatusMessage } from "../ui";

/** Attach an OpenAI-compatible chat endpoint, and list / remove the ones already attached. */
export default function EndpointPanel({ endpoints, reload }: { endpoints: Endpoint[]; reload: () => Promise<void> }) {
  const { user } = useAuth();
  const [status, setStatus] = useState<StatusMessage>(null);
  const [busy, setBusy] = useState(false);

  async function attach(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    const text = (key: string) => String(data.get(key) ?? "").trim();
    if (!user) return setStatus({ text: "Sign in first (top right) to attach a model.", kind: "error" });

    setBusy(true);
    setStatus({ text: "Connecting…" });
    try {
      const ep = await apiJson<Endpoint>("/endpoints", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: text("name"), url: text("url"), model: text("model"), api_key: text("api_key") || null }),
      });
      setStatus({ text: `Attached "${ep.name}" (${ep.latency_ms} ms).`, kind: "success" });
      form.reset();
      await reload();
    } catch (err) {
      setStatus({ text: errorMessage(err), kind: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    try {
      await apiFetch(`/endpoints/${id}`, { method: "DELETE" });
    } finally {
      await reload();
    }
  }

  return (
    <div>
      <h2 className="mb-1 text-xl font-bold">1. Connect your model</h2>
      <p className="mb-4 text-sm text-muted">
        Any OpenAI-compatible chat URL. The API key is kept on the server and never sent back to the browser.
      </p>
      <form onSubmit={attach}>
        <Field label="Name" htmlFor="ep-name">
          <input id="ep-name" name="name" type="text" placeholder="my-llm" required className={inputClass} />
        </Field>
        <Field label="API endpoint (OpenAI-compatible chat URL)" htmlFor="ep-url">
          <input id="ep-url" name="url" type="text" placeholder="https://api.example.com/v1/chat/completions" required className={inputClass} />
        </Field>
        <Field label="Model" htmlFor="ep-model">
          <input id="ep-model" name="model" type="text" placeholder="gpt-x" required className={inputClass} />
        </Field>
        <Field label="API key" hint="(optional, kept server-side only)" htmlFor="ep-key">
          <div className="flex gap-2.5">
            <input id="ep-key" name="api_key" type="password" placeholder="sk-..." autoComplete="off" className={inputClass} />
            <Button type="submit" disabled={busy}>
              Attach
            </Button>
          </div>
        </Field>
      </form>
      <Status message={status} />

      <ul className="mt-3.5 flex flex-col gap-2" aria-label="Attached endpoints">
        {endpoints.map((ep) => (
          <li key={ep.id} className="flex items-center justify-between gap-2.5 rounded-lg bg-card px-3.5 py-2.5 text-sm">
            <span>{ep.name}</span>
            <span className="text-muted">
              {ep.model} · {ep.latency_ms} ms
            </span>
            <button type="button" onClick={() => remove(ep.id)} className="px-2 py-1 text-[0.85rem] text-muted hover:text-[#b3261e]">
              Remove
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
