import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, apiJson, getToken, setToken } from "./api";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("token storage", () => {
  it("round-trips and clears", () => {
    expect(getToken()).toBeNull();
    setToken("abc");
    expect(getToken()).toBe("abc");
    setToken(null);
    expect(getToken()).toBeNull();
  });

  it("does not throw when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(getToken()).toBeNull();
    vi.restoreAllMocks();
  });
});

describe("apiFetch", () => {
  it("sends the bearer token to the backend URL", async () => {
    setToken("tok");
    const fetchMock = mockFetch(200, {});
    await apiFetch("/endpoints");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://localhost:8001/endpoints");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
  });

  it("sends no Authorization header when signed out", async () => {
    const fetchMock = mockFetch(200, {});
    await apiFetch("/health");
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
  });

  it("drops a token the server rejects, but not on the auth routes", async () => {
    setToken("stale");
    mockFetch(401, { detail: "Sign in required" });
    await apiFetch("/auth/login", { method: "POST" });
    expect(getToken()).toBe("stale"); // a failed login must not sign you out
    await apiFetch("/endpoints");
    expect(getToken()).toBeNull();
  });
});

describe("apiJson", () => {
  it("returns parsed JSON", async () => {
    mockFetch(200, { status: "ok" });
    await expect(apiJson("/health")).resolves.toEqual({ status: "ok" });
  });

  it("throws an ApiError with the server's message and status", async () => {
    mockFetch(422, { detail: "Enter a valid email address" });
    const err = (await apiJson("/auth/register").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("Enter a valid email address");
    expect(err.status).toBe(422);
  });

  it("falls back to a generic message when the error body is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>boom</html>", { status: 500 })));
    await expect(apiJson("/x")).rejects.toThrow("Request failed.");
  });
});
