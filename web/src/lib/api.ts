// The one place that knows where the backend is and how to authenticate to it.
// Same rules as the static site: a bearer token kept in localStorage, sent on every call,
// dropped when the server stops accepting it.

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8001";

const TOKEN_KEY = "modelhub-token";

/** Fired on window when the server rejects the saved token. */
export const SIGNED_OUT_EVENT = "modelhub:signed-out";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // storage blocked, or running on the server
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // sign-in just won't survive a reload
  }
}

/** fetch() against the backend, with the token attached. `path` starts with "/". */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (res.status === 401 && token && !path.startsWith("/auth/")) {
    setToken(null);
    window.dispatchEvent(new Event(SIGNED_OUT_EVENT)); // lets the sign-in state in React catch up
  }
  return res;
}

/** Like apiFetch but returns parsed JSON, and throws an Error carrying the server's message. */
export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await apiFetch(path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(typeof body.detail === "string" ? body.detail : "Request failed.", res.status);
  }
  return body as T;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export type Health = { status: string; service: string; version: string };
