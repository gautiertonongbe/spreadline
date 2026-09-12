"""Security primitives.

Spreadline v0.1 has one operator and deliberately does not ship a login flow
(spec §37). What it does ship is the *seam*: every request resolves an
``AuthContext`` through ``get_auth_context``, and every query filters by
``organization_id``. Adding real authentication later means replacing the body
of one function, not auditing every route.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class AuthContext:
    """Who is acting, and inside which organization."""

    organization_id: str
    user_id: str | None = None
    email: str | None = None
    is_service: bool = False

    @property
    def actor(self) -> str:
        return self.email or self.user_id or ("service" if self.is_service else "anonymous")


def hash_secret(secret: str, *, salt: str | None = None) -> str:
    """PBKDF2-SHA256. Present so no caller is ever tempted to store a raw secret."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), 240_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_secret(secret: str, encoded: str) -> bool:
    try:
        algorithm, salt, _ = encoded.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    return hmac.compare_digest(hash_secret(secret, salt=salt), encoded)


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def redact_credential(value: str | None) -> str:
    """Render a credential for display without disclosing it."""
    if not value:
        return "not configured"
    return f"configured (…{value[-4:]})" if len(value) > 8 else "configured"


def provider_credentials(provider_slug: str) -> str | None:
    return {
        "amazon": settings.amazon_provider_credentials,
        "walmart": settings.walmart_provider_credentials,
    }.get(provider_slug)
