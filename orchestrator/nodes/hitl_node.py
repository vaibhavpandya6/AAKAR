"""HITL node — formats the plan for human review and gates execution.

This node itself only prepares the human-readable plan summary and
updates the project status in PostgreSQL.  The actual execution
pause is achieved by declaring ``interrupt_before=["router_node"]``
in the LangGraph graph definition — LangGraph suspends the graph
before router_node runs, giving the operator time to approve or
reject the plan via the API.
"""

import uuid
from typing import Any

import structlog
from sqlalchemy import update

from db.connection import db_manager
from db.models import Project
from db.models import ProjectStatus as DBProjectStatus
from orchestrator.state import PlatformState, ProjectStatus

logger = structlog.get_logger()


async def hitl_node(state: PlatformState) -> dict[str, Any]:
    """Format the plan for human review and mark the project as awaiting approval.

    Converts ``task_dag`` into a human-readable summary structure, persists
    the ``AWAITING_APPROVAL`` status to PostgreSQL, and returns the updated
    status so the graph can gate on it.

    The actual graph suspension is controlled by ``interrupt_before`` in the
    graph definition — NOT by this node's return value.

    Args:
        state: Current PlatformState.

    Returns:
        Partial state dict with ``project_status = AWAITING_APPROVAL``.
    """
    project_id = state.get("project_id", "")
    task_dag: list[dict] = state.get("task_dag") or []
    project_summary: str = state.get("project_summary", "")

    logger.info(
        "hitl_node_start",
        project_id=project_id,
        task_count=len(task_dag),
    )

    # ── Build human-readable plan summary ─────────────────────────────────────
    plan_summary = _format_plan_summary(project_id, project_summary, task_dag)

    logger.info(
        "hitl_plan_ready",
        project_id=project_id,
        task_count=len(task_dag),
        skill_breakdown=plan_summary["skill_breakdown"],
    )

    # ── Update PostgreSQL project status ──────────────────────────────────────
    await _update_project_status_in_db(project_id, DBProjectStatus.AWAITING_APPROVAL)

    return {
        "project_status": ProjectStatus.AWAITING_APPROVAL,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_plan_summary(
    project_id: str,
    project_summary: str,
    task_dag: list[dict],
) -> dict[str, Any]:
    """Build a structured, human-readable plan summary.

    Args:
        project_id: Project identifier.
        project_summary: High-level description from the planner.
        task_dag: Full validated task list.

    Returns:
        Dict with ``project_id``, ``project_summary``, ``total_tasks``,
        ``skill_breakdown``, and ``tasks`` (simplified list).
    """
    skill_breakdown: dict[str, int] = {}
    simplified_tasks: list[dict] = []

    for task in task_dag:
        skill = str(task.get("skill_required", "unknown"))
        skill_breakdown[skill] = skill_breakdown.get(skill, 0) + 1

        simplified_tasks.append(
            {
                "id": task.get("id"),
                "title": task.get("title"),
                "skill_required": skill,
                "depends_on": task.get("depends_on") or [],
                "acceptance_criteria": task.get("acceptance_criteria") or [],
            }
        )

    return {
        "project_id": project_id,
        "project_summary": project_summary,
        "total_tasks": len(task_dag),
        "skill_breakdown": skill_breakdown,
        "tasks": simplified_tasks,
    }


async def _update_project_status_in_db(
    project_id: str,
    status: DBProjectStatus,
) -> None:
    """Persist project status change to PostgreSQL.

    Silently logs and continues on DB error so the graph is not hard-blocked
    by a transient database issue during the HITL gate.

    Args:
        project_id: UUID string of the project.
        status: New status to set.
    """
    try:
        async for session in db_manager.get_session():
            await session.execute(
                update(Project)
                .where(Project.id == uuid.UUID(project_id))
                .values(status=status)
            )
            await session.commit()
            break

        logger.info(
            "hitl_db_status_updated",
            project_id=project_id,
            status=status.value,
        )
    except Exception as exc:
        logger.error(
            "hitl_db_update_failed",
            project_id=project_id,
            status=status.value,
            error=str(exc),
        )
