"""A tiny OpenAI-compatible model for the end-to-end tests. Standard library only.

It requires the API key `sk-e2e-key` (so a test can prove the stored key still decrypts), labels
a record "pos" when it mentions "great" and "neg" otherwise, and is deliberately wrong for any
record containing "tricky", so the expected accuracy is known exactly. Any other message is
echoed back, whole or as a stream of word-sized chunks.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "sk-e2e-key"


def answer(prompt: str) -> str:
    if prompt.startswith("Classify the record below"):
        record = prompt.split("Record:", 1)[1].lower()
        label = "pos" if "great" in record else "neg"
        if "tricky" in record:
            label = "neg" if label == "pos" else "pos"
        return label
    return f"echo: {prompt}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the container log quiet
        pass

    def _send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"status": "ok"})

    def do_POST(self):
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            return self._send(401, {"error": "bad key"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        reply = answer(body["messages"][-1]["content"])
        if not body.get("stream"):
            return self._send(200, {"choices": [{"message": {"role": "assistant", "content": reply}}]})
        events = [{"choices": [{"delta": {"content": word + " "}}]} for word in reply.split()]
        sse = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        self._send(200, sse.encode(), "text/event-stream")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
