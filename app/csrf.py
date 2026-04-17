"""
CSRF protection middleware for FastAPI/Starlette.

Uses the Synchronizer Token Pattern:
- A random token is stored in the user's session.
- Every HTML form must include the token as a hidden field named "csrf_token".
- POST/PUT/DELETE/PATCH requests are rejected if the token is missing or wrong.
- Login POST is exempt (no session yet).

Implementation note: We cannot consume request.form() in middleware because
FastAPI's dependency injection (Form(...)) also needs the body stream.
Instead, we use a response hook to inject the token and rely on the per-route
CSRF validation via a shared dependency.
"""

import secrets
import logging
from functools import wraps

from fastapi import Request, HTTPException

logger = logging.getLogger(__name__)

CSRF_SESSION_KEY = "_csrf_token"
CSRF_FIELD_NAME = "csrf_token"
TOKEN_LENGTH = 32

# Paths exempt from CSRF check (no session available yet)
EXEMPT_PATHS = {"/login"}


def get_or_create_token(request: Request) -> str:
    """Get existing CSRF token from session, or create a new one."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_hex(TOKEN_LENGTH)
        request.session[CSRF_SESSION_KEY] = token
    return token


async def validate_csrf(request: Request) -> None:
    """
    Validate CSRF token for state-changing requests.
    Call this at the beginning of POST route handlers.
    Reads the token from the already-parsed form data.
    """
    if request.url.path in EXEMPT_PATHS:
        return

    expected = request.session.get(CSRF_SESSION_KEY)
    if not expected:
        logger.warning("CSRF: no token in session for %s %s", request.method, request.url.path)
        raise HTTPException(status_code=403, detail="CSRF token missing from session")

    # Try form field first, then header
    submitted = None
    try:
        form = await request.form()
        submitted = form.get(CSRF_FIELD_NAME)
    except Exception:
        pass
    if not submitted:
        submitted = request.headers.get("x-csrf-token")

    if not submitted or not secrets.compare_digest(str(submitted), expected):
        logger.warning("CSRF: validation failed for %s %s", request.method, request.url.path)
        raise HTTPException(status_code=403, detail="CSRF validation failed")
