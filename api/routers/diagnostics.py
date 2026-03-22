"""Diagnostics and debugging endpoints for development.

/diagnostics/llm — Test LLM connectivity and JSON generation
/diagnostics/redis — Check Redis streams and consumer groups
/diagnostics/database — Verify database connectivity and models
/diagnostics/graph — Inspect LangGraph state and checkpoints
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.middleware.auth import get_current_user
from config import create_json_mode_llm
from db.connection import get_db
from db.models import AgentLog, Project
from langchain_core.messages import HumanMessage, SystemMessage
from messaging.message_bus import get_message_bus
from orchestrator.checkpointer import load_state

logger = structlog.get_logger()

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.get(
    "/llm",
    summary="Test LLM connectivity and JSON generation",
)
async def test_llm(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Test that the LLM (Groq) can be invoked and generates valid JSON.

    Returns:
        Test results including model info, response time, and sample output.
    """
    try:
        llm = create_json_mode_llm()

        system = "You are a helpful assistant. Respond with valid JSON only."
        user = 'Generate a simple JSON object with fields: {"test": "success", "timestamp": "<current time>"}'

        import time
        start = time.monotonic()

        response = await llm.ainvoke([
            SystemMessage(content=system),
            HumanMessage(content=user),
        ])

        duration_ms = int((time.monotonic() - start) * 1000)
        raw_content = response.content

        # Try parsing as JSON
        try:
            parsed = json.loads(raw_content)
            json_valid = True
        except json.JSONDecodeError as exc:
            parsed = None
            json_valid = False
            error_msg = str(exc)

        return {
            "status": "ok" if json_valid else "json_parse_failed",
            "llm_model": llm.model_name,
            "duration_ms": duration_ms,
            "json_valid": json_valid,
            "raw_content": raw_content[:500],
            "parsed_content": parsed if json_valid else None,
            "error": error_msg if not json_valid else None,
        }

    except Exception as exc:
        logger.error("test_llm_failed", error=str(exc))
        return {
            "status": "error",
            "error": str(exc),
        }


@router.get(
    "/redis",
    summary="Check Redis streams and consumer groups",
)
async def test_redis(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Verify Redis connectivity and inspect stream/consumer group state.

    Returns:
        Redis connection status and stream information.
    """
    try:
        message_bus = await get_message_bus()

        # Check orchestrator stream
        orchestrator_stream = "stream:orchestrator"
        stream_info = await message_bus.redis.xinfo_stream(orchestrator_stream)

        # Check agent streams
        agent_streams = [
            "stream:backend_agent",
            "stream:frontend_agent",
            "stream:database_agent",
            "stream:qa_agent",
        ]

        stream_stats = {}
        for stream in agent_streams:
            try:
                info = await message_bus.redis.xinfo_stream(stream)
                stream_stats[stream] = {
                    "length": info.get("length", 0),
                    "first_entry": info.get("first-entry"),
                    "last_entry": info.get("last-entry"),
                }
            except Exception as exc:
                stream_stats[stream] = {"error": str(exc)}

        return {
            "status": "ok",
            "orchestrator_stream": {
                "name": orchestrator_stream,
                "length": stream_info.get("length", 0),
            },
            "agent_streams": stream_stats,
        }

    except Exception as exc:
        logger.error("test_redis_failed", error=str(exc))
        return {
            "status": "error",
            "error": str(exc),
        }


@router.get(
    "/database",
    summary="Verify database connectivity and check tables",
)
async def test_database(
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Test database connection and query basic statistics.

    Returns:
        Database connection status and table counts.
    """
    try:
        # Test basic query
        result = await db.execute(text("SELECT 1 as test"))
        test_value = result.scalar()

        # Count projects
        project_count = await db.execute(select(Project))
        projects = len(project_count.scalars().all())

        # Count agent logs
        log_count = await db.execute(select(AgentLog))
        logs = len(log_count.scalars().all())

        return {
            "status": "ok",
            "connection": "active",
            "test_query": test_value,
            "project_count": projects,
            "agent_log_count": logs,
        }

    except Exception as exc:
        logger.error("test_database_failed", error=str(exc))
        return {
            "status": "error",
            "error": str(exc),
        }


@router.get(
    "/graph/{project_id}",
    summary="Inspect LangGraph state for a project",
)
async def inspect_graph_state(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the raw LangGraph checkpoint state for debugging.

    Args:
        project_id: UUID of the project.
        request: Carries app.state.checkpointer.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Complete graph state or error information.
    """
    # Verify project exists
    try:
        result = await db.execute(
            select(Project).where(Project.id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project '{project_id}' not found",
            )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{project_id}' is not a valid UUID",
        )

    # Load graph state
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        return {
            "status": "error",
            "error": "Checkpointer not available on app.state",
        }

    try:
        graph_state = await load_state(checkpointer, project_id)

        if not graph_state:
            return {
                "status": "no_checkpoint",
                "message": "No graph checkpoint found for this project",
            }

        return {
            "status": "ok",
            "project_id": project_id,
            "graph_state": graph_state,
        }

    except Exception as exc:
        logger.error(
            "inspect_graph_state_failed",
            project_id=project_id,
            error=str(exc),
        )
        return {
            "status": "error",
            "error": str(exc),
        }


@router.get(
    "/logs/test",
    summary="Test agent log query to diagnose log access errors",
)
async def test_log_query(
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Test querying agent logs to identify any database or query issues.

    Returns:
        Sample logs and any errors encountered.
    """
    try:
        # Simple query - get recent logs
        result = await db.execute(
            select(AgentLog)
            .order_by(AgentLog.timestamp.desc())
            .limit(10)
        )
        logs = result.scalars().all()

        return {
            "status": "ok",
            "log_count": len(logs),
            "sample_logs": [
                {
                    "id": str(log.id),
                    "agent": log.agent,
                    "action": log.action,
                    "status": log.status,
                    "timestamp": log.timestamp.isoformat(),
                }
                for log in logs
            ],
        }

    except Exception as exc:
        logger.error("test_log_query_failed", error=str(exc))
        return {
            "status": "error",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
