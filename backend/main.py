import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import serving
from auth import router as auth_router
from chat import router as chat_router
from endpoints import router as endpoints_router
from estimate import router as estimate_router
from evaluation import router as evaluation_router
from logs import router as logs_router
from metrics import router as metrics_router
from readiness import router as readiness_router
from testsets import router as testsets_router
from weights import cleanup_stale_temp, router as weights_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    cleanup_stale_temp()  # drop half-written uploads left by a crash
    serving.startup_cleanup()  # remove model containers left by a previous run
    reaper = asyncio.create_task(serving.reaper_loop())  # stops models nobody is using
    try:
        yield
    finally:
        reaper.cancel()
        serving.shutdown_all()


app = FastAPI(title="Model Hub API", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(endpoints_router)
app.include_router(chat_router)
app.include_router(metrics_router)
app.include_router(testsets_router)
app.include_router(evaluation_router)
app.include_router(logs_router)
app.include_router(estimate_router)
app.include_router(readiness_router)
app.include_router(weights_router)
app.include_router(serving.router)

# Allow the Next.js app (web/, :3000) to call the API in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "model-hub-api", "version": app.version}
