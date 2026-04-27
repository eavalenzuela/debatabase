"""Authentication primitives for the IRC-style nickname/password flow.

Single shared argon2 hasher (the library recommends reusing one). All
password I/O — hashing on register, verification on login — goes through
this module so the route layer never touches the algorithm directly.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()

MIN_PASSWORD_LEN = 8


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored_hash: str | None, plain: str) -> bool:
    """Return True iff `plain` matches the stored argon2 hash.

    A NULL stored_hash never matches — this is what keeps the bootstrap
    "local" placeholder user (which exists for pre-auth single-user
    mode) from being logged into without a password being set.
    """
    if not stored_hash:
        return False
    try:
        _hasher.verify(stored_hash, plain)
    except VerifyMismatchError:
        return False
    except Exception:
        return False
    return True


def validate_password(plain: str) -> str | None:
    """Return an error message if the password is invalid, else None."""
    if len(plain) < MIN_PASSWORD_LEN:
        return f"password must be at least {MIN_PASSWORD_LEN} characters"
    return None


def validate_nickname(nick: str) -> str | None:
    nick = nick.strip()
    if not nick:
        return "nickname required"
    if len(nick) > 32:
        return "nickname too long (max 32)"
    if any(c.isspace() for c in nick):
        return "nickname cannot contain spaces"
    return None
