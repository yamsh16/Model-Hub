"""Serve model.joblib as an OpenAI-compatible chat endpoint (what Model Hub attaches to).

It reads "feature: value" lines from the last user message, predicts, and replies with only
the class name. Run:  uvicorn serve:app --port 9000
"""
import json
import re
import time

import joblib
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

bundle = joblib.load("model.joblib")
model, FEATURES = bundle["model"], bundle["features"]
app = FastAPI()


class Message(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    model: str = "iris-logreg"
    messages: list[Message]
    stream: bool = False


def answer(text: str) -> str:
    values = {}
    for f in FEATURES:
        m = re.search(rf"\b{f}\s*[:=]\s*(-?\d+(?:\.\d+)?)", text, re.I)
        if m:
            values[f] = float(m.group(1))
    if len(values) < len(FEATURES):
        return f"I classify iris flowers. Send lines like: {', '.join(f + ': 1.0' for f in FEATURES)}"
    return str(model.predict([[values[f] for f in FEATURES]])[0])


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": "iris-logreg", "object": "model"}]}


@app.post("/v1/chat/completions")
def chat(body: ChatIn):
    user = [m.content for m in body.messages if m.role == "user"]
    reply = answer(user[-1] if user else "")
    base = {"id": "chatcmpl-1", "created": int(time.time()), "model": body.model}
    if body.stream:
        def events():
            chunk = {**base, "object": "chat.completion.chunk",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": reply}}]}
            yield f"data: {json.dumps(chunk)}\n\n"
            done = {**base, "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(done)}\n\ndata: [DONE]\n\n"
        return StreamingResponse(events(), media_type="text/event-stream")
    return {**base, "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": reply}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 1, "total_tokens": 1}}
