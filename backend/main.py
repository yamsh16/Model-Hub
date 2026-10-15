from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from endpoints import router as endpoints_router
from testsets import router as testsets_router

app = FastAPI(title="Model Hub API", version="0.1.0")
app.include_router(endpoints_router)
app.include_router(testsets_router)

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
