"""
Encrypts/decrypts Alpaca API credentials before they touch the database.

This uses symmetric (Fernet) encryption with a single server-side master
key. That's a reasonable baseline for a small self-hosted service, but it
means anyone with both the database AND the MASTER_KEY env var can decrypt
everyone's keys. Before this goes multi-tenant / hosted for real, upgrade to:
  - a proper secrets manager (AWS KMS, GCP KMS, HashiCorp Vault) instead of
    a raw env var, so the key itself is never on the app server's disk
  - per-user encryption keys derived from their login password (so a DB
    leak alone isn't enough — you'd also need each user's password)
  - TLS everywhere, so credentials are never sent in plaintext over HTTP
"""
import os

from cryptography.fernet import Fernet, InvalidToken


def _get_fernet():
    key = os.environ.get("MASTER_KEY")
    if not key:
        raise RuntimeError(
            "MASTER_KEY environment variable is not set. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
            "and set it as an environment variable. Never commit it to git."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    if plaintext is None:
        return None
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if ciphertext is None:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Could not decrypt credential — wrong MASTER_KEY or corrupted data.") from e
