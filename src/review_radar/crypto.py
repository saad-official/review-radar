"""Encryption at rest for per-app secrets (the app's GitHub token).

The same scheme as Disputely's `lib/crypto/secretbox.ts`, in Python `cryptography`:

  - Master key: APP_ENCRYPTION_KEY, at least 32 random bytes, base64
    (`openssl rand -base64 32`).
  - Per-app key: HKDF-SHA256(master, salt = fixed app label, info = app id), so a
    ciphertext copied to another app's row never decrypts.
  - Cipher: AES-256-GCM with a random 96-bit nonce; the app id is also bound as
    additional authenticated data.
  - Format: "v1." + base64url(nonce | ciphertext | tag). The version prefix lets a future
    scheme coexist with stored v1 values.

Error messages never include the secret or the key.
"""

from __future__ import annotations

import base64
import binascii
import os
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = "v1"
NONCE_BYTES = 12
TAG_BYTES = 16
KEY_BYTES = 32
HKDF_SALT = b"review-radar.secretbox.v1"
_BASE64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


class SecretboxError(Exception):
    """The key is missing or malformed, or a ciphertext cannot be decrypted."""


def _master_key(raw: str | None) -> bytes:
    if not raw:
        raise SecretboxError(
            "APP_ENCRYPTION_KEY is not set. Generate one with `openssl rand -base64 32`."
        )
    trimmed = raw.strip()
    try:
        key = base64.b64decode(trimmed, validate=True) if _BASE64.match(trimmed) else b""
    except binascii.Error:
        key = b""
    if len(key) < KEY_BYTES:
        raise SecretboxError(
            f"APP_ENCRYPTION_KEY must be at least {KEY_BYTES} bytes encoded as base64 "
            f"(got {len(key)} bytes). Generate one with `openssl rand -base64 32`."
        )
    return key


def _app_key(master: str | None, app_id: str) -> bytes:
    if not app_id:
        raise SecretboxError("An app id is required to derive its key.")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=HKDF_SALT, info=app_id.encode())
    return hkdf.derive(_master_key(master))


def encrypt_secret(master: str | None, app_id: str, plaintext: str) -> str:
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(_app_key(master, app_id)).encrypt(nonce, plaintext.encode(), app_id.encode())
    return f"{VERSION}.{base64.urlsafe_b64encode(nonce + sealed).rstrip(b'=').decode()}"


def decrypt_secret(master: str | None, app_id: str, ciphertext: str) -> str:
    parts = ciphertext.split(".")
    if len(parts) != 2 or parts[0] != VERSION or not parts[1]:
        raise SecretboxError("Unrecognised encrypted value format.")
    payload = parts[1]
    try:
        data = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    except (binascii.Error, ValueError) as exc:
        raise SecretboxError("Encrypted value is not valid base64url.") from exc
    if len(data) < NONCE_BYTES + TAG_BYTES:
        raise SecretboxError("Encrypted value is truncated.")
    key = _app_key(master, app_id)
    try:
        plain = AESGCM(key).decrypt(data[:NONCE_BYTES], data[NONCE_BYTES:], app_id.encode())
    except InvalidTag as exc:
        raise SecretboxError(
            "Could not decrypt the stored secret (wrong app, tampered value, or "
            "APP_ENCRYPTION_KEY changed)."
        ) from exc
    return plain.decode()


def mask_token(token: str) -> str:
    """Display form: the GitHub prefix plus the last 4 characters (`github_pat_…a1B2`)."""
    value = token.strip()
    match = re.match(r"^(github_pat_|ghp_|gho_|ghs_)", value)
    prefix = match.group(1) if match else ""
    rest = value[len(prefix) :]
    return f"{prefix}…{rest[-4:]}" if len(rest) >= 8 else f"{prefix}…"
