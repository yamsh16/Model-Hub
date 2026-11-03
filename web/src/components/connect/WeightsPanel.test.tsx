import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setToken } from "@/lib/api";
import type { Weights } from "@/lib/types";
import AuthProvider from "../AuthProvider";
import WeightsPanel from "./WeightsPanel";

const weights: Weights = {
  id: "w1",
  name: "tiny-llama",
  filename: "tiny.gguf",
  format: "gguf",
  size_bytes: 1536,
  sha256: "ab".repeat(32),
  tensor_count: 3,
  parameter_count: 1_230_000_000,
  dtypes: { F32: 2, Q8_0: 1 },
  architecture: "llama",
  metadata: {},
  warnings: ["Contains a chat template (a Jinja program). It must only ever be rendered in a sandbox."],
};

let list: Weights[];
let calls: { path: string; method: string }[];

beforeEach(() => {
  list = [weights];
  calls = [];
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.replace("http://localhost:8001", "");
      const method = init?.method ?? "GET";
      calls.push({ path, method });
      if (path === "/auth/me") return Response.json({ id: "u1", email: "a@b.co" });
      if (path === "/weights" && method === "GET") return Response.json(list);
      if (path === "/weights/w1" && method === "DELETE") {
        list = [];
        return new Response(null, { status: 204 });
      }
      return new Response("{}", { status: 404 });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderPanel() {
  return render(
    <AuthProvider>
      <WeightsPanel />
    </AuthProvider>,
  );
}

function choose(name: string) {
  const input = screen.getByLabelText(/Weights file/) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File([new Uint8Array(8)], name)] } });
}

describe("WeightsPanel", () => {
  it("lists uploaded models with what the scan found, including its warnings", async () => {
    setToken("tok");
    renderPanel();
    const item = await screen.findByText("tiny-llama");
    const row = item.closest("li")!;
    expect(row.textContent).toContain("gguf · 1.5 KB");
    expect(row.textContent).toContain("llama · 1.23B parameters · 3 tensors · F32 ×2, Q8_0 ×1");
    expect(row.textContent).toContain("sha256 " + "ab".repeat(32));
    expect(row.textContent).toContain("must only ever be rendered in a sandbox");
  });

  it("removes a model", async () => {
    setToken("tok");
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    await waitFor(() => expect(screen.queryByText("tiny-llama")).toBeNull());
    expect(calls).toContainEqual({ path: "/weights/w1", method: "DELETE" });
  });

  it("rejects a pickle file in the browser without sending anything", async () => {
    setToken("tok");
    renderPanel();
    await screen.findByText("tiny-llama");
    choose("model.pt");
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Only \.safetensors and \.gguf/);
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("asks you to choose a file, and to sign in first", async () => {
    renderPanel(); // no token: signed out
    fireEvent.click(await screen.findByRole("button", { name: "Upload" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Sign in first/);
    expect(screen.queryByText("tiny-llama")).toBeNull(); // a signed-out visitor sees no models
  });
});
