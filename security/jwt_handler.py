"""JWT token creation and validation."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from jose import JWTError, jwt

from config import settings

logger = logging.getLogger(__name__)


def create_access_token(
    data: Dict[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    """Create a JWT access token.

    Args:
        data: Payload data to encode in token.
        expires_delta: Optional custom expiration time. Defaults to JWT_EXPIRE_MINUTES from settings.

    Returns:
        Encoded JWT token string.
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.jwt_expire_minutes
        )

    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    encoded_jwt = jwt.encode(
        to_encode, settings.app_secret_key, algorithm=settings.jwt_algorithm
    )
    logger.debug("Access token created", subject=data.get("sub"))
    return encoded_jwt


def create_service_token(agent_name: str, expires_delta: Optional[timedelta] = None) -> str:
    """Create a short-lived service token for inter-service calls.

    Args:
        agent_name: Name of the agent/service requesting the token.
        expires_delta: Optional custom expiration time. Defaults to 5 minutes.

    Returns:
        Encoded JWT token string.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=5)

    data = {
        "sub": agent_name,
        "type": "service",
        "scope": "inter_service",
    }

    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + expires_delta

    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    encoded_jwt = jwt.encode(
        to_encode, settings.service_token_secret, algorithm=settings.jwt_algorithm
    )
    logger.debug("Service token created", agent=agent_name)
    return encoded_jwt


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT access token.

    Args:
        token: JWT token string to decode.

    Returns:
        Decoded token payload dictionary.

    Raises:
        HTTPException: 401 Unauthorized if token is invalid, expired, or malformed.
    """
    try:
        payload = jwt.decode(
            token, settings.app_secret_key, algorithms=[settings.jwt_algorithm]
        )
        logger.debug("Token decoded successfully", subject=payload.get("sub"))
        return payload
    except JWTError as e:
        logger.warning("Token decode failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


def verify_service_token(token: str) -> str:
    """Verify and extract agent name from service token.

    Args:
        token: JWT service token string to verify.

    Returns:
        Agent name extracted from token.

    Raises:
        HTTPException: 401 if token is invalid/expired, 403 if not a service token.
    """
    try:
        payload = jwt.decode(
            token, settings.service_token_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as e:
        logger.warning("Service token verification failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired service token",
        ) from e

    # Verify this is a service token
    if payload.get("type") != "service":
        logger.warning("Token is not a service token", subject=payload.get("sub"))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token is not authorized for service calls",
        )

    agent_name = payload.get("sub")
    if not agent_name:
        logger.warning("Service token missing agent name")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Service token missing required claims",
        )

    logger.debug("Service token verified", agent=agent_name)
    return agent_name
