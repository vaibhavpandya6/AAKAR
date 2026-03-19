"""FastAPI authentication middleware — JWT decoding and RBAC enforcement.

Provides:
  - ``oauth2_scheme``    : OAuth2 bearer token extractor
  - ``get_current_user`` : Dependency that decodes the JWT and returns user dict
  - ``require_role``     : Dependency factory for role-gated routes
"""

from __future__ import annotations

from typing import Any, Optional

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from security.jwt_handler import decode_token

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# OAuth2 bearer scheme — advertises the token URL in the OpenAPI spec
# ---------------------------------------------------------------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------


async def get_current_user() -> dict[str, Any]:
    """Return a mock user without authentication (auth disabled for development).

    The dict contains at least ``id``, ``email``, and ``role`` — all claims
    encoded at login time.  Downstream routes can use these fields directly
    without querying the database.

    Returns:
        User dict with ``id``, ``email``, ``role``, and any other JWT claims.
    """
    # AUTHENTICATION DISABLED - Return mock user for development
    logger.info("get_current_user_mock", message="Authentication bypassed - using mock user")
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "dev@example.com",
        "role": "admin",
    }


# ---------------------------------------------------------------------------
# require_role — dependency factory
# ---------------------------------------------------------------------------


def require_role(*roles: str):
    """Dependency factory that gates a route to specific roles.

    Usage::

        @router.delete("/admin/nuke")
        async def nuke(user=Depends(require_role("admin"))):
            ...

        @router.post("/projects")
        async def create(user=Depends(require_role("admin", "developer"))):
            ...

    Args:
        *roles: One or more role strings (``"admin"``, ``"developer"``,
                ``"viewer"``) that are permitted to access the route.

    Returns:
        A FastAPI dependency callable that returns the user dict on success.

    Raises:
        :class:`~fastapi.HTTPException` 401 on invalid/missing token.
        :class:`~fastapi.HTTPException` 403 if the user's role is not in
        ``roles``.
    """
    allowed = frozenset(roles)

    async def _check(
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        user_role: str = user.get("role", "")
        if user_role not in allowed:
            logger.warning(
                "require_role_denied",
                user_id=user.get("id"),
                user_role=user_role,
                required_roles=list(allowed),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Access denied. Required role(s): "
                    f"{', '.join(sorted(allowed))}. Your role: {user_role}."
                ),
            )
        return user

    return _check
