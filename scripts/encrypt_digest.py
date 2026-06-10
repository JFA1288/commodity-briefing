"""Encrypt docs/data/latest.json with AES-256-GCM for client-side password gate.

Usage (called by GitHub Actions after src.main):
    DASHBOARD_PASSWORD=<secret> python scripts/encrypt_digest.py

Writes docs/data/latest.enc.json and removes the plaintext latest.json.
"""

import base64
import json
import os
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PLAINTEXT = Path("docs/data/latest.json")
ENCRYPTED = Path("docs/data/latest.enc.json")
ITERATIONS = 100_000


def encrypt(password: str, plaintext: bytes) -> dict:
    salt = secrets.token_bytes(16)
    iv = secrets.token_bytes(12)

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS)
    key = kdf.derive(password.encode())

    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)

    return {
        "salt": base64.b64encode(salt).decode(),
        "iv": base64.b64encode(iv).decode(),
        "data": base64.b64encode(ciphertext).decode(),
        "iterations": ITERATIONS,
    }


def main() -> None:
    password = os.environ.get("DASHBOARD_PASSWORD", "").strip()
    if not password:
        print("DASHBOARD_PASSWORD not set — skipping encryption, latest.json left in place.")
        sys.exit(0)

    if not PLAINTEXT.exists():
        print(f"ERROR: {PLAINTEXT} not found.")
        sys.exit(1)

    plaintext = PLAINTEXT.read_bytes()
    encrypted = encrypt(password, plaintext)

    ENCRYPTED.write_text(json.dumps(encrypted))
    PLAINTEXT.unlink()  # remove plaintext so it is not published

    print(f"Encrypted {PLAINTEXT} → {ENCRYPTED} ({len(plaintext)} → {len(encrypted['data'])} bytes b64)")


if __name__ == "__main__":
    main()
