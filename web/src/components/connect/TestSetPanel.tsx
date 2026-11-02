"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiJson } from "@/lib/api";
import { errorMessage, pct } from "@/lib/format";
import type { Endpoint, Evaluation, Leakage, TestSet } from "@/lib/types";
import { useAuth } from "../AuthProvider";
import { useWorkspace } from "../WorkspaceProvider";
import { Button, Field, inputClass, Status, type StatusMessage } from "../ui";

const JSON_HEADERS = { "Content-Type": "application/json" };

/** Upload a test-set CSV, choose the target column, check for leakage, and score an endpoint on it. */
export default function TestSetPanel({ endpoints }: { endpoints: Endpoint[] }) {
  const router = useRouter();
  const { user } = useAuth();
  const { testset, setTestset, setEvaluation } = useWorkspace();
  const [status, setStatus] = useState<StatusMessage>(null);
  const [leak, setLeak] = useState<StatusMessage>(null);
  const [endpointId, setEndpointId] = useState("");
  const [running, setRunning] = useState(false);

  const chosenEndpoint = endpoints.some((e) => e.id === endpointId) ? endpointId : (endpoints[0]?.id ?? "");

  async function upload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    setLeak(null);
    if (!file) return;
    if (!user) return setStatus({ text: "Sign in first (top right) to upload a test set.", kind: "error" });
    const form = new FormData();
    form.append("file", file);
    setStatus({ text: "Uploading…" });
    try {
      const ts = await apiJson<TestSet>("/testsets", { method: "POST", body: form });
      setTestset(ts);
      setStatus({ text: `Loaded ${ts.rows} rows, ${ts.columns.length} columns. Pick the target column.`, kind: "success" });
    } catch (err) {
      setTestset(null);
      setStatus({ text: errorMessage(err), kind: "error" });
    }
  }

  async function chooseTarget(column: string) {
    if (!testset) return;
    setLeak(null);
    try {
      const ts = await apiJson<TestSet>(`/testsets/${testset.id}/target`, {
        method: "PUT",
        headers: JSON_HEADERS,
        body: JSON.stringify({ column }),
      });
      setTestset(ts); // also discards any evaluation of the previous target
      const classes = Object.entries(ts.classes ?? {})
        .map(([k, n]) => `${k}: ${n}`)
        .join(", ");
      setStatus({ text: `Target "${ts.target}" — ${classes}`, kind: "success" });
    } catch (err) {
      setStatus({ text: errorMessage(err), kind: "error" });
    }
  }

  async function checkLeakage(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    setLeak(null);
    if (!file || !testset) return;
    const form = new FormData();
    form.append("train_file", file);
    try {
      const r = await apiJson<Leakage>(`/testsets/${testset.id}/leakage`, { method: "POST", body: form });
      setLeak(
        r.warning
          ? { text: `⚠ ${r.warning}`, kind: "error" }
          : { text: `No overlap between ${r.train_rows} training rows and the test set.`, kind: "success" },
      );
    } catch (err) {
      setLeak({ text: errorMessage(err), kind: "error" });
    }
  }

  async function evaluate() {
    if (!testset || !chosenEndpoint) return;
    setRunning(true);
    setStatus({ text: "Running the test set through the endpoint… this can take a minute." });
    try {
      const report = await apiJson<Evaluation>(`/testsets/${testset.id}/evaluate`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ endpoint_id: chosenEndpoint }),
      });
      setEvaluation(report);
      setStatus({ text: `Done — accuracy ${pct(report.accuracy)}. Opening the report…`, kind: "success" });
      router.push("/report");
    } catch (err) {
      setStatus({ text: errorMessage(err), kind: "error" });
    } finally {
      setRunning(false);
    }
  }

  return (
    <div>
      <h2 className="mb-1 text-xl font-bold">2. Test set</h2>
      <p className="mb-4 text-sm text-muted">A CSV with a header row; one column is what the model should predict.</p>

      <Field label="Test set CSV with a header row" htmlFor="ts-file">
        <input id="ts-file" type="file" accept=".csv" onChange={upload} />
      </Field>

      {testset && (
        <Field label="Target column (what the model predicts)" htmlFor="ts-target">
          <select
            id="ts-target"
            value={testset.target ?? ""}
            onChange={(e) => chooseTarget(e.target.value)}
            className={inputClass}
          >
            <option value="" disabled>
              Choose a column…
            </option>
            {testset.columns.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} ({c.unique} distinct)
              </option>
            ))}
          </select>
        </Field>
      )}

      {testset?.target && (
        <>
          <Field label="Training CSV" hint="(optional, to check for train/test leakage)" htmlFor="ts-train">
            <input id="ts-train" type="file" accept=".csv" onChange={checkLeakage} />
          </Field>

          {endpoints.length > 0 ? (
            <Field label="Run the test set through" htmlFor="ts-endpoint">
              <div className="flex gap-2.5">
                <select id="ts-endpoint" value={chosenEndpoint} onChange={(e) => setEndpointId(e.target.value)} className={inputClass}>
                  {endpoints.map((ep) => (
                    <option key={ep.id} value={ep.id}>
                      {ep.name} ({ep.model})
                    </option>
                  ))}
                </select>
                <Button onClick={evaluate} disabled={running}>
                  Evaluate
                </Button>
              </div>
            </Field>
          ) : (
            <p className="mt-3.5 text-sm text-muted">Attach an endpoint on the left to evaluate this test set.</p>
          )}
        </>
      )}

      <Status message={status} />
      <Status message={leak} />
    </div>
  );
}
