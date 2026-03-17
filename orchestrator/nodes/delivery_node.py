"""Delivery node — merges all agent branches, creates the release branch and tag,
and marks the project as DELIVERED in PostgreSQL.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import update

from db.connection import db_manager
from db.models import Project
from db.models import ProjectStatus as DBProjectStatus
from orchestrator.state import PlatformState, ProjectStatus
from workspace_manager.git_manager import GitManager, GitError, MergeConflictError

logger = structlog.get_logger()

# Branch naming prefix that all agents use when creating task branches
_AGENT_BRANCH_PREFIX = "agent/"


async def delivery_node(state: PlatformState) -> dict[str, Any]:
    """Finalise the project by merging branches, tagging, and recording delivery.

    Steps:
    1. Discover all ``agent/*/task-*`` branches in the project workspace.
    2. Merge each branch into ``main`` via :class:`~workspace_manager.git_manager.GitManager`.
       Merge conflicts are logged as warnings and skipped (not fatal).
    3. Create a ``release/{project_id}-{timestamp}`` branch from ``main``.
    4. Create an annotated ``delivered-{timestamp}`` tag on ``main``.
    5. Persist ``DELIVERED`` status to PostgreSQL.

    Args:
        state: Current PlatformState.

    Returns:
        Partial state dict with ``project_status = DELIVERED``.
    """
    project_id = state.get("project_id", "")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    logger.info("delivery_node_start", project_id=project_id)

    git_manager = GitManager()

    # ── 1. Discover agent branches ────────────────────────────────────────────
    agent_branches = await asyncio.to_thread(_list_agent_branches, git_manager, project_id)

    logger.info(
        "delivery_branches_found",
        project_id=project_id,
        branch_count=len(agent_branches),
        branches=agent_branches,
    )

    # ── 2. Merge agent branches into main ─────────────────────────────────────
    merged: list[str] = []
    skipped: list[str] = []

    for branch in agent_branches:
        try:
            await asyncio.to_thread(git_manager.merge_to_main, project_id, branch)
            merged.append(branch)
            logger.info(
                "delivery_branch_merged",
                project_id=project_id,
                branch=branch,
            )
        except MergeConflictError as exc:
            skipped.append(branch)
            logger.warning(
                "delivery_merge_conflict_skipped",
                project_id=project_id,
                branch=branch,
                conflict=str(exc),
            )
        except GitError as exc:
            skipped.append(branch)
            logger.warning(
                "delivery_merge_error_skipped",
                project_id=project_id,
                branch=branch,
                error=str(exc),
            )

    # ── 3. Create release branch ──────────────────────────────────────────────
    release_branch: str = ""
    try:
        release_branch = await asyncio.to_thread(
            git_manager.create_release_branch, project_id
        )
        logger.info(
            "delivery_release_branch_created",
            project_id=project_id,
            branch=release_branch,
        )
    except GitError as exc:
        logger.warning(
            "delivery_release_branch_failed",
            project_id=project_id,
            error=str(exc),
        )

    # ── 4. Tag the delivery ───────────────────────────────────────────────────
    tag_name = f"delivered-{timestamp}"
    try:
        await asyncio.to_thread(
            git_manager.tag,
            project_id,
            tag_name,
            f"Delivered project {project_id} at {timestamp}",
        )
        logger.info(
            "delivery_tag_created",
            project_id=project_id,
            tag=tag_name,
        )
    except GitError as exc:
        logger.warning(
            "delivery_tag_failed",
            project_id=project_id,
            tag=tag_name,
            error=str(exc),
        )

    # ── 5. Persist status to PostgreSQL ──────────────────────────────────────
    await _update_project_status_in_db(project_id, DBProjectStatus.DELIVERED)

    # ── Log delivery summary ──────────────────────────────────────────────────
    logger.info(
        "delivery_node_complete",
        project_id=project_id,
        merged_branches=merged,
        skipped_branches=skipped,
        release_branch=release_branch,
        delivery_tag=tag_name,
        merged_count=len(merged),
        skipped_count=len(skipped),
    )

    return {
        "project_status": ProjectStatus.DELIVERED,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _list_agent_branches(git_manager: GitManager, project_id: str) -> list[str]:
    """Return all local Git branches whose names start with ``agent/``.

    Runs synchronously (called via :func:`asyncio.to_thread`).

    Args:
        git_manager: Configured GitManager instance.
        project_id: Project identifier.

    Returns:
        Sorted list of branch names matching ``agent/*/task-*``.
    """
    try:
        # Access the raw GitPython Repo via the private helper
        repo = git_manager._get_repo(project_id)
        return sorted(
            head.name
            for head in repo.heads
            if head.name.startswith(_AGENT_BRANCH_PREFIX)
        )
    except Exception as exc:
        logger.warning(
            "delivery_list_branches_failed",
            project_id=project_id,
            error=str(exc),
        )
        return []


async def _update_project_status_in_db(
    project_id: str,
    status: DBProjectStatus,
) -> None:
    """Persist project status change to PostgreSQL.

    Silently logs on error so a transient DB issue does not block delivery.

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
            "delivery_db_status_updated",
            project_id=project_id,
            status=status.value,
        )
    except Exception as exc:
        logger.error(
            "delivery_db_update_failed",
            project_id=project_id,
            status=status.value,
            error=str(exc),
        )
