"""Centralised structured logging for ai-dev-platform.

Provides:
- ``configure_logging()``  — one-shot structlog/stdlib setup; call from lifespan.
- ``get_logger(name)``     — returns a bound structlog logger.
- ``log_agent_action(...)``— structured log + async DB insert into ``agent_logs``.
- ``AgentActionContext``   — async context manager that auto-times and logs an action.

Request-ID propagation
~~~~~~~~~~~~~~~~~~~~~~
Each async task can carry a ``request_id`` by:

1. Setting the Python ContextVar:   ``request_id_var.set("uuid")``
2. OR using structlog's context:    ``structlog.contextvars.bind_contextvars(request_id="uuid")``

Both are automatically merged into every log record produced in that context.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from structlog.processors import CallsiteParameter, CallsiteParameterAdder

from config import settings

# ---------------------------------------------------------------------------
# Public ContextVar — importable by middleware / route handlers
# ---------------------------------------------------------------------------

#: Set this in every async task / request scope to propagate a request ID.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _inject_request_id(
    logger: Any,
    method_name: str,
    event_dict: dict,
) -> dict:
    """Structlog processor: inject ``request_id`` from the ContextVar."""
    rid = request_id_var.get(None)
    if rid:
        event_dict.setdefault("request_id", rid)
    return event_dict


_SHARED_PROCESSORS: list = [
    # Merge any keys bound via structlog.contextvars.bind_contextvars(...)
    structlog.contextvars.merge_contextvars,
    # Pull request_id from our own ContextVar
    _inject_request_id,
    # Standard metadata
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    # Caller location — filename, function, line number
    CallsiteParameterAdder(
        [
            CallsiteParameter.FILENAME,
            CallsiteParameter.FUNC_NAME,
            CallsiteParameter.LINENO,
        ]
    ),
    structlog.processors.StackInfoRenderer(),
]

_is_configured = False


def configure_logging() -> None:
    """Configure structlog and the stdlib root logger.

    Idempotent — safe to call multiple times; only acts on the first call.

    Renderer selection:
    - ``ENVIRONMENT=production``  → :class:`structlog.processors.JSONRenderer`
    - anything else               → :class:`structlog.dev.ConsoleRenderer`
    """
    global _is_configured
    if _is_configured:
        return

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        force=True,
    )
    # Suppress noisy third-party loggers
    for noisy in ("urllib3", "httpcore", "httpx", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    renderer = (
        structlog.processors.JSONRenderer()
        if settings.environment == "production"
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.processors.ExceptionRenderer(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _is_configured = True


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a structlog logger pre-bound with ``logger_name=name``.

    Args:
        name: Logical name for the component (e.g. ``"orchestrator.planner"``).

    Returns:
        A bound :class:`structlog.BoundLogger`.
    """
    return structlog.get_logger().bind(logger_name=name)


# ---------------------------------------------------------------------------
# Agent-action logging — structured log + DB insert
# ---------------------------------------------------------------------------


async def log_agent_action(
    *,
    agent: str,
    action: str,
    project_id: str,
    task_id: Optional[str] = None,
    file_path: Optional[str] = None,
    status: str,
    duration_ms: int,
    metadata: Optional[dict] = None,
) -> None:
    """Log an agent action to structlog **and** insert a row into ``agent_logs``.

    The DB insert is fire-and-forget: any persistence failure is logged as a
    warning and swallowed so it never disrupts the calling agent.

    Args:
        agent: Agent name (e.g. ``"backend_agent"``).
        action: Action performed (e.g. ``"write_file"``).
        project_id: UUID string of the owning project.
        task_id: UUID string of the related task (optional).
        file_path: Workspace-relative file path affected (optional).
        status: Outcome string — ``"success"``, ``"error"``, etc.
        duration_ms: Wall-clock duration in milliseconds.
        metadata: Arbitrary JSON-serialisable extra context (optional).
    """
    _log = get_logger("observability.logger")

    # ── 1. Structured log ──────────────────────────────────────────────────
    _log.info(
        "agent_action",
        agent=agent,
        action=action,
        project_id=project_id,
        task_id=task_id,
        file_path=file_path,
        status=status,
        duration_ms=duration_ms,
        **(metadata or {}),
    )

    # ── 2. Async DB insert ────────────────────────────────────────────────
    try:
        import uuid as _uuid

        from db.connection import db_manager
        from db.models import AgentLog

        row = AgentLog(
            id=_uuid.uuid4(),
            project_id=_uuid.UUID(project_id),
            task_id=_uuid.UUID(task_id) if task_id else None,
            agent=agent[:255],
            action=action[:255],
            file_path=(file_path or "")[:500] or None,
            status=status[:50],
            duration_ms=max(0, duration_ms),
            metadata=metadata,
            timestamp=datetime.now(timezone.utc),
        )

        async for session in db_manager.get_session():
            session.add(row)
            await session.commit()
            break

    except Exception as exc:
        # Never let a logging failure crash the agent
        get_logger("observability.logger").warning(
            "agent_log_db_insert_failed",
            agent=agent,
            action=action,
            project_id=project_id,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# AgentActionContext — async context manager
# ---------------------------------------------------------------------------


class AgentActionContext:
    """Async context manager that measures and logs an agent action.

    On **enter**: logs ``action_start`` and records the monotonic start time.
    On **exit**:  computes ``duration_ms``, logs ``action_end`` (or
    ``action_error`` on exception), then calls :func:`log_agent_action` which
    also persists the row to ``agent_logs``.

    Usage::

        async with AgentActionContext(
            agent="backend_agent",
            action="write_file",
            project_id=project_id,
            task_id=task_id,
            file_path="src/main.py",
        ):
            await workspace_manager.write_file(...)

    Args:
        agent: Agent name.
        action: Action identifier.
        project_id: Owning project UUID string.
        task_id: Related task UUID string (optional).
        file_path: Affected file path (optional).
        metadata: Extra context to include in the log (optional).
    """

    def __init__(
        self,
        *,
        agent: str,
        action: str,
        project_id: str,
        task_id: Optional[str] = None,
        file_path: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.agent = agent
        self.action = action
        self.project_id = project_id
        self.task_id = task_id
        self.file_path = file_path
        self.metadata = metadata or {}
        self._start: float = 0.0
        self._log = get_logger("observability.agent_action")

    async def __aenter__(self) -> "AgentActionContext":
        self._start = time.monotonic()
        self._log.debug(
            "action_start",
            agent=self.agent,
            action=self.action,
            project_id=self.project_id,
            task_id=self.task_id,
            file_path=self.file_path,
        )
        # Bind context so all logs emitted inside the block carry these keys
        structlog.contextvars.bind_contextvars(
            agent=self.agent,
            action=self.action,
            project_id=self.project_id,
            task_id=self.task_id or "",
        )
        return self

    async def __aexit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        duration_ms = int((time.monotonic() - self._start) * 1_000)
        # Unbind the keys we added so they don't leak into sibling coroutines
        structlog.contextvars.unbind_contextvars("agent", "action", "project_id", "task_id")

        if exc_type is not None:
            error_meta = {**self.metadata, "error": str(exc_val), "error_type": exc_type.__name__}
            self._log.error(
                "action_error",
                agent=self.agent,
                action=self.action,
                project_id=self.project_id,
                task_id=self.task_id,
                duration_ms=duration_ms,
                error=str(exc_val),
            )
            await log_agent_action(
                agent=self.agent,
                action=self.action,
                project_id=self.project_id,
                task_id=self.task_id,
                file_path=self.file_path,
                status="error",
                duration_ms=duration_ms,
                metadata=error_meta,
            )
        else:
            self._log.info(
                "action_end",
                agent=self.agent,
                action=self.action,
                project_id=self.project_id,
                task_id=self.task_id,
                duration_ms=duration_ms,
            )
            await log_agent_action(
                agent=self.agent,
                action=self.action,
                project_id=self.project_id,
                task_id=self.task_id,
                file_path=self.file_path,
                status="success",
                duration_ms=duration_ms,
                metadata=self.metadata or None,
            )

        return False  # never suppress exceptions
