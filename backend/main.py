from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chat import router as chat_router
from endpoints import router as endpoints_router
from estimate import router as estimate_router
from evaluation import router as evaluation_router
from logs import router as logs_router
from metrics import router as metrics_router
from testsets import router as testsets_router

app = FastAPI(title="Model Hub API", version="0.1.0")
app.include_router(endpoints_router)
app.include_router(chat_router)
app.include_router(metrics_router)
app.include_router(testsets_router)
app.include_router(evaluation_router)
app.include_router(logs_router)
app.include_router(estimate_router)

# Allow the static frontend (python -m http.server 8000) to call the API in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "model-hub-api", "version": app.version}
