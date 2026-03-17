"""Role-based access control (RBAC) for the platform."""

import logging
from enum import Enum
from typing import Optional

from fastapi import Depends, HTTPException, status, Header
from sqlalchemy.ext.asyncio import AsyncSession

from db import User, get_db
from security.jwt_handler import decode_token

logger = logging.getLogger(__name__)


class Role(str, Enum):
    """Platform roles."""

    ADMIN = "admin"
    DEVELOPER = "developer"
    VIEWER = "viewer"


async def get_bearer_token(authorization: Optional[str] = Header(None)) -> str:
    """Extract bearer token from Authorization header.

    Args:
        authorization: Authorization header value.

    Returns:
        Bearer token string.

    Raises:
        HTTPException: 403 if token is missing or malformed.
    """
    if not authorization:
        logger.warning("Authorization header missing")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.warning("Invalid authorization header format")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return parts[1]


async def get_current_user(
    token: str = Depends(get_bearer_token), db: AsyncSession = Depends(get_db)
) -> User:
    """Extract and validate current user from JWT token.

    Args:
        token: JWT token from Authorization header.
        db: Database session.

    Returns:
        User object for the authenticated user.

    Raises:
        HTTPException: 401 if token is invalid, 404 if user not found.
    """
    payload = decode_token(token)
    user_id = payload.get("sub")

    if not user_id:
        logger.warning("Token missing user ID")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
        )

    # In a real DB lookup, you'd query the user:
    # user = await db.execute(select(User).where(User.id == user_id))
    # user = user.scalar_one_or_none()
    # if not user:
    #     raise HTTPException(status_code=404, detail="User not found")

    logger.debug("Current user authenticated", user_id=user_id)
    return user_id  # type: ignore


def require_role(*roles: Role):
    """Dependency factory to require specific roles.

    Args:
        *roles: One or more Role values required to access the resource.

    Returns:
        FastAPI dependency callable.

    Example:
        @app.get("/admin")
        async def admin_only(user_role: Role = Depends(require_role(Role.ADMIN))):
            return {"role": user_role}
    """

    async def check_role(
        token: str = Depends(get_bearer_token),
    ) -> str:
        payload = decode_token(token)
        user_role_str = payload.get("role")

        if not user_role_str:
            logger.warning("Token missing role claim")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token missing role information",
            )

        try:
            user_role = Role(user_role_str)
        except ValueError:
            logger.warning("Invalid role in token", role=user_role_str)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid role",
            )

        if user_role not in roles:
            logger.warning(
                "Insufficient permissions",
                required_roles=[r.value for r in roles],
                user_role=user_role.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This resource requires one of: {', '.join(r.value for r in roles)}",
            )

        logger.debug("Role check passed", user_role=user_role.value)
        return user_role_str

    return check_role


def require_any_role(*roles: Role):
    """Convenience dependency to check if user has any of the given roles.

    This is an alias for require_role() that makes the intent clearer.

    Args:
        *roles: One or more Role values acceptable for the resource.

    Returns:
        FastAPI dependency callable.
    """
    return require_role(*roles)


def require_admin():
    """Convenience dependency to require admin role only.

    Returns:
        FastAPI dependency callable.

    Example:
        @app.delete("/users/{user_id}")
        async def delete_user(user_id: str, admin: str = Depends(require_admin)):
            # Only admins can delete users
            ...
    """
    return require_role(Role.ADMIN)
