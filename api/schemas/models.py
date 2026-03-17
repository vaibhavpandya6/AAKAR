"""Pydantic request/response models for all API endpoints.

All models use strict validation — no unknown fields are allowed in
request bodies and all timestamps are serialised to ISO-8601 UTC strings.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------------------------------------------------------------------------
# Auth models
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    """Registration request body."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="developer", pattern=r"^(admin|developer|viewer)$")

    model_config = {"str_strip_whitespace": True}


class LoginRequest(BaseModel):
    """Login request body."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds")


class UserResponse(BaseModel):
    """Current-user info returned by GET /auth/me."""

    id: str
    email: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Project models
# ---------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    """Create-project request body."""

    prompt: str = Field(
        min_length=10,
        max_length=5000,
        description="Natural-language description of the project to build.",
    )

    @field_validator("prompt")
    @classmethod
    def no_empty_whitespace(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("prompt must not be blank")
        return stripped


class ProjectResponse(BaseModel):
    """Project creation/retrieval response."""

    id: str
    status: str
    prompt: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectStatusResponse(BaseModel):
    """Detailed project execution status."""

    project_id: str
    status: str
    project_summary: str = ""
    pending_tasks: list[dict[str, Any]] = Field(default_factory=list)
    in_progress_tasks: list[dict[str, Any]] = Field(default_factory=list)
    completed_tasks: list[dict[str, Any]] = Field(default_factory=list)
    failed_tasks: list[dict[str, Any]] = Field(default_factory=list)
    files_written: list[str] = Field(default_factory=list)
    bug_reports: list[dict[str, Any]] = Field(default_factory=list)
    error_message: Optional[str] = None
    updated_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Task DAG models
# ---------------------------------------------------------------------------


class TaskItem(BaseModel):
    """A single task within the planner DAG."""

    id: str
    title: str
    description: str = ""
    skill_required: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class TaskDAGResponse(BaseModel):
    """Full task plan returned at the HITL review step."""

    project_id: str
    project_summary: str
    total_tasks: int
    skill_breakdown: dict[str, int] = Field(default_factory=dict)
    tasks: list[TaskItem] = Field(default_factory=list)
    plan_approved: bool = False
    status: str = "AWAITING_APPROVAL"


# ---------------------------------------------------------------------------
# HITL plan approval models
# ---------------------------------------------------------------------------


class PlanApprovalRequest(BaseModel):
    """Body for POST /projects/{id}/plan/approve."""

    approved: bool
    feedback: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Required when approved=False; tells the planner what to change.",
    )

    @field_validator("feedback")
    @classmethod
    def feedback_required_on_rejection(
        cls, v: Optional[str], info: Any
    ) -> Optional[str]:
        # Can't access sibling fields easily in Pydantic v2 validators on individual
        # fields, so we do a model-level check instead (see model_validator).
        return v

    def model_post_init(self, __context: Any) -> None:
        if not self.approved and not self.feedback:
            raise ValueError("feedback is required when approved=False")


class PlanApprovalResponse(BaseModel):
    """Response to a plan approval/rejection request."""

    status: str = Field(
        description="One of: 'resumed' | 'replanning'",
        pattern=r"^(resumed|replanning)$",
    )
    message: str


# ---------------------------------------------------------------------------
# Log model
# ---------------------------------------------------------------------------


class LogEntry(BaseModel):
    """A single agent-log record."""

    id: str
    project_id: str
    task_id: Optional[str] = None
    agent: str
    action: str
    file_path: Optional[str] = None
    status: str
    duration_ms: int
    metadata: Optional[dict[str, Any]] = None
    timestamp: datetime

    model_config = {"from_attributes": True}


class LogsResponse(BaseModel):
    """Paginated log listing."""

    project_id: str
    total: int
    entries: list[LogEntry]


# ---------------------------------------------------------------------------
# File models
# ---------------------------------------------------------------------------


class FileListResponse(BaseModel):
    """Workspace file manifest listing."""

    project_id: str
    total_files: int
    files: list[str]


class FileContentResponse(BaseModel):
    """Single file content response."""

    project_id: str
    file_path: str
    content: str
    size_bytes: int


# ---------------------------------------------------------------------------
# Rollback model
# ---------------------------------------------------------------------------


class RollbackRequest(BaseModel):
    """Rollback-to-tag request body."""

    tag: str = Field(
        min_length=1,
        max_length=200,
        description="Git tag to roll back to (e.g. 'delivered-20250101_120000').",
    )

    @field_validator("tag")
    @classmethod
    def sanitize_tag(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("tag must not be blank")
        return stripped


class RollbackResponse(BaseModel):
    """Rollback operation result."""

    project_id: str
    tag: str
    status: str = "rolled_back"
    message: str


# ---------------------------------------------------------------------------
# Health model
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """GET /health response."""

    status: str = "ok"
    timestamp: str
    version: str = "1.0.0"
    environment: str
    metrics: dict = Field(default_factory=dict)
