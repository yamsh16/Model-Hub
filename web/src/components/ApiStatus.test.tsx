import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ApiStatus from "./ApiStatus";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ApiStatus", () => {
  it("shows connected when /health answers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ status: "ok", service: "model-hub-api", version: "0.1.0" }))),
    );
    render(<ApiStatus />);
    expect(screen.getByRole("status").dataset.state).toBe("checking");
    await waitFor(() => expect(screen.getByRole("status").dataset.state).toBe("up"));
    expect(screen.getByRole("status").textContent).toContain("model-hub-api 0.1.0");
  });

  it("explains how to start the backend when it is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("network"))));
    render(<ApiStatus />);
    await waitFor(() => expect(screen.getByRole("status").dataset.state).toBe("down"));
    expect(screen.getByRole("status").textContent).toContain("uvicorn main:app --port 8001");
  });
});
