"""Secure Password Hashing and Validation Utilities.

DESIGN JUSTIFICATION (bcrypt vs argon2-cffi):
1. Native Compatibility with Supabase: Supabase Auth uses bcrypt to hash passwords.
   By adopting bcrypt locally, we can seamlessly import existing Supabase user password
   hashes (via scripts in Phase 3) without forcing active users to reset passwords.
2. Platform Portability: bcrypt has stable, precompiled binary wheels for Windows, macOS,
   and Linux, reducing build-time dependencies on local developer machines compared to
   argon2-cffi, which relies on a C compilation step for some older Python/platform configurations.
"""
import re
import bcrypt


def validate_password_strength(password: str) -> None:
    """
    Validates that a password meets strength requirements:
    - Minimum 8 characters.
    - At least one letter.
    - At least one number.
    """
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("Password must contain at least one letter and one number")


import asyncio

async def hash_password(password: str) -> str:
    """
    Hashes a plaintext password using bcrypt.
    """
    validate_password_strength(password)
    # Generate salt and hash
    def _hash():
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode("utf-8"), salt)
    hashed = await asyncio.to_thread(_hash)
    return hashed.decode("utf-8")


async def verify_password(password: str, hashed_password: str) -> bool:
    """
    Verifies a plaintext password against a bcrypt hash in a timing-safe manner.
    """
    try:
        return await asyncio.to_thread(
            bcrypt.checkpw, password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except Exception:
        return False