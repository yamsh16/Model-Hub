import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getToken, setToken, SIGNED_OUT_EVENT } from "./api";
import { checkWeightsFile, uploadWeights } from "./upload";

/** A controllable stand-in for XMLHttpRequest. */
class FakeXhr {
  static last: FakeXhr;
  method = "";
  url = "";
  headers: Record<string, string> = {};
  sent: unknown = null;
  status = 0;
  responseText = "";
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  aborted = false;
  constructor() {
    FakeXhr.last = this;
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  send(body: unknown) {
    this.sent = body;
  }
  abort() {
    this.aborted = true;
    this.onabort?.();
  }
  respond(status: number, body: unknown) {
    this.status = status;
    this.responseText = typeof body === "string" ? body : JSON.stringify(body);
    this.onload?.();
  }
}

const file = new File([new Uint8Array(10)], "my model.safetensors");

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
});
afterEach(() => vi.unstubAllGlobals());

describe("checkWeightsFile", () => {
  it("accepts the two data-only formats, in any letter case", () => {
    expect(checkWeightsFile({ name: "m.safetensors", size: 1 })).toBeNull();
    expect(checkWeightsFile({ name: "M.GGUF", size: 1 })).toBeNull();
  });

  it("rejects code-bearing and unknown formats with an explanation", () => {
    for (const name of ["m.pt", "m.pkl", "m.bin", "m.ckpt", "m.onnx", "m", "m.safetensors.zip"]) {
      expect(checkWeightsFile({ name, size: 1 })).toMatch(/Only \.safetensors and \.gguf/);
    }
  });

  it("rejects empty and oversized files", () => {
    expect(checkWeightsFile({ name: "m.gguf", size: 0 })).toMatch(/empty/);
    expect(checkWeightsFile({ name: "m.gguf", size: 11 }, 10)).toMatch(/larger/);
  });
});

describe("uploadWeights", () => {
  it("sends the raw file with the token, naming it in the query string", async () => {
    setToken("tok");
    const { promise } = uploadWeights(file, "my model", () => {});
    const x = FakeXhr.last;
    expect(x.method).toBe("POST");
    const url = new URL(x.url);
    expect(url.pathname).toBe("/weights");
    expect(url.searchParams.get("name")).toBe("my model");
    expect(url.searchParams.get("filename")).toBe("my model.safetensors");
    expect(x.headers["Authorization"]).toBe("Bearer tok");
    expect(x.headers["Content-Type"]).toBe("application/octet-stream");
    expect(x.sent).toBe(file); // the File itself, not a multipart wrapper
    x.respond(201, { id: "w1", name: "my model" });
    await expect(promise).resolves.toMatchObject({ id: "w1" });
  });

  it("reports progress as a fraction", () => {
    const seen: number[] = [];
    uploadWeights(file, "n", (f) => seen.push(f));
    const x = FakeXhr.last;
    x.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 });
    x.upload.onprogress?.({ lengthComputable: false, loaded: 9, total: 0 }); // ignored
    expect(seen).toEqual([0.5]);
  });

  it("turns a server rejection into an ApiError carrying the scan message", async () => {
    const { promise } = uploadWeights(file, "n", () => {});
    FakeXhr.last.respond(422, { detail: "Rejected: this file is a Python pickle" });
    const err = (await promise.catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.message).toMatch(/pickle/);
  });

  it("falls back to a generic message when the error is not JSON", async () => {
    const { promise } = uploadWeights(file, "n", () => {});
    FakeXhr.last.respond(502, "<html>bad gateway</html>");
    await expect(promise).rejects.toThrow("Upload failed.");
  });

  it("signs out when the server rejects the token", async () => {
    setToken("stale");
    const heard = vi.fn();
    window.addEventListener(SIGNED_OUT_EVENT, heard);
    const { promise } = uploadWeights(file, "n", () => {});
    FakeXhr.last.respond(401, { detail: "Sign in required" });
    await promise.catch(() => {});
    expect(getToken()).toBeNull();
    expect(heard).toHaveBeenCalled();
    window.removeEventListener(SIGNED_OUT_EVENT, heard);
  });

  it("reports a network failure as a TypeError (shown as 'could not reach the API')", async () => {
    const { promise } = uploadWeights(file, "n", () => {});
    FakeXhr.last.onerror?.();
    await expect(promise).rejects.toBeInstanceOf(TypeError);
  });

  it("can be cancelled", async () => {
    const { promise, abort } = uploadWeights(file, "n", () => {});
    abort();
    expect(FakeXhr.last.aborted).toBe(true);
    const err = (await promise.catch((e: unknown) => e)) as DOMException;
    expect(err.name).toBe("AbortError");
  });
});
