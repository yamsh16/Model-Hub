"""Encrypt/decrypt stored third-party API keys (Fernet: AES-128-CBC + HMAC)."""
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

import config


def _fernet() -> Fernet:
    return Fernet(config.encryption_key().encode())


def encrypt(plaintext: str | None) -> str | None:
    return _fernet().encrypt(plaintext.encode()).decode() if plaintext else None


def decrypt(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise HTTPException(
            409, "The stored API key can no longer be decrypted (the server key changed); "
                 "remove this model and attach it again"
        )
