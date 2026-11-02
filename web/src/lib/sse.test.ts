import { describe, expect, it } from "vitest";
import { readSse } from "./sse";

function streamOf(...chunks: string[]): Response {
  const enc = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const c of chunks) controller.enqueue(enc.encode(c));
        controller.close();
      },
    }),
  );
}

async function collect(res: Response): Promise<string[]> {
  const out: string[] = [];
  await readSse(res, (d) => out.push(d));
  return out;
}

describe("readSse", () => {
  it("reads several events from one chunk", async () => {
    expect(await collect(streamOf('data: {"delta":"a"}\n\ndata: [DONE]\n\n'))).toEqual(['{"delta":"a"}', "[DONE]"]);
  });

  it("reassembles an event split across chunks", async () => {
    expect(await collect(streamOf('data: {"del', 'ta":"hi"}\n', "\n"))).toEqual(['{"delta":"hi"}']);
  });

  it("ignores lines that are not data", async () => {
    expect(await collect(streamOf(": keep-alive\n\ndata: x\n\n"))).toEqual(["x"]);
  });
});
