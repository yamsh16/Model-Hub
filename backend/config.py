import logging
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

log = logging.getLogger("modelhub.config")

# Development fallback location (git-ignored). Production sets the environment variables.
DEV_SECRETS_DIR = Path(__file__).parent / ".dev-secrets"


def get_secret(name: str, generate) -> str:
    """Return the secret from env var `name`; otherwise a persisted dev-only value.

    The dev value is created on first use and stored under .dev-secrets/ so sign-ins and
    stored API keys survive a restart. Never rely on that in production: set the variable.
    """
    value = os.environ.get(name)
    if value:
        return value
    path = DEV_SECRETS_DIR / name
    if path.exists():
        return path.read_text().strip()
    DEV_SECRETS_DIR.mkdir(exist_ok=True)
    value = generate()
    path.write_text(value)
    try:
        path.chmod(0o600)
    except OSError:
        pass  # not supported on every filesystem
    log.warning("%s is not set; created a development key at %s", name, path)
    return value


def jwt_secret() -> str:
    return get_secret("JWT_SECRET", lambda: secrets.token_urlsafe(48))


def encryption_key() -> str:
    return get_secret("ENCRYPTION_KEY", lambda: Fernet.generate_key().decode())


GIB = 1024**3


def upload_dir() -> Path:
    """Where uploaded weight files are stored (git-ignored by default). Set UPLOAD_DIR in production."""
    return Path(os.environ.get("UPLOAD_DIR") or Path(__file__).parent / ".uploads")


def max_upload_bytes() -> int:
    return int(float(os.environ.get("MAX_UPLOAD_GB", "2")) * GIB)


def max_user_upload_bytes() -> int:
    return int(float(os.environ.get("MAX_USER_UPLOAD_GB", "10")) * GIB)


# ---- sandboxed serving of uploaded models (see sandbox.py) ----

# Pinned by digest so the code that runs untrusted models is the code that was reviewed. Update it
# deliberately: `docker pull <image>` then copy the new digest. Override with LLAMA_IMAGE.
DEFAULT_LLAMA_IMAGE = "ghcr.io/ggml-org/llama.cpp@sha256:10a38e6cc91760ab157f0a0a8546218d35f4a314432a70298abfa3d096829ad7"


def serve_image() -> str:
    return os.environ.get("LLAMA_IMAGE") or DEFAULT_LLAMA_IMAGE


def docker_bin() -> str:
    return os.environ.get("DOCKER_BIN") or "docker"


def serve_memory() -> str:
    return os.environ.get("SERVE_MEMORY") or "2g"


def serve_cpus() -> float:
    return float(os.environ.get("SERVE_CPUS") or 2)


def serve_context() -> int:
    return int(os.environ.get("SERVE_CONTEXT") or 2048)


def serve_max_tokens() -> int:
    return int(os.environ.get("SERVE_MAX_TOKENS") or 512)


def serve_idle_seconds() -> int:
    return int(os.environ.get("SERVE_IDLE_SECONDS") or 600)


def serve_max_running() -> int:
    return int(os.environ.get("SERVE_MAX_RUNNING") or 2)


def serve_max_per_user() -> int:
    return int(os.environ.get("SERVE_MAX_PER_USER") or 1)


def serve_start_timeout() -> float:
    return float(os.environ.get("SERVE_START_TIMEOUT") or 120)


def serve_request_timeout() -> float:
    return float(os.environ.get("SERVE_REQUEST_TIMEOUT") or 120)
