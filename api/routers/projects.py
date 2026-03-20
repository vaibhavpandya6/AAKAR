"""Projects router — project lifecycle, file access, and rollback endpoints."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.middleware.auth import get_current_user, require_role
from api.schemas.models import (
    CreateProjectRequest,
    FileContentResponse,
    FileListResponse,
    LogEntry,
    LogsResponse,
    ProjectResponse,
    ProjectStatusResponse,
    RollbackRequest,
    RollbackResponse,
)
from db.connection import get_db
from db.models import AgentLog, Project
from memory.vector_store import VectorStore
from db.models import ProjectStatus as DBProjectStatus
from orchestrator.checkpointer import load_state
from orchestrator.state import initial_state
from workspace_manager.git_manager import GitError, GitManager
from workspace_manager.manager import get_workspace_manager
from security.prompt_guard import sanitize_user_input

logger = structlog.get_logger()

router = APIRouter(prefix="/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# POST /projects/create
# ---------------------------------------------------------------------------


@router.post(
    "/create",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project and start the orchestrator graph",
)
async def create_project(
    body: CreateProjectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_role("admin", "developer")),
) -> ProjectResponse:
    """Create a new project and kick off the LangGraph orchestration pipeline.

    Steps:

    1. Sanitize the user prompt (defensive double-check after middleware).
    2. Persist a ``Project`` row with status ``CREATED``.
    3. Initialise the project workspace directory on disk.
    4. Build the :class:`~orchestrator.state.PlatformState` and start the
       LangGraph graph as a background ``asyncio`` task.

    The graph runs asynchronously; the project status advances through
    ``PLANNING → AWAITING_APPROVAL → IN_PROGRESS → …`` as the agents work.
    Poll ``GET /projects/{id}/status`` to follow progress.

    Args:
        body: Validated :class:`~api.schemas.models.CreateProjectRequest`.
        request: Starlette request (carries ``app.state.graph``).
        db: Async database session.
        current_user: Decoded JWT claims; must be ``admin`` or ``developer``.

    Returns:
        :class:`~api.schemas.models.ProjectResponse` with the new project ID.
    """
    prompt = sanitize_user_input(body.prompt)
    project_id = uuid.uuid4()
    user_id = current_user["id"]

    # ── 1. Persist project row ──────────────────────────────────────────────
    project = Project(
        id=project_id,
        user_id=uuid.UUID(user_id),
        prompt=prompt,
        status=DBProjectStatus.CREATED,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    # ── 2. Initialise workspace ─────────────────────────────────────────────
    try:
        workspace_manager = get_workspace_manager()
        await workspace_manager.create_workspace(str(project_id))
    except Exception as exc:
        logger.error("create_project_workspace_failed", project_id=str(project_id), error=str(exc))
        # Non-fatal at this stage — the graph will create it if missing

    # ── 2b. Warm RAG index before graph starts ─────────────────────────────
    try:
        indexed_chunks = await VectorStore().index_workspace(str(project_id))
        logger.info(
            "create_project_rag_warmup_complete",
            project_id=str(project_id),
            indexed_chunks=indexed_chunks,
        )
    except Exception as exc:
        logger.warning(
            "create_project_rag_warmup_failed",
            project_id=str(project_id),
            error=str(exc),
        )

    # ── 3. Start graph as background task ───────────────────────────────────
    graph = getattr(request.app.state, "graph", None)
    if graph is not None:
        run_state = initial_state(
            project_id=str(project_id),
            user_id=user_id,
            original_prompt=prompt,
        )
        config = {"configurable": {"thread_id": str(project_id)}}
        asyncio.create_task(
            graph.ainvoke(run_state, config=config),
            name=f"graph-{project_id}",
        )
        logger.info("create_project_graph_started", project_id=str(project_id))
    else:
        logger.warning("create_project_no_graph", project_id=str(project_id))

    logger.info(
        "create_project_ok",
        project_id=str(project_id),
        user_id=user_id,
    )

    return ProjectResponse(
        id=str(project.id),
        status=project.status.value,
        prompt=project.prompt,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


# ---------------------------------------------------------------------------
# GET /projects/{id}/status
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/status",
    response_model=ProjectStatusResponse,
    summary="Get the current execution status of a project",
)
async def get_project_status(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> ProjectStatusResponse:
    """Return the live execution status of a project.

    Combines data from two sources:

    - **PostgreSQL** ``projects`` table — canonical project status.
    - **LangGraph checkpoint** — rich orchestration state (task lists,
      bug reports, files written, error message).

    The checkpoint may not exist yet if the graph has not started.  In that
    case only the DB status is returned.

    Args:
        project_id: UUID of the project.
        request: Carries ``app.state.checkpointer``.
        db: Async database session.
        current_user: Authenticated user (any role).

    Returns:
        :class:`~api.schemas.models.ProjectStatusResponse`.
    """
    # Verify project exists in DB
    project = await _get_project_or_404(db, project_id)

    # Try to enrich with graph checkpoint
    checkpointer = getattr(request.app.state, "checkpointer", None)
    graph_state = None
    if checkpointer is not None:
        try:
            graph_state = await load_state(checkpointer, project_id)
        except Exception as exc:
            logger.warning(
                "get_status_checkpoint_failed",
                project_id=project_id,
                error=str(exc),
            )

    if graph_state:
        return ProjectStatusResponse(
            project_id=project_id,
            status=graph_state.get("project_status", project.status.value),
            project_summary=graph_state.get("project_summary", ""),
            pending_tasks=graph_state.get("pending_tasks") or [],
            in_progress_tasks=graph_state.get("in_progress_tasks") or [],
            completed_tasks=graph_state.get("completed_tasks") or [],
            failed_tasks=graph_state.get("failed_tasks") or [],
            files_written=graph_state.get("files_written") or [],
            bug_reports=graph_state.get("bug_reports") or [],
            error_message=graph_state.get("error_message"),
            updated_at=graph_state.get("updated_at"),
        )

    return ProjectStatusResponse(
        project_id=project_id,
        status=project.status.value,
        updated_at=project.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /projects/{id}/logs
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/logs",
    response_model=LogsResponse,
    summary="List agent execution logs for a project",
)
async def get_project_logs(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
    # Filters
    agent: Optional[str] = Query(default=None, description="Filter by agent name"),
    action: Optional[str] = Query(default=None, description="Filter by log action"),
    log_status: Optional[str] = Query(
        default=None, alias="status", description="Filter by log status"
    ),
    from_time: Optional[datetime] = Query(
        default=None, description="Earliest timestamp (ISO-8601)"
    ),
    to_time: Optional[datetime] = Query(
        default=None, description="Latest timestamp (ISO-8601)"
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> LogsResponse:
    """Return agent execution logs for the given project.

    All filter parameters are optional and combinable.  Results are ordered
    by timestamp descending (newest first).

    Args:
        project_id: UUID of the project.
        db: Async database session.
        current_user: Authenticated user.
        agent: Optional exact-match filter on agent name.
        action: Optional exact-match filter on action field.
        log_status: Optional exact-match filter on status field.
        from_time: Optional lower bound on timestamp.
        to_time: Optional upper bound on timestamp.
        limit: Maximum records to return (1–500, default 100).
        offset: Pagination offset.

    Returns:
        :class:`~api.schemas.models.LogsResponse`.
    """
    await _get_project_or_404(db, project_id)

    query = (
        select(AgentLog)
        .where(AgentLog.project_id == uuid.UUID(project_id))
        .order_by(AgentLog.timestamp.desc())
    )
    if agent:
        query = query.where(AgentLog.agent == agent)
    if action:
        query = query.where(AgentLog.action == action)
    if log_status:
        query = query.where(AgentLog.status == log_status)
    if from_time:
        ft = from_time.replace(tzinfo=timezone.utc) if from_time.tzinfo is None else from_time
        query = query.where(AgentLog.timestamp >= ft)
    if to_time:
        tt = to_time.replace(tzinfo=timezone.utc) if to_time.tzinfo is None else to_time
        query = query.where(AgentLog.timestamp <= tt)

    # Total count (without pagination)
    count_result = await db.execute(query)
    all_rows = count_result.scalars().all()
    total = len(all_rows)

    # Paginated rows
    paginated = await db.execute(query.offset(offset).limit(limit))
    rows = paginated.scalars().all()

    entries = [
        LogEntry(
            id=str(row.id),
            project_id=str(row.project_id),
            task_id=str(row.task_id) if row.task_id else None,
            agent=row.agent,
            action=row.action,
            file_path=row.file_path,
            status=row.status,
            duration_ms=row.duration_ms,
            metadata=row.log_metadata,
            timestamp=row.timestamp,
        )
        for row in rows
    ]

    return LogsResponse(project_id=project_id, total=total, entries=entries)


# ---------------------------------------------------------------------------
# GET /projects/{id}/files
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/files",
    response_model=FileListResponse,
    summary="List all files in the project workspace",
)
async def list_project_files(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> FileListResponse:
    """Return all file paths from the project workspace manifest.

    The manifest is maintained by the workspace manager and updated atomically
    after every agent file write.

    Args:
        project_id: UUID of the project.
        db: Async database session.
        current_user: Authenticated user.

    Returns:
        :class:`~api.schemas.models.FileListResponse`.
    """
    await _get_project_or_404(db, project_id)

    try:
        workspace_manager = get_workspace_manager()
        manifest = await workspace_manager.get_manifest(project_id)
        files = sorted(manifest.get("files", {}).keys())
    except Exception as exc:
        logger.warning(
            "list_files_manifest_error", project_id=project_id, error=str(exc)
        )
        files = []

    return FileListResponse(
        project_id=project_id,
        total_files=len(files),
        files=files,
    )


# ---------------------------------------------------------------------------
# GET /projects/{id}/files/{path}
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/files/{file_path:path}",
    response_model=FileContentResponse,
    summary="Return the content of a single workspace file",
)
async def get_file_content(
    project_id: str,
    file_path: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> FileContentResponse:
    """Return the raw content of one workspace file.

    Args:
        project_id: UUID of the project.
        file_path: Relative path within the workspace (e.g. ``src/main.py``).
                   FastAPI captures the full path including any forward slashes.
        db: Async database session.
        current_user: Authenticated user.

    Returns:
        :class:`~api.schemas.models.FileContentResponse`.

    Raises:
        HTTP 404 if the project does not exist or the file is not found.
    """
    await _get_project_or_404(db, project_id)

    try:
        workspace_manager = get_workspace_manager()
        content: str = await workspace_manager.read_file(project_id, file_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {file_path}",
        )
    except Exception as exc:
        logger.error(
            "get_file_content_error",
            project_id=project_id,
            file_path=file_path,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not read file.",
        )

    return FileContentResponse(
        project_id=project_id,
        file_path=file_path,
        content=content,
        size_bytes=len(content.encode("utf-8")),
    )


# ---------------------------------------------------------------------------
# POST /projects/{id}/rollback
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/rollback",
    response_model=RollbackResponse,
    summary="Roll back the project workspace to a Git tag",
)
async def rollback_project(
    project_id: str,
    body: RollbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_role("admin", "developer")),
) -> RollbackResponse:
    """Roll back the project workspace to a named Git tag.

    Delegates to :meth:`~workspace_manager.git_manager.GitManager.rollback_to_tag`.
    GitManager is synchronous, so the call is wrapped in
    :func:`asyncio.to_thread` to avoid blocking the event loop.

    Args:
        project_id: UUID of the project.
        body: :class:`~api.schemas.models.RollbackRequest` with the target tag.
        db: Async database session.
        current_user: Must be ``admin`` or ``developer``.

    Returns:
        :class:`~api.schemas.models.RollbackResponse`.

    Raises:
        HTTP 404 if the project does not exist.
        HTTP 422 if the tag does not exist in the repository.
    """
    await _get_project_or_404(db, project_id)

    git_manager = GitManager()
    try:
        await asyncio.to_thread(
            git_manager.rollback_to_tag,
            project_id,
            body.tag,
        )
    except GitError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Rollback failed: {exc}",
        )
    except Exception as exc:
        logger.error(
            "rollback_error",
            project_id=project_id,
            tag=body.tag,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Rollback encountered an unexpected error.",
        )

    logger.info(
        "project_rolled_back",
        project_id=project_id,
        tag=body.tag,
        user_id=current_user["id"],
    )

    return RollbackResponse(
        project_id=project_id,
        tag=body.tag,
        message=f"Workspace successfully rolled back to tag '{body.tag}'.",
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

    Raises:
        :class:`~fastapi.HTTPException` 400 if ``project_id`` is not a valid UUID.
        :class:`~fastapi.HTTPException` 404 if the project does not exist.
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
