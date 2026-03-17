"""Security layer for ai-dev-platform."""

from security.jwt_handler import (
    create_access_token,
    create_service_token,
    decode_token,
    verify_service_token,
)
from security.prompt_guard import (
    sanitize_user_input,
    scan_for_injection,
    wrap_untrusted_input,
)
from security.rbac import (
    Role,
    get_bearer_token,
    get_current_user,
    require_admin,
    require_any_role,
    require_role,
)

__all__ = [
    # JWT
    "create_access_token",
    "create_service_token",
    "decode_token",
    "verify_service_token",
    # RBAC
    "Role",
    "get_bearer_token",
    "get_current_user",
    "require_role",
    "require_any_role",
    "require_admin",
    # Prompt Guard
    "scan_for_injection",
    "sanitize_user_input",
    "wrap_untrusted_input",
]
