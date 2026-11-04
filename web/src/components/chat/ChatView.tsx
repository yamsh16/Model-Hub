"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import { readSse } from "@/lib/sse";
import { CHAT_KEY_PREFIX } from "@/lib/storage";
import type { ChatMessage } from "@/lib/types";
import { useEndpoints } from "@/lib/useEndpoints";
import { useWeights } from "@/lib/useWeights";
import { useAuth } from "../AuthProvider";
import { Button, buttonClass, Card, Container, inputClass, Status, type StatusMessage } from "../ui";

// The server keeps no chat state: each turn sends the whole conversation, and the history is
// kept per endpoint in localStorage so it survives a reload (wiped when the account changes).
function loadHistory(endpointId: string): ChatMessage[] {
  try {
    const saved = JSON.parse(localStorage.getItem(CHAT_KEY_PREFIX + endpointId) ?? "null");
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}

function saveHistory(endpointId: string, history: ChatMessage[]) {
  try {
    localStorage.setItem(CHAT_KEY_PREFIX + endpointId, JSON.stringify(history));
  } catch {
    // storage full or blocked: chat still works, it just won't survive a reload
  }
}

type ChatTarget = { value: string; id: string; kind: "endpoint" | "local"; label: string };

export default function ChatView() {
  const { user, ready } = useAuth();
  const { endpoints } = useEndpoints();
  const { weights } = useWeights();
  const [chosen, setChosen] = useState("");
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState<string | null>(null); // the reply while it streams in
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<StatusMessage>(null);
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  // Remote endpoints, plus uploaded GGUF models that are currently running in their sandbox.
  const options: ChatTarget[] = [
    ...endpoints.map((ep) => ({ value: `endpoint:${ep.id}`, id: ep.id, kind: "endpoint" as const, label: `${ep.name} (${ep.model})` })),
    ...weights
      .filter((w) => w.serve_state === "running")
      .map((w) => ({ value: `local:${w.id}`, id: w.id, kind: "local" as const, label: `${w.name} (uploaded, ${w.architecture ?? w.format})` })),
  ];
  const target = options.find((o) => o.value === chosen) ?? options[0];
  const endpointId = target?.id ?? "";
  const idleLocal = weights.filter((w) => w.format === "gguf" && w.serve_state === "stopped").length;

  // Switching model (or loading the list) shows that model's saved conversation.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- load the saved history for the selected model
    setHistory(endpointId ? loadHistory(endpointId) : []);
    setStatus(null);
  }, [endpointId]);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [history, draft]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy || !endpointId) return;

    const before = history;
    const asked = [...before, { role: "user" as const, content: text }];
    let reply = "";
    setInput("");
    setHistory(asked);
    setDraft("…");
    setStatus(null);
    setBusy(true);

    try {
      const res = await apiFetch(`${target.kind === "local" ? "/weights" : "/endpoints"}/${endpointId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: asked, stream: true }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "The model did not answer.");
      }
      await readSse(res, (data) => {
        if (data === "[DONE]") return;
        const evt = JSON.parse(data);
        if (evt.error) throw new Error(evt.error);
        reply += evt.delta;
        setDraft(reply);
      });
      if (!reply) throw new Error("The model sent an empty reply.");
    } catch (err) {
      setStatus({ text: errorMessage(err), kind: "error" });
    }

    // Keep whatever streamed in; drop the unanswered question only if nothing came back.
    const after = reply ? [...asked, { role: "assistant" as const, content: reply }] : before;
    setHistory(after);
    saveHistory(endpointId, after);
    setDraft(null);
    setBusy(false);
  }

  function clear() {
    setHistory([]);
    saveHistory(endpointId, []);
    setStatus(null);
  }

  const none = options.length === 0;
  const bubble = "max-w-[80%] whitespace-pre-wrap rounded-[10px] px-4 py-2.5";

  return (
    <section className="bg-grey-bg py-12">
      <Container className="grid items-center gap-12 lg:grid-cols-2">
        <div>
          <h1 className="text-3xl font-bold sm:text-4xl">Once attached, just talk to it</h1>
          <p className="mt-4 max-w-lg text-lg text-muted">
            Test the model in a live chat without any extra setup. Ask questions, send inputs, and compare answers
            against the report to see where it is strong and where it needs work.
          </p>
          {ready && none && (
            <p className="mt-6">
              <Link href="/connect" className={buttonClass.teal}>
                {user ? "Attach a model first" : "Sign in and attach a model"}
              </Link>
            </p>
          )}
        </div>

        <Card>
          <div className="mb-3 flex gap-2.5">
            <select
              aria-label="Model to chat with"
              value={target?.value ?? ""}
              onChange={(e) => setChosen(e.target.value)}
              disabled={none || busy}
              className={inputClass}
            >
              {options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <Button variant="outline" onClick={clear} disabled={none || busy || history.length === 0}>
              Clear
            </Button>
          </div>

          <div className="mb-5 flex max-h-80 min-h-24 flex-col gap-3.5 overflow-y-auto" aria-live="polite" aria-label="Conversation">
            {history.map((m, i) => (
              <div key={i} className={`${bubble} ${m.role === "user" ? "self-end bg-ink text-white" : "self-start bg-teal-soft text-ink"}`}>
                {m.content}
              </div>
            ))}
            {draft !== null && <div className={`${bubble} self-start bg-teal-soft text-ink`}>{draft}</div>}
            <div ref={bottom} />
          </div>

          <Status message={status} />
          {idleLocal > 0 && (
            <p className="mt-3 text-[0.85rem] text-muted">
              {idleLocal} uploaded model{idleLocal === 1 ? " is" : "s are"} not running.{" "}
              <Link href="/connect" className="font-semibold text-teal hover:underline">
                Start {idleLocal === 1 ? "it" : "one"} on the Connect page
              </Link>{" "}
              to chat with {idleLocal === 1 ? "it" : "them"} here.
            </p>
          )}
          <form onSubmit={send} className="mt-3.5 flex gap-2.5">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={none ? "Attach a model first" : "Message your model"}
              autoComplete="off"
              disabled={none}
              aria-label="Message"
              className={inputClass}
            />
            <Button type="submit" variant="teal" disabled={none || busy} aria-label="Send" className="!px-3.5">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true">
                <path d="M5 12h14M13 5l7 7-7 7" />
              </svg>
            </Button>
          </form>
        </Card>
      </Container>
    </section>
  );
}
