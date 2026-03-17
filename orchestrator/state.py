"""Shared state schema for the LangGraph orchestration graph.

Every node in the graph reads from and writes back to PlatformState.
No agent or router may hold local state — everything flows through here.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Status enumerations
# ---------------------------------------------------------------------------


class ProjectStatus(str, Enum):
    """Lifecycle status for the overall project."""

    CREATED = "CREATED"
    PLANNING = "PLANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    IN_PROGRESS = "IN_PROGRESS"
    QA = "QA"
    REVIEW = "REVIEW"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class TaskStatus(str, Enum):
    """Execution status for an individual task."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# PlatformState — the single source of truth flowing through every graph node
# ---------------------------------------------------------------------------


class PlatformState(TypedDict):
    """Complete LangGraph state schema shared by all graph nodes.

    Fields are grouped by concern.  All lists must default to [] and all
    optional scalars to None so that every graph node receives a valid object
    even on the very first invocation.
    """

    # ── Project identity ───────────────────────────────────────────────────
    project_id: str
    user_id: str
    original_prompt: str
    project_status: ProjectStatus

    # ── Planning outputs ───────────────────────────────────────────────────
    project_summary: str            # High-level description produced by planner
    task_dag: list[dict]            # Raw task list with depends_on fields from planner
    plan_feedback: str              # Human feedback when plan is rejected (HITL loop)
    plan_approved: bool             # Gate that must be True before execution begins

    # ── Task execution tracking ────────────────────────────────────────────
    pending_tasks: list[dict]       # Tasks waiting to be dispatched
    in_progress_tasks: list[dict]   # Tasks currently running in an agent
    completed_tasks: list[dict]     # Tasks that finished successfully
    failed_tasks: list[dict]        # Tasks that exhausted retries
    current_task: Optional[dict]    # The single task being acted on right now

    # ── Agent outputs ──────────────────────────────────────────────────────
    files_written: list[str]        # Accumulated file paths across all agents
    bug_reports: list[dict]         # Bug reports from QA agent(s)
    review_result: Optional[dict]   # Final output from reviewer agent
    retry_counts: dict[str, int]    # task_id → number of retries attempted

    # ── Memory & context ──────────────────────────────────────────────────
    rag_chunks: list[dict]          # Vector-store results for current task
    error_message: Optional[str]    # Last error string; used for routing decisions

    # ── Metadata ──────────────────────────────────────────────────────────
    created_at: str     # ISO-8601 UTC timestamp of project creation
    updated_at: str     # ISO-8601 UTC timestamp of last state mutation


# ---------------------------------------------------------------------------
# State factory helpers
# ---------------------------------------------------------------------------


def initial_state(
    project_id: str,
    user_id: str,
    original_prompt: str,
) -> PlatformState:
    """Create a fully-initialised PlatformState for a new project.

    All lists default to empty; all optional scalars to None or "".
    Callers should treat this as the canonical way to start a new graph run.

    Args:
        project_id: Unique project identifier (UUID string).
        user_id: Requesting user identifier.
        original_prompt: Raw user requirement text.

    Returns:
        A PlatformState with safe defaults for every field.
    """
    now = datetime.now(timezone.utc).isoformat()
    return PlatformState(
        # Identity
        project_id=project_id,
        user_id=user_id,
        original_prompt=original_prompt,
        project_status=ProjectStatus.CREATED,
        # Planning
        project_summary="",
        task_dag=[],
        plan_feedback="",
        plan_approved=False,
        # Execution tracking
        pending_tasks=[],
        in_progress_tasks=[],
        completed_tasks=[],
        failed_tasks=[],
        current_task=None,
        # Agent outputs
        files_written=[],
        bug_reports=[],
        review_result=None,
        retry_counts={},
        # Memory
        rag_chunks=[],
        error_message=None,
        # Metadata
        created_at=now,
        updated_at=now,
    )


def update_state(state: PlatformState, **updates: Any) -> PlatformState:
    """Return a new state dict with the given fields overwritten.

    LangGraph nodes must return a *new* state rather than mutate in place.
    This helper enforces that pattern and stamps ``updated_at``.

    Args:
        state: Current state.
        **updates: Fields to overwrite.

    Returns:
        Updated state dict (shallow copy with timestamp refreshed).
    """
    # TypedDict supports spread via dict constructor
    new_state = dict(state)
    new_state.update(updates)
    new_state["updated_at"] = datetime.now(timezone.utc).isoformat()
    return PlatformState(**new_state)
