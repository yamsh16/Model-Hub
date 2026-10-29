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
