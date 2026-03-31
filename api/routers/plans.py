"""HITL plan endpoints — review and approve or reject the BRD-to-WBS task DAG.

The orchestration graph pauses *before* the ``router`` node (``interrupt_before``
is set in :func:`~orchestrator.graph.build_graph`).  These endpoints let an
operator inspect the plan, then either resume execution or send it back to the
BRD-to-WBS node with corrective feedback.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.middleware.auth import get_current_user, require_role
from api.schemas.models import (
    PlanApprovalRequest,
    PlanApprovalResponse,
    TaskDAGResponse,
    TaskItem,
)
from db.connection import get_db
from db.models import Project
from orchestrator.checkpointer import load_state
from orchestrator.state import initial_state

import uuid

logger = structlog.get_logger()

router = APIRouter(prefix="/projects", tags=["plans"])


# ---------------------------------------------------------------------------
# GET /projects/{id}/plan
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/plan",
    response_model=TaskDAGResponse,
    summary="Return the WBS-generated task plan awaiting human approval",
)
async def get_plan(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> TaskDAGResponse:
    """Return the current task plan from the LangGraph checkpoint.

    The plan is available after the ``brd_to_wbs_node`` has run and the graph
    has paused at the HITL interrupt point (``project_status = AWAITING_APPROVAL``).

    Args:
        project_id: UUID of the project.
        request: Carries ``app.state.checkpointer``.
        db: Async database session.
        current_user: Any authenticated user.

    Returns:
        :class:`~api.schemas.models.TaskDAGResponse` with the full task list,
        skill breakdown, and summary.

    Raises:
        HTTP 404 if the project does not exist.
        HTTP 409 if no plan has been generated yet (graph not at HITL gate).
    """
    await _get_project_or_404(db, project_id)

    graph_state = await _load_graph_state(request, project_id)

    task_dag: list[dict] = graph_state.get("task_dag") or []
    project_summary: str = graph_state.get("project_summary", "")
    plan_approved: bool = bool(graph_state.get("plan_approved", False))
    project_status: str = str(graph_state.get("project_status", "UNKNOWN"))

    if not task_dag:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No plan has been generated yet for this project. "
                "The graph may still be in the PLANNING phase."
            ),
        )

    # Compute skill breakdown
    skill_breakdown: dict[str, int] = {}
    task_items: list[TaskItem] = []

    for task in task_dag:
        skill = str(task.get("skill_required", "unknown"))
        skill_breakdown[skill] = skill_breakdown.get(skill, 0) + 1
        task_items.append(
            TaskItem(
                id=str(task.get("id", "")),
                title=str(task.get("title", "")),
                description=str(task.get("description", "")),
                skill_required=skill,
                acceptance_criteria=task.get("acceptance_criteria") or [],
                depends_on=[str(d) for d in (task.get("depends_on") or [])],
            )
        )

    logger.info(
        "get_plan_ok",
        project_id=project_id,
        task_count=len(task_items),
        status=project_status,
    )

    return TaskDAGResponse(
        project_id=project_id,
        project_summary=project_summary,
        total_tasks=len(task_items),
        skill_breakdown=skill_breakdown,
        tasks=task_items,
        plan_approved=plan_approved,
        status=project_status,
    )


# ---------------------------------------------------------------------------
# POST /projects/{id}/plan/approve
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/plan/approve",
    response_model=PlanApprovalResponse,
    summary="Approve the plan (resume graph) or reject it (trigger a re-plan)",
)
async def approve_plan(
    project_id: str,
    body: PlanApprovalRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_role("admin", "developer")),
) -> PlanApprovalResponse:
    """Approve or reject the generated task plan.

    **Approval flow** (``approved=True``):

    1. Updates the LangGraph state with ``plan_approved=True``.
    2. Resumes the graph from the interrupt point (before ``router``).
    3. Returns ``{ "status": "resumed" }``.

    **Rejection flow** (``approved=False``, ``feedback`` required):

    1. Loads the current checkpoint to recover the ``original_prompt``.
    2. Builds a fresh ``PlatformState`` that includes the operator feedback
       in ``plan_feedback``.
    3. Restarts the graph from ``START`` (passing the new state as input)
       so the BRD-to-WBS node re-generates the DAG incorporating the feedback.
    4. Returns ``{ "status": "replanning" }``.

    Both flows run the graph as a fire-and-forget ``asyncio`` background task.
    The caller should poll ``GET /projects/{id}/status`` to observe progress.

    Args:
        project_id: UUID of the project.
        body: :class:`~api.schemas.models.PlanApprovalRequest`.
        request: Carries ``app.state.graph`` and ``app.state.checkpointer``.
        db: Async database session.
        current_user: Must be ``admin`` or ``developer``.

    Returns:
        :class:`~api.schemas.models.PlanApprovalResponse`.

    Raises:
        HTTP 404 if the project does not exist.
        HTTP 409 if there is no pending plan to approve.
        HTTP 503 if the graph is not available (server not initialised).
    """
    project = await _get_project_or_404(db, project_id)
    graph = _get_graph_or_503(request)
    config = {"configurable": {"thread_id": project_id}}

    if body.approved:
        # ── Resume from HITL interrupt ────────────────────────────────────────
        # Update state: set plan_approved so the graph can validate it if needed.
        try:
            await graph.aupdate_state(
                config=config,
                values={"plan_approved": True},
            )
        except Exception as exc:
            logger.error(
                "approve_plan_update_state_failed",
                project_id=project_id,
                error=str(exc),
            )
            # If state update fails, try to resume without it
            pass

        # ainvoke(None, config) resumes from the interrupt point (before "router")
        asyncio.create_task(
            graph.ainvoke(None, config=config),
            name=f"graph-resume-{project_id}",
        )

        logger.info("plan_approved_resumed", project_id=project_id, user=current_user["id"])

        return PlanApprovalResponse(
            status="resumed",
            message=(
                "Plan approved. Task dispatch is starting — "
                "poll GET /projects/{id}/status for updates."
            ),
        )

    else:
        # ── Reject: restart graph from START with feedback ────────────────────
        # Recover the original prompt from the checkpoint so we can re-plan.
        graph_state = None
        try:
            graph_state = await _load_graph_state(request, project_id)
        except HTTPException:
            pass  # No checkpoint yet — use DB prompt

        original_prompt: str = (
            graph_state.get("original_prompt", "") if graph_state else ""
        ) or project.prompt

        feedback: str = body.feedback or ""

        # Build a fresh initial state that carries the feedback
        replan_state = initial_state(
            project_id=project_id,
            user_id=current_user["id"],
            original_prompt=original_prompt,
        )
        replan_state["plan_feedback"] = feedback
        replan_state["plan_approved"] = False

        # Passing a non-None dict to ainvoke restarts the graph from START.
        # Using the same thread_id means the checkpoint is overwritten.
        asyncio.create_task(
            graph.ainvoke(replan_state, config=config),
            name=f"graph-replan-{project_id}",
        )

        logger.info(
            "plan_rejected_replanning",
            project_id=project_id,
            user=current_user["id"],
            feedback_len=len(feedback),
        )

        return PlanApprovalResponse(
            status="replanning",
            message=(
                f"Plan rejected. Re-running the planner with your feedback. "
                f"Poll GET /projects/{project_id}/plan once the new plan is ready."
            ),
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _get_project_or_404(db: AsyncSession, project_id: str) -> Project:
    """Load a Project by UUID or raise HTTP 404.

    Args:
        db: Async database session.
        project_id: UUID string of the project.

    Returns:
        The ORM :class:`~db.models.Project` instance.
    """
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{project_id}' is not a valid project UUID.",
        )
    result = await db.execute(select(Project).where(Project.id == pid))
    project: Project | None = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )
    return project


async def _load_graph_state(request: Request, project_id: str) -> dict:
    """Load the LangGraph checkpoint for a project.

    Args:
        request: FastAPI request carrying ``app.state.checkpointer``.
        project_id: Project thread ID.

    Returns:
        PlatformState dict (may be empty if no checkpoint yet).

    Raises:
        HTTP 503 if the checkpointer is not available.
        HTTP 409 if no checkpoint is found for this project.
    """
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Graph checkpointer is not initialised.",
        )
    try:
        state = await load_state(checkpointer, project_id)
    except Exception as exc:
        logger.error("load_graph_state_error", project_id=project_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not load graph state from checkpointer.",
        )
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"No checkpoint found for project '{project_id}'. "
                "The graph may not have started yet."
            ),
        )
    return state


def _get_graph_or_503(request: Request) -> Any:
    """Return the compiled graph or raise HTTP 503.

    Args:
        request: FastAPI request carrying ``app.state.graph``.

    Returns:
        The compiled LangGraph :class:`~langgraph.graph.state.CompiledStateGraph`.

    Raises:
        HTTP 503 if the graph is not available.
    """
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Orchestration graph is not initialised. Try again in a moment.",
        )
    return graph
