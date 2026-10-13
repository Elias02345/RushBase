"""Password hashing (argon2id) and opaque, hashed tokens."""

import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 10
WORKER_TOKEN_PREFIX = "rbw"  # noqa: S105 - token type tag, not a secret
PERSONAL_TOKEN_PREFIX = "rbp"  # noqa: S105 - token type tag, not a secret

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters
_DUMMY_HASH = _hasher.hash("rushbase-timing-equalisation")


class WeakPasswordError(ValueError):
    """The password does not meet the minimum requirements."""


def validate_password(password: str) -> None:
    """Raise :class:`WeakPasswordError` when ``password`` is too short."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


async def hash_password(password: str) -> str:
    """Hash ``password`` with argon2id (in a worker thread; hashing is deliberately slow)."""
    validate_password(password)
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    """Verify ``password``; with ``password_hash=None`` a dummy hash equalises timing."""
    target = password_hash or _DUMMY_HASH

    def check() -> bool:
        try:
            return _hasher.verify(target, password) and password_hash is not None
        except (VerificationError, InvalidHashError):
            return False

    return await asyncio.to_thread(check)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest under which ``token`` is stored.

    Tokens are 256-bit random values, so a fast hash is sufficient (no brute-force surface).
    """
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two strings without leaking where they differ."""
    return hmac.compare_digest(left.encode(), right.encode())


@dataclass(frozen=True)
class NewToken:
    """A freshly generated token: the plaintext is shown once and never stored."""

    plaintext: str
    token_hash: str
    prefix: str


def new_token(kind_prefix: str) -> NewToken:
    """Generate ``<kind_prefix>_<43 url-safe chars>`` and its storage hash/prefix."""
    plaintext = f"{kind_prefix}_{secrets.token_urlsafe(32)}"
    return NewToken(plaintext, hash_token(plaintext), plaintext[:8])


def new_secret(nbytes: int = 32) -> str:
    """Return a URL-safe random string (session tokens, CSRF tokens)."""
    return secrets.token_urlsafe(nbytes)
