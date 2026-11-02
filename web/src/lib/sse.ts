/** Read a server-sent-events response body, calling onEvent(payload) for each `data:` line. */
export async function readSse(res: Response, onEvent: (data: string) => void): Promise<void> {
  if (!res.body) throw new Error("The reply had no body.");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // Events end with a blank line; a chunk can hold several, or stop in the middle of one.
    let end: number;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data:")) onEvent(line.slice(5).trim());
      }
    }
  }
}
