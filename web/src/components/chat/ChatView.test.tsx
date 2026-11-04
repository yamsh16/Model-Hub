import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setToken } from "@/lib/api";
import { CHAT_KEY_PREFIX } from "@/lib/storage";
import AuthProvider from "../AuthProvider";
import ChatView from "./ChatView";

const endpoint = { id: "e1", name: "my-llm", url: "https://x/v1/chat/completions", model: "gpt-x", latency_ms: 40 };

let localModels: unknown[] = [];

function sse(...events: string[]): Response {
  const enc = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(c) {
        for (const e of events) c.enqueue(enc.encode(`data: ${e}\n\n`));
        c.close();
      },
    }),
  );
}

/** Routes the page's calls: sign-in check, endpoint list, and the chat itself. */
function mockBackend(chat: () => Response) {
  const calls: { path: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.replace("http://localhost:8001", "");
      calls.push({ path, body: init?.body as string | undefined });
      if (path === "/auth/me") return Response.json({ id: "u1", email: "a@b.co" });
      if (path === "/endpoints") return Response.json([endpoint]);
      if (path === "/endpoints/e1/chat") return chat();
      if (path === "/weights") return Response.json(localModels);
      if (path === "/weights/w1/chat") return chat();
      return new Response("{}", { status: 404 });
    }),
  );
  return calls;
}

function renderChat() {
  return render(
    <AuthProvider>
      <ChatView />
    </AuthProvider>,
  );
}

async function ask(text: string) {
  const box = await screen.findByLabelText("Message");
  await waitFor(() => expect((box as HTMLInputElement).disabled).toBe(false));
  fireEvent.change(box, { target: { value: text } });
  fireEvent.submit(box.closest("form")!);
}

beforeEach(() => {
  localModels = [];
  localStorage.clear();
  setToken("tok");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ChatView", () => {
  it("streams the reply, sends the whole conversation, and saves the history", async () => {
    const calls = mockBackend(() => sse('{"delta":"Hel"}', '{"delta":"lo"}', "[DONE]"));
    renderChat();
    await ask("Hi");

    expect(await screen.findByText("Hello")).toBeTruthy();
    const chat = calls.find((c) => c.path === "/endpoints/e1/chat")!;
    expect(JSON.parse(chat.body!)).toEqual({ messages: [{ role: "user", content: "Hi" }], stream: true });
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem(CHAT_KEY_PREFIX + "e1")!)).toEqual([
        { role: "user", content: "Hi" },
        { role: "assistant", content: "Hello" },
      ]),
    );
  });

  it("drops the unanswered question and shows the error when the model fails", async () => {
    mockBackend(() => Response.json({ detail: "The model did not answer." }, { status: 502 }));
    renderChat();
    await ask("Hi");

    expect(await screen.findByText("The model did not answer.")).toBeTruthy();
    expect(screen.queryByText("Hi")).toBeNull();
    expect(JSON.parse(localStorage.getItem(CHAT_KEY_PREFIX + "e1")!)).toEqual([]);
  });

  it("restores the saved conversation for the selected model", async () => {
    localStorage.setItem(CHAT_KEY_PREFIX + "e1", JSON.stringify([{ role: "user", content: "earlier question" }]));
    mockBackend(() => sse("[DONE]"));
    renderChat();
    expect(await screen.findByText("earlier question")).toBeTruthy();
  });
});

const local = (state: string) => ({
  id: "w1", name: "tiny-llama", format: "gguf", architecture: "llama", serve_state: state,
});

describe("ChatView with uploaded models", () => {
  it("offers a running uploaded model and chats through its own route", async () => {
    localModels = [local("running")];
    const calls = mockBackend(() => sse('{"delta":"local hi"}', "[DONE]"));
    renderChat();
    const select = (await screen.findByLabelText("Model to chat with")) as HTMLSelectElement;
    await waitFor(() => expect([...select.options].map((o) => o.textContent)).toContain("tiny-llama (uploaded, llama)"));

    fireEvent.change(select, { target: { value: "local:w1" } });
    await ask("Hi there");
    expect(await screen.findByText("local hi")).toBeTruthy();
    expect(calls.some((c) => c.path === "/weights/w1/chat")).toBe(true);
    expect(calls.some((c) => c.path === "/endpoints/e1/chat")).toBe(false);
    expect(localStorage.getItem(CHAT_KEY_PREFIX + "w1")).toContain("local hi"); // its own history
  });

  it("does not list a stopped uploaded model, and points to where to start it", async () => {
    localModels = [local("stopped")];
    mockBackend(() => sse("[DONE]"));
    renderChat();
    const select = (await screen.findByLabelText("Model to chat with")) as HTMLSelectElement;
    expect(await screen.findByText(/1 uploaded model is not running/)).toBeTruthy();
    expect([...select.options].map((o) => o.textContent)).not.toContain("tiny-llama (uploaded, llama)");
    expect(screen.getByRole("link", { name: /Start it on the Connect page/ }).getAttribute("href")).toBe("/connect");
  });
});
