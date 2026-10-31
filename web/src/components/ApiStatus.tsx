"use client";

import { useEffect, useState } from "react";
import { API_BASE, apiJson, type Health } from "@/lib/api";

type State = { kind: "checking" } | { kind: "up"; health: Health } | { kind: "down" };

/** Shows whether the browser can reach the backend: proves the URL setting and CORS are right. */
export default function ApiStatus() {
  const [state, setState] = useState<State>({ kind: "checking" });

  useEffect(() => {
    let cancelled = false;
    apiJson<Health>("/health")
      .then((health) => !cancelled && setState({ kind: "up", health }))
      .catch(() => !cancelled && setState({ kind: "down" }));
    return () => {
      cancelled = true;
    };
  }, []);

  const label =
    state.kind === "checking"
      ? "Checking the backend…"
      : state.kind === "up"
        ? `Backend connected · ${state.health.service} ${state.health.version}`
        : `Backend not reachable at ${API_BASE}. Start it with: uvicorn main:app --port 8001`;

  return (
    <p
      role="status"
      data-state={state.kind}
      className={`inline-flex items-center gap-2 rounded-md px-3 py-2 font-mono text-sm ${
        state.kind === "up" ? "bg-teal-soft text-teal" : state.kind === "down" ? "bg-red-50 text-red-700" : "bg-card text-muted"
      }`}
    >
      <span aria-hidden="true" className="h-2 w-2 rounded-full bg-current" />
      {label}
    </p>
  );
}
