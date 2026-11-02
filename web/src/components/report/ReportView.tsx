"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { apiJson } from "@/lib/api";
import { pct } from "@/lib/format";
import { estimateCard, progressCard } from "@/lib/report";
import type { Readiness } from "@/lib/types";
import { useWorkspace } from "../WorkspaceProvider";
import { buttonClass, Container, DataTable, Panel } from "../ui";
import LossChart from "./LossChart";

const SAMPLE_ADVICE = [
  "Attach a model and run a test set to get real advice here.",
  "Load a training log to see how much more training it needs.",
  "Re-run the report after each training round to track progress.",
];

function Stat({ label, value, note, dark, children }: { label: string; value: string; note: string; dark?: boolean; children?: React.ReactNode }) {
  return (
    <div className={`rounded-md p-5 ${dark ? "bg-ink text-white" : "bg-card"}`}>
      <p className={`text-[0.95rem] ${dark ? "text-[#d6dbe2]" : "text-muted"}`}>{label}</p>
      <p className="my-2 whitespace-nowrap text-4xl font-semibold">{value}</p>
      {children}
      <p className={`text-sm ${dark ? "text-[#d6dbe2]" : "text-muted"}`}>{note}</p>
    </div>
  );
}

/** The report: every number comes from what the connect page loaded; nothing is computed here. */
export default function ReportView() {
  const { testset, evaluation, log } = useWorkspace();
  const [readiness, setReadiness] = useState<Readiness | null>(null);

  const testsetId = testset?.id;
  const logId = log?.id;
  // Re-ask the server whenever the inputs change (a new target or evaluation changes the score).
  useEffect(() => {
    if (!testsetId && !logId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- nothing loaded: clear the report
      setReadiness(null);
      return;
    }
    const params = new URLSearchParams();
    if (testsetId) params.set("testset_id", testsetId);
    if (logId) params.set("log_id", logId);
    let cancelled = false;
    apiJson<Readiness>(`/readiness?${params}`)
      .then((r) => !cancelled && setReadiness(r))
      .catch(() => {
        // backend down or it forgot the ids: keep what is on screen
      });
    return () => {
      cancelled = true;
    };
  }, [testsetId, logId, testset?.target, evaluation]);

  const hasData = !!readiness && (readiness.score !== null || readiness.estimate !== null);
  const progress = progressCard(readiness?.estimate ?? null);
  const needed = estimateCard(readiness?.estimate ?? null);
  const advice = readiness?.advice.length ? readiness.advice.map((a) => a.text) : SAMPLE_ADVICE;

  return (
    <section className="py-12">
      <Container>
        <p className="mb-2.5 font-mono text-sm tracking-[0.12em] text-muted">{hasData ? "YOUR REPORT" : "SAMPLE REPORT"}</p>
        <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-3xl font-bold sm:text-4xl">Everything about your model, in one view</h1>
          <Link href="/connect" className={buttonClass.outline}>
            Load data
          </Link>
        </div>

        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Training progress" value={progress.value} note={progress.note}>
            <div className="mb-3 h-2 overflow-hidden rounded-full bg-line" role="progressbar" aria-label="Training progress" aria-valuenow={Math.round((progress.fraction ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
              <div className="h-full rounded-full bg-teal" style={{ width: `${Math.round((progress.fraction ?? 0) * 100)}%` }} />
            </div>
          </Stat>
          <Stat
            label="Accuracy"
            value={evaluation ? pct(evaluation.accuracy) : "–"}
            note={
              evaluation
                ? `Macro F1 ${evaluation.f1_macro.toFixed(3)} · ROC-AUC ${evaluation.roc_auc === null ? "n/a (endpoint returns labels only)" : evaluation.roc_auc.toFixed(3)}`
                : "Run a test set to measure this"
            }
          />
          <Stat label="Training still needed" value={needed.value} note={needed.note} />
          <Stat
            dark
            label="Readiness score"
            value={readiness?.score == null ? "– / 100" : `${readiness.score} / 100`}
            note={readiness?.verdict ?? "Needs a test-set evaluation"}
          />
        </div>

        {readiness && readiness.components.length > 0 && (
          <details className="mt-6 rounded-md border border-line p-6">
            <summary className="cursor-pointer font-semibold">How the readiness score is built</summary>
            <p className="my-3 text-sm text-muted">
              Built from the signals available ({Math.round(readiness.coverage * 100)}% of the full weight). It is a
              rule-of-thumb summary, not a guarantee.
            </p>
            <DataTable
              label="Readiness score components"
              header={["Signal", "Weight", "Score", "Detail"]}
              rows={readiness.components.map((c) => [c.label, `${Math.round(c.weight * 100)}%`, `${Math.round(c.value * 100)}`, c.detail])}
            />
          </details>
        )}

        {evaluation && (
          <Panel title="Evaluation on your test set" className="mt-6">
            <p className="text-sm text-muted">
              Target “{evaluation.target}”, scored on{" "}
              {evaluation.sampled ? `a random ${evaluation.n} of ${evaluation.total_rows} rows` : `all ${evaluation.n} rows`}.
              {evaluation.unparsed > 0 && ` ${evaluation.unparsed} replies named no single class and were counted wrong.`}
            </p>
            <div className="mt-4 grid gap-6 md:grid-cols-2">
              <div>
                <h4 className="mb-2 text-[0.95rem] font-semibold">
                  Confusion matrix <span className="font-normal text-muted">(rows = true, columns = predicted)</span>
                </h4>
                <DataTable
                  label="Confusion matrix"
                  header={["", ...evaluation.classes]}
                  rows={evaluation.confusion_matrix.map((row, i) => [evaluation.classes[i], ...row])}
                />
              </div>
              <div>
                <h4 className="mb-2 text-[0.95rem] font-semibold">Per class</h4>
                <DataTable
                  label="Per-class scores"
                  header={["Class", "Precision", "Recall", "F1", "Rows"]}
                  rows={evaluation.per_class.map((c) => [c.label, c.precision.toFixed(3), c.recall.toFixed(3), c.f1.toFixed(3), c.support])}
                />
              </div>
            </div>
          </Panel>
        )}

        <div className="mt-6 grid gap-5 lg:grid-cols-2">
          <div className="flex flex-col gap-5">
            <LossChart log={log} />
            <Panel title="Training estimate">
              {readiness?.estimate ? (
              <details>
                <summary className="cursor-pointer font-semibold">Assumptions behind the training estimate</summary>
                <ul className="mt-2 list-disc pl-5 text-sm">
                  {[`Rule used: ${readiness.estimate.criterion}.`, ...readiness.estimate.warnings, ...readiness.estimate.assumptions].map((a, i) => (
                    <li key={i} className="mb-1.5">
                      {a}
                    </li>
                  ))}
                </ul>
              </details>
              ) : (
                <p className="text-sm text-muted">Load a training log on the connect page to see the estimate and its assumptions.</p>
              )}
            </Panel>
          </div>
          <Panel title="What to do next">
            <ul className="list-disc pl-5">
              {advice.map((text, i) => (
                <li key={i} className="mb-1.5">
                  {text}
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </Container>
    </section>
  );
}
