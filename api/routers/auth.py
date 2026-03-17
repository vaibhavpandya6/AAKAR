"""Authentication router — register, login, and current-user endpoints."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.middleware.auth import get_current_user
from api.schemas.models import LoginRequest, TokenResponse, UserCreate, UserResponse
from config import settings
from db.connection import get_db
from db.models import User, UserRole
from security.jwt_handler import create_access_token

logger = structlog.get_logger()

router = APIRouter(prefix="/auth", tags=["auth"])

# Password hashing context — bcrypt with automatic pepper management
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def _verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)


def _make_token(user: User) -> str:
    """Create a JWT for the given user row."""
    return create_access_token(
        data={
            "sub": str(user.id),
            "email": user.email,
            "role": user.role.value,
        },
        expires_delta=timedelta(minutes=settings.jwt_expire_minutes),
    )


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user and receive a JWT",
)
async def register(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Create a new user account.

    - ``email`` must be unique — returns **409** if already taken.
    - ``password`` is hashed with bcrypt before storage; the plaintext is
      never persisted.
    - Returns a JWT on success so the client can start using the API
      immediately.

    Args:
        body: Registration request (email, password, optional role).
        db: Injected async database session.

    Returns:
        :class:`~api.schemas.models.TokenResponse` with a bearer token.
    """
    # Check for duplicate email
    result = await db.execute(select(User).where(User.email == body.email))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with email '{body.email}' already exists.",
        )

    # Map role string → enum (default developer)
    try:
        role_enum = UserRole(body.role)
    except ValueError:
        role_enum = UserRole.DEVELOPER

    user = User(
        id=uuid.uuid4(),
        email=body.email,
        password_hash=_hash_password(body.password),
        role=role_enum,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = _make_token(user)

    logger.info("user_registered", user_id=str(user.id), email=user.email, role=role_enum.value)

    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
    )


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and receive a JWT",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Validate credentials and return a JWT access token.

    Returns **401** for both unknown email and wrong password to avoid
    user enumeration attacks.

    Args:
        body: Login request (email + password).
        db: Injected async database session.

    Returns:
        :class:`~api.schemas.models.TokenResponse` with a bearer token.
    """
    result = await db.execute(select(User).where(User.email == body.email))
    user: User | None = result.scalar_one_or_none()

    if not user or not _verify_password(body.password, user.password_hash):
        logger.warning("login_failed", email=body.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = _make_token(user)

    logger.info("user_login", user_id=str(user.id), email=user.email)

    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
    )


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Return the currently authenticated user",
)
async def me(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Return profile information for the authenticated user.

    Performs a lightweight DB look-up to return full user data (including
    ``created_at``) rather than only the JWT claims.

    Args:
        current_user: Decoded JWT claims from the auth middleware.
        db: Injected async database session.

    Returns:
        :class:`~api.schemas.models.UserResponse`.
    """
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(current_user["id"]))
    )
    user: User | None = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    return UserResponse(
        id=str(user.id),
        email=user.email,
        role=user.role.value,
        created_at=user.created_at,
    )
