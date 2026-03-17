"""Reviewer node — calls the ReviewerAgent inline and returns the review decision.

Instantiates all agent dependencies from their singletons, creates a
synthetic "review" task covering all project files, dispatches to
ReviewerAgent, and maps the approved / rejected outcome back to state.
"""

from typing import Any

import structlog
from langchain_openai import ChatOpenAI

from agents.reviewer_agent.agent import ReviewerAgent
from memory.long_term import get_long_term_memory
from memory.vector_store import get_vector_store
from messaging.message_bus import get_message_bus
from orchestrator.state import PlatformState, ProjectStatus
from workspace_manager.manager import get_workspace_manager

logger = structlog.get_logger()

_REVIEWER_MODEL = "gpt-4o"
_REVIEWER_AGENT_NAME = "reviewer-agent"


async def reviewer_node(state: PlatformState) -> dict[str, Any]:
    """Run the ReviewerAgent against the full project codebase.

    Collects the list of files written throughout the project, builds a
    synthetic review task, and delegates to :class:`~agents.reviewer_agent.agent.ReviewerAgent`.
    Maps the ``approved`` flag to the appropriate ``ProjectStatus``.

    Args:
        state: Current PlatformState.

    Returns:
        Partial state dict with ``review_result`` and ``project_status``.
        Approved → ``DELIVERED``; rejected → ``FAILED`` with ``error_message``.
    """
    project_id = state.get("project_id", "")
    files_written: list[str] = state.get("files_written") or []
    completed_tasks: list[dict] = state.get("completed_tasks") or []

    logger.info(
        "reviewer_node_start",
        project_id=project_id,
        files_written_count=len(files_written),
        completed_task_count=len(completed_tasks),
    )

    # ── Build synthetic review task ──────────────────────────────────────────
    review_task: dict[str, Any] = {
        "id": "review_final",
        "title": "Final code review",
        "description": (
            "Perform a comprehensive security, performance, and correctness review "
            "of the entire project codebase.  Evaluate all implemented tasks:\n\n"
            + "\n".join(
                f"- [{t.get('id')}] {t.get('title', '')}"
                for t in completed_tasks
            )
        ),
        "skill_required": "reviewer",
        "acceptance_criteria": [
            "No high or critical security vulnerabilities.",
            "All major performance concerns are noted.",
            "Code consistency is acceptable across agents.",
            "No hardcoded secrets or credentials.",
        ],
        "files_to_review": files_written[:50],
    }

    # ── Instantiate ReviewerAgent with singletons ────────────────────────────
    try:
        llm = ChatOpenAI(model=_REVIEWER_MODEL, temperature=0)
        vector_store = get_vector_store()
        long_term_memory = get_long_term_memory()
        message_bus = await get_message_bus()
        workspace_manager = get_workspace_manager()

        reviewer = ReviewerAgent(
            agent_name=_REVIEWER_AGENT_NAME,
            llm=llm,
            vector_store=vector_store,
            long_term_memory=long_term_memory,
            message_bus=message_bus,
            workspace_manager=workspace_manager,
        )
    except Exception as exc:
        logger.error(
            "reviewer_node_agent_init_failed",
            project_id=project_id,
            error=str(exc),
        )
        return {
            "error_message": f"Reviewer agent init failed: {exc}",
            "project_status": ProjectStatus.FAILED,
        }

    # ── Execute review ────────────────────────────────────────────────────────
    try:
        result: dict[str, Any] = await reviewer.execute(review_task, project_id)
    except Exception as exc:
        logger.error(
            "reviewer_node_execute_failed",
            project_id=project_id,
            error=str(exc),
        )
        return {
            "error_message": f"Reviewer agent execution failed: {exc}",
            "project_status": ProjectStatus.FAILED,
        }

    # ── Map outcome to state ─────────────────────────────────────────────────
    approved: bool = result.get("approved", False)
    issues: list = result.get("issues", [])
    summary: str = result.get("summary", "")

    logger.info(
        "reviewer_node_complete",
        project_id=project_id,
        approved=approved,
        issue_count=len(issues),
    )

    if approved:
        return {
            "review_result": result,
            "project_status": ProjectStatus.DELIVERED,
            "error_message": None,
        }

    # Build a concise rejection summary for downstream routing
    high_issues = [i for i in issues if i.get("severity") in ("critical", "high")]
    rejection_summary = (
        f"Review rejected: {len(high_issues)} high/critical issue(s). {summary}"
    )

    return {
        "review_result": result,
        "error_message": rejection_summary,
        "project_status": ProjectStatus.FAILED,
    }
