"""Application configuration management."""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Security & Authentication
    app_secret_key: str = Field(
        default="your-super-secret-key-change-in-production",
        alias="APP_SECRET_KEY",
    )
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=60, alias="JWT_EXPIRE_MINUTES")

    # LLM Configuration (Groq)
    groq_api_key: str = Field(
        default="gsk-placeholder-your-groq-api-key", alias="GROQ_API_KEY"
    )
    llm_model: str = Field(default="llama-3.1-70b-versatile", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")

    # Database Configuration
    postgres_url: str = Field(
        default="postgresql+asyncpg://user:password@localhost:5432/aidevplatform",
        alias="POSTGRES_URL",
    )

    # Redis Configuration
    redis_url: str = Field(default="redis://localhost:6379", alias="REDIS_URL")

    # State Management
    checkpointer: str = Field(default="sqlite", alias="CHECKPOINTER")

    # Workspace Configuration
    workspace_base_path: str = Field(default="./workspaces", alias="WORKSPACE_BASE_PATH")

    # Sandbox Configuration
    sandbox_image: str = Field(default="node:18-alpine", alias="SANDBOX_IMAGE")
    sandbox_cpu_limit: str = Field(default="1000", alias="SANDBOX_CPU_LIMIT")
    sandbox_memory_limit: str = Field(default="512MB", alias="SANDBOX_MEMORY_LIMIT")
    sandbox_timeout_seconds: int = Field(default=30, alias="SANDBOX_TIMEOUT_SECONDS")

    # Logging & Environment
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # Service Token
    service_token_secret: str = Field(
        default="your-service-token-secret-change-in-production",
        alias="SERVICE_TOKEN_SECRET",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid_levels:
            raise ValueError(f"LOG_LEVEL must be one of {valid_levels}")
        return v.upper()

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        valid_environments = {"development", "production", "testing"}
        if v.lower() not in valid_environments:
            raise ValueError(
                f"ENVIRONMENT must be one of {valid_environments}"
            )
        return v.lower()

    @field_validator("checkpointer")
    @classmethod
    def validate_checkpointer(cls, v: str) -> str:
        valid_checkpointers = {"sqlite", "postgres"}
        if v.lower() not in valid_checkpointers:
            raise ValueError(
                f"CHECKPOINTER must be one of {valid_checkpointers}"
            )
        return v.lower()


def validate_secrets() -> None:
    """Validate that all required secrets are properly configured.

    Raises:
        RuntimeError: If any required secret is missing or still set to a placeholder.
    """
    settings = Settings()

    placeholder_values = {
        "your-super-secret-key-change-in-production": "APP_SECRET_KEY",
        "gsk-placeholder-your-groq-api-key": "GROQ_API_KEY",
        "your-service-token-secret-change-in-production": "SERVICE_TOKEN_SECRET",
    }

    critical_secrets = {
        "app_secret_key": "APP_SECRET_KEY",
        "groq_api_key": "GROQ_API_KEY",
        "service_token_secret": "SERVICE_TOKEN_SECRET",
        "postgres_url": "POSTGRES_URL",
        "redis_url": "REDIS_URL",
    }

    errors = []

    for attr_name, env_var_name in critical_secrets.items():
        value = getattr(settings, attr_name, None)

        if not value:
            errors.append(f"Required secret '{env_var_name}' is not set")
            continue

        for placeholder, secret_name in placeholder_values.items():
            if value == placeholder and secret_name == env_var_name:
                errors.append(
                    f"Required secret '{env_var_name}' is still set to placeholder value. "
                    f"Please update it in your .env file."
                )
                break

    if errors:
        error_message = "\n".join(f"  - {err}" for err in errors)
        raise RuntimeError(
            f"Configuration validation failed:\n{error_message}"
        )

    logger.info(
        "Configuration validation successful",
        environment=settings.environment,
        log_level=settings.log_level,
    )


# Global settings instance
settings = Settings()
