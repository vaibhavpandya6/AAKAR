"""Message schemas and validation."""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator


class MessageType(str, Enum):
    """Inter-agent message types."""

    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_COMPLETE = "TASK_COMPLETE"
    TASK_FAILED = "TASK_FAILED"
    BUG_REPORT = "BUG_REPORT"
    FILE_WRITTEN = "FILE_WRITTEN"
    REVIEW_REQUEST = "REVIEW_REQUEST"
    REVIEW_RESULT = "REVIEW_RESULT"
    HITL_REQUIRED = "HITL_REQUIRED"
    LOCK_TIMEOUT = "LOCK_TIMEOUT"
    MERGE_CONFLICT = "MERGE_CONFLICT"


class Message(BaseModel):
    """Inter-agent message model."""

    message_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique message identifier",
    )
    correlation_id: str = Field(
        description="Correlation ID in format 'project_id:task_id'",
    )
    sender: str = Field(description="Name of sending agent")
    recipient: str = Field(description="Name of recipient agent")
    message_type: MessageType = Field(description="Type of message")
    payload: Dict[str, Any] = Field(
        default_factory=dict, description="Message payload data"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Message creation timestamp",
    )
    schema_version: str = Field(
        default="1.0", description="Schema version for compatibility"
    )

    @field_validator("correlation_id")
    @classmethod
    def validate_correlation_id(cls, v: str) -> str:
        """Validate correlation_id format: project_id:task_id."""
        if not v or ":" not in v:
            raise ValueError("correlation_id must be in format 'project_id:task_id'")
        parts = v.split(":")
        if len(parts) != 2 or not all(parts):
            raise ValueError("correlation_id must have exactly 2 non-empty parts")
        return v

    @field_validator("sender", "recipient")
    @classmethod
    def validate_agent_name(cls, v: str) -> str:
        """Validate agent names are non-empty strings."""
        if not v or not isinstance(v, str):
            raise ValueError("Agent name must be a non-empty string")
        if len(v) > 100:
            raise ValueError("Agent name must be 100 characters or less")
        return v

    def model_dump_redis(self) -> Dict[str, Any]:
        """Convert message to Redis-compatible dictionary format.

        Returns:
            Dictionary with serialized values for Redis Storage.
        """
        return {
            "message_id": self.message_id,
            "correlation_id": self.correlation_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "message_type": self.message_type.value,
            "payload": str(self.payload),  # Redis stores as string
            "timestamp": self.timestamp.isoformat(),
            "schema_version": self.schema_version,
        }

    @classmethod
    def model_validate_redis(cls, data: Dict[str, Any]) -> "Message":
        """Create Message from Redis data.

        Args:
            data: Dictionary from Redis stream.

        Returns:
            Message instance.
        """
        import json

        return cls(
            message_id=data.get("message_id", str(uuid.uuid4())),
            correlation_id=data["correlation_id"],
            sender=data["sender"],
            recipient=data["recipient"],
            message_type=MessageType(data["message_type"]),
            payload=json.loads(data.get("payload", "{}")),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            schema_version=data.get("schema_version", "1.0"),
        )


def validate_message(data: Dict[str, Any]) -> Message:
    """Validate and create message from dictionary.

    Args:
        data: Dictionary to validate.

    Returns:
        Validated Message instance.

    Raises:
        ValueError: If validation fails.
    """
    try:
        return Message(**data)
    except Exception as e:
        raise ValueError(f"Message validation failed: {str(e)}") from e
