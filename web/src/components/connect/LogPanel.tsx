"use client";

import Link from "next/link";
import { useState } from "react";
import { apiJson } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import type { TrainingLog } from "@/lib/types";
import { useAuth } from "../AuthProvider";
import { useWorkspace } from "../WorkspaceProvider";
import { Button, buttonClass, Field, inputClass, Status, type StatusMessage } from "../ui";

/** Load a training log (CSV upload or MLflow run); the report draws its loss curve and estimate. */
export default function LogPanel() {
  const { user } = useAuth();
  const { log, setLog } = useWorkspace();
  const [status, setStatus] = useState<StatusMessage>(null);
  const [busy, setBusy] = useState(false);

  async function loaded(request: Promise<TrainingLog>, done: (log: TrainingLog) => string) {
    try {
      const next = await request;
      setLog(next);
      setStatus({ text: done(next), kind: "success" });
    } catch (err) {
      setStatus({ text: errorMessage(err), kind: "error" });
    }
  }

  async function uploadCsv(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return setStatus(null);
    if (!user) return setStatus({ text: "Sign in first (top right) to upload a log.", kind: "error" });
    const form = new FormData();
    form.append("file", file);
    setStatus({ text: "Reading log…" });
    await loaded(apiJson<TrainingLog>("/logs", { method: "POST", body: form }), (l) => `Loaded ${l.x.length} ${l.x_name}s, ${Object.keys(l.series).length} metrics.`);
  }

  async function importMlflow(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    const text = (key: string) => String(data.get(key) ?? "").trim();
    if (!user) return setStatus({ text: "Sign in first (top right) to import a run.", kind: "error" });
    setBusy(true);
    setStatus({ text: "Importing from MLflow…" });
    await loaded(
      apiJson<TrainingLog>("/logs/mlflow", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tracking_uri: text("uri"), run_id: text("run"), token: text("token") || null }),
      }),
      (l) => `Imported ${Object.keys(l.series).length} metrics over ${l.x.length} steps.`,
    );
    form.querySelector<HTMLInputElement>("[name=token]")!.value = "";
    setBusy(false);
  }

  return (
    <div>
      <h2 className="mb-1 text-xl font-bold">3. Training log</h2>
      <p className="mb-4 text-sm text-muted">Optional. Gives the report a loss curve and the “training still needed” estimate.</p>

      <Field label="Training log CSV" hint="(Keras, Lightning, HF Trainer, or a W&B export)" htmlFor="log-file">
        <input id="log-file" type="file" accept=".csv" onChange={uploadCsv} />
      </Field>

      <details className="mt-3">
        <summary className="cursor-pointer text-[0.95rem] font-semibold">or import from MLflow</summary>
        <form onSubmit={importMlflow} className="mt-2.5 max-w-xl">
          <Field label="Tracking server URL" htmlFor="ml-uri">
            <input id="ml-uri" name="uri" type="text" placeholder="https://mlflow.example.com" required className={inputClass} />
          </Field>
          <Field label="Run ID" htmlFor="ml-run">
            <input id="ml-run" name="run" type="text" required className={inputClass} />
          </Field>
          <Field label="Token" hint="(optional)" htmlFor="ml-token">
            <div className="flex gap-2.5">
              <input id="ml-token" name="token" type="password" autoComplete="off" className={inputClass} />
              <Button type="submit" disabled={busy}>
                Import
              </Button>
            </div>
          </Field>
        </form>
      </details>

      <Status message={status} />
      {log && (
        <p className="mt-3.5 text-sm">
          <span className="text-muted">Using {log.filename}. </span>
          <Link href="/report" className={`${buttonClass.outline} !px-3 !py-1.5 text-sm`}>
            See the report
          </Link>
        </p>
      )}
    </div>
  );
}
