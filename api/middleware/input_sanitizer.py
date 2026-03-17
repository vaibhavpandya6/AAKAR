"""Input sanitizer middleware — prompt-injection scanning and sanitization.

Intercepts every POST, PUT, and PATCH request.  For JSON bodies:

1. Parses the raw body as JSON.
2. Recursively collects all string values and concatenates them.
3. Calls :func:`~security.prompt_guard.scan_for_injection` on the combined text.
4. If injection is detected: immediately returns HTTP 400 with the reason.
5. Calls :func:`~security.prompt_guard.sanitize_user_input` on each string value
   (strips null bytes, collapses whitespace, truncates to 4000 chars).
6. Re-serialises the sanitized body and replaces ``request._body`` so the
   route handler receives clean data.

Non-JSON bodies (form data, binary uploads) are passed through unchanged.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from security.prompt_guard import sanitize_user_input, scan_for_injection

logger = structlog.get_logger()

# HTTP methods on which the middleware acts
_CHECKED_METHODS = frozenset({"POST", "PUT", "PATCH"})


class InputSanitizerMiddleware(BaseHTTPMiddleware):
    """Scans and sanitizes JSON request bodies for prompt injection.

    Registered via :func:`fastapi.FastAPI.add_middleware` in ``api/main.py``.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Process the request, sanitising JSON bodies on write methods.

        Args:
            request: Incoming Starlette/FastAPI request.
            call_next: Next middleware or route handler in the chain.

        Returns:
            The downstream response, or an HTTP 400 if injection is detected.
        """
        if request.method not in _CHECKED_METHODS:
            return await call_next(request)

        # Skip the /auth/login endpoint so credentials are never accidentally
        # mutated by the sanitizer (passwords may contain special chars).
        if request.url.path.rstrip("/") in ("/auth/login", "/auth/register"):
            return await call_next(request)

        # Read and cache the body — Starlette stores it in request._body so
        # subsequent reads by the route handler return the cached value.
        try:
            raw_body: bytes = await request.body()
        except Exception as exc:
            logger.warning("sanitizer_body_read_error", error=str(exc))
            return await call_next(request)

        if not raw_body:
            return await call_next(request)

        # Attempt to parse as JSON — non-JSON bodies are passed through
        try:
            data: Any = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return await call_next(request)

        # ── 1. Collect all string values for injection scanning ──────────────
        combined_text = _collect_strings(data)

        if combined_text:
            is_safe, reason = scan_for_injection(combined_text)
            if not is_safe:
                logger.warning(
                    "sanitizer_injection_blocked",
                    path=str(request.url.path),
                    method=request.method,
                    reason=reason,
                )
                return JSONResponse(
                    status_code=400,
                    content={
                        "detail": reason,
                        "code": "injection_detected",
                    },
                )

        # ── 2. Sanitize every string value in the payload ───────────────────
        sanitized_data = _sanitize_recursive(data)

        # ── 3. Replace the cached body so the route handler sees clean data ──
        try:
            sanitized_bytes = json.dumps(
                sanitized_data, ensure_ascii=False
            ).encode("utf-8")
            # Monkey-patch the cached body; downstream route handlers read this.
            request._body = sanitized_bytes  # type: ignore[attr-defined]
        except (TypeError, ValueError) as exc:
            # If re-serialisation fails, let the original body through
            logger.warning("sanitizer_reserialise_failed", error=str(exc))

        return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_strings(obj: Any) -> str:
    """Recursively collect all string values from a JSON object.

    Args:
        obj: Arbitrary JSON-decoded Python object.

    Returns:
        Single string containing all string values joined by a space.
    """
    parts: list[str] = []

    if isinstance(obj, str):
        parts.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            parts.append(_collect_strings(v))
    elif isinstance(obj, list):
        for item in obj:
            parts.append(_collect_strings(item))

    return " ".join(filter(None, parts))


def _sanitize_recursive(obj: Any) -> Any:
    """Recursively sanitize all string values in a JSON object.

    Applies :func:`~security.prompt_guard.sanitize_user_input` to every
    string leaf.  Non-string values are returned unchanged.

    Args:
        obj: Arbitrary JSON-decoded Python object.

    Returns:
        Object with the same structure but sanitized string values.
    """
    if isinstance(obj, str):
        return sanitize_user_input(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_recursive(item) for item in obj]
    return obj
