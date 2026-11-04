"""Where uploaded weight files live on disk."""
import uuid
from pathlib import Path

import config


def weights_path(user_id: uuid.UUID, model_id: uuid.UUID, fmt: str) -> Path:
    """A user's file. Built only from ids and a fixed extension, never from anything the user
    typed, so an upload can't choose its own path."""
    return config.upload_dir() / str(user_id) / f"{model_id}.{fmt}"


def temp_dir() -> Path:
    return config.upload_dir() / "tmp"
