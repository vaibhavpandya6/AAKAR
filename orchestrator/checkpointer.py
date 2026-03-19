"""LangGraph checkpointer factory and state persistence helpers.

Supports SQLite (development) and PostgreSQL (production) backends,
selected via the CHECKPOINTER environment variable.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Union

import structlog

from config import settings
from orchestrator.state import PlatformState

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Type alias  — we accept either sync or async saver without coupling
# ---------------------------------------------------------------------------

AnyCheckpointer = Any   # langgraph.checkpoint.base.BaseCheckpointSaver


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_checkpointer() -> AnyCheckpointer:
    """Build and return the appropriate LangGraph checkpointer.

    Reads ``CHECKPOINTER`` from settings:
    - ``"sqlite"``   → :class:`~langgraph.checkpoint.sqlite.SqliteSaver`
      backed by ``checkpoints.db``  (default; great for development)
    - ``"postgres"`` → :class:`~langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`
      backed by the ``POSTGRES_URL`` connection string (production)

    Returns:
        A configured checkpointer instance.

    Raises:
        RuntimeError: If postgres is requested but POSTGRES_URL is missing.
        ImportError: If required optional extras are not installed.
    """
    backend = (settings.checkpointer or "sqlite").lower().strip()
    logger.info("checkpointer_backend_selected", backend=backend)

    if backend == "postgres":
        return _build_postgres_checkpointer()

    # Default → SQLite
    return _build_sqlite_checkpointer()


def _build_sqlite_checkpointer() -> AnyCheckpointer:
    """Build a memory-backed checkpointer for development.

    Note: For production, use PostgreSQL checkpointer instead.

    Returns:
        MemorySaver instance (in-memory checkpointing).
    """
    try:
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "langgraph is required for checkpointing. "
            "Install with: pip install langgraph"
        ) from exc

    saver = MemorySaver()
    logger.info("memory_checkpointer_created", note="Using in-memory checkpointer for development")
    return saver


def _build_postgres_checkpointer() -> AnyCheckpointer:
    """Build a PostgreSQL-backed async checkpointer.

    Returns:
        AsyncPostgresSaver connected via POSTGRES_URL from settings.

    Raises:
        RuntimeError: If POSTGRES_URL is empty or unset.
    """
    if not settings.postgres_url:
        raise RuntimeError(
            "CHECKPOINTER=postgres requires POSTGRES_URL to be set in .env"
        )

    # Convert asyncpg URL to psycopg3 format expected by the postgres saver
    pg_url = settings.postgres_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    ).replace(
        "postgresql+psycopg2://", "postgresql://"
    )

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "langgraph[postgres] extras are required for PostgreSQL checkpointing.  "
            "Install with: pip install 'langgraph[postgres]'"
        ) from exc

    saver = AsyncPostgresSaver.from_conn_string(pg_url)
    logger.info(
        "postgres_checkpointer_created",
        host=pg_url.split("@")[-1] if "@" in pg_url else "redacted",
    )
    return saver


# ---------------------------------------------------------------------------
# State persistence helpers
# ---------------------------------------------------------------------------


def _thread_config(thread_id: str) -> dict:
    """Build the LangGraph RunnableConfig for a thread.

    Args:
        thread_id: Unique identifier for the graph execution thread.

    Returns:
        Config dict with ``configurable.thread_id`` set.
    """
    return {"configurable": {"thread_id": thread_id}}


async def save_state(
    checkpointer: AnyCheckpointer,
    thread_id: str,
    state: PlatformState,
) -> None:
    """Persist a PlatformState snapshot to the checkpointer.

    Wraps the state in a minimal LangGraph Checkpoint envelope and calls
    ``aput`` (async) or ``put`` (sync) depending on the checkpointer type.

    Args:
        checkpointer: The active checkpointer (SQLite or Postgres).
        thread_id: Thread identifier to save under.
        state: Full PlatformState to snapshot.
    """
    config = _thread_config(thread_id)

    checkpoint: dict = {
        "v": 1,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel_values": dict(state),
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }

    metadata = {
        "source": "save_state",
        "step": -1,
        "writes": {},
        "project_id": state.get("project_id", ""),
        "project_status": state.get("project_status", ""),
    }

    try:
        # Prefer async put if available (PostgresSaver, async-wrapped SqliteSaver)
        if hasattr(checkpointer, "aput"):
            await checkpointer.aput(config, checkpoint, metadata)
        else:
            checkpointer.put(config, checkpoint, metadata)

        logger.info(
            "state_saved",
            thread_id=thread_id,
            project_id=state.get("project_id"),
            project_status=state.get("project_status"),
        )

    except Exception as exc:
        logger.error(
            "state_save_failed",
            thread_id=thread_id,
            error=str(exc),
        )
        raise


async def load_state(
    checkpointer: AnyCheckpointer,
    thread_id: str,
) -> Optional[PlatformState]:
    """Load the most recent PlatformState snapshot for a thread.

    Args:
        checkpointer: The active checkpointer.
        thread_id: Thread identifier to retrieve.

    Returns:
        The most recently saved PlatformState, or ``None`` if no checkpoint
        exists for this thread.
    """
    config = _thread_config(thread_id)

    try:
        # Prefer async get if available
        if hasattr(checkpointer, "aget"):
            checkpoint = await checkpointer.aget(config)
        else:
            checkpoint = checkpointer.get(config)

        if checkpoint is None:
            logger.debug("no_checkpoint_found", thread_id=thread_id)
            return None

        channel_values: dict = checkpoint.get("channel_values", {})
        if not channel_values:
            return None

        state = PlatformState(**channel_values)

        logger.info(
            "state_loaded",
            thread_id=thread_id,
            project_id=state.get("project_id"),
            project_status=state.get("project_status"),
        )
        return state

    except Exception as exc:
        logger.error(
            "state_load_failed",
            thread_id=thread_id,
            error=str(exc),
        )
        return None
