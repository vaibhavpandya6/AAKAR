"""Task queue built on Redis Streams with consumer-group delivery."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
import structlog

from config import settings
from task_system.router import AgentRouter

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Redis key conventions
# ---------------------------------------------------------------------------
#   task stream  : tasks:{project_id}
#   status hash  : task_status:{project_id}
#   consumer grp : workers
# ---------------------------------------------------------------------------

_STREAM_PREFIX = "tasks"
_STATUS_PREFIX = "task_status"
_GROUP_NAME = "workers"

# Task status constants
STATUS_PENDING = "PENDING"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETE = "COMPLETE"
STATUS_FAILED = "FAILED"


class TaskQueue:
    """Manages task lifecycle via Redis Streams.

    One stream per project:  ``tasks:{project_id}``
    Task statuses stored in a Redis hash: ``task_status:{project_id}``
    Consumer group ``workers`` gives each agent its own delivery cursor.
    """

    def __init__(self, redis_client: Optional[aioredis.Redis] = None) -> None:
        self._redis: Optional[aioredis.Redis] = redis_client
        self._router = AgentRouter()

    async def _r(self) -> aioredis.Redis:
        """Lazy-initialise Redis client and return it."""
        if not self._redis:
            self._redis = await aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    # ──────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _stream_key(project_id: str) -> str:
        return f"{_STREAM_PREFIX}:{project_id}"

    @staticmethod
    def _status_key(project_id: str) -> str:
        return f"{_STATUS_PREFIX}:{project_id}"

    async def _ensure_consumer_group(self, stream_key: str) -> None:
        """Create the consumer group on the stream (idempotent)."""
        r = await self._r()
        try:
            await r.xgroup_create(stream_key, _GROUP_NAME, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    @staticmethod
    def _serialise_task(task: Dict[str, Any]) -> Dict[str, str]:
        """Flatten task dict to Redis-compatible string dict."""
        return {
            k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
            for k, v in task.items()
        }

    @staticmethod
    def _deserialise_task(raw: Dict[str, str]) -> Dict[str, Any]:
        """Reconstruct task dict from Redis string dict."""
        result: Dict[str, Any] = {}
        for k, v in raw.items():
            try:
                result[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                result[k] = v
        return result

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    async def enqueue(self, project_id: str, task: Dict[str, Any]) -> str:
        """Add a task to the project queue.

        Determines the target skill/stream via AgentRouter, writes the task
        to both the project stream and the agent-specific stream, and
        initialises the task status to PENDING.

        Args:
            project_id: Project identifier.
            task: Task dict. Must contain at least ``id`` and ``title``.

        Returns:
            Redis stream entry ID of the enqueued task.
        """
        r = await self._r()
        task_id = str(task.get("id", ""))
        stream_key = self._stream_key(project_id)
        status_key = self._status_key(project_id)

        await self._ensure_consumer_group(stream_key)

        # Route to skill → agent stream
        skill = self._router.route_task(task)
        agent_stream = self._router.get_agent_stream(skill)

        # Augment task with routing metadata
        enriched = {
            **task,
            "project_id": project_id,
            "skill": skill,
            "agent_stream": agent_stream,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "status": STATUS_PENDING,
        }

        # Write to project stream
        redis_id = await r.xadd(stream_key, self._serialise_task(enriched))

        # Also push to the dedicated agent stream so the correct agent picks it up
        await r.xadd(agent_stream, self._serialise_task(enriched))

        # Track status in a hash keyed by task_id
        await r.hset(
            status_key,
            task_id,
            json.dumps(
                {
                    "status": STATUS_PENDING,
                    "skill": skill,
                    "enqueued_at": enriched["enqueued_at"],
                    "redis_id": redis_id,
                }
            ),
        )

        await logger.ainfo(
            "task_enqueued",
            project_id=project_id,
            task_id=task_id,
            skill=skill,
            redis_id=redis_id,
        )
        return redis_id

    async def dequeue(
        self,
        agent_name: str,
        stream_key: Optional[str] = None,
        block_ms: int = 1000,
    ) -> Optional[Dict[str, Any]]:
        """Pop the next pending task for an agent.

        First attempts to reclaim any messages stuck in pending state from crashed
        workers using XAUTOCLAIM. If none found, reads new undelivered messages
        using XREADGROUP.

        Does NOT acknowledge — the agent must call :meth:`mark_complete` or
        :meth:`mark_failed` to ack.

        Args:
            agent_name: Consumer name within the group (e.g. "backend-agent-1").
            stream_key: Override stream to read from. If omitted, derive from agent_name.
            block_ms: Block timeout in milliseconds (0 = non-blocking).

        Returns:
            Task dict with added ``_redis_id`` key, or None if nothing pending.
        """
        r = await self._r()

        if stream_key is None:
            # Infer agent stream from name prefix: "backend-agent-1" → stream:backend_agent
            skill = agent_name.split("-")[0]
            stream_key = AgentRouter.get_agent_stream(skill)

        await self._ensure_consumer_group(stream_key)

        # Step 1: Try to reclaim stuck pending messages (older than 30 seconds)
        # This handles crashed/killed workers gracefully
        try:
            autoclaim_result = await r.xautoclaim(
                stream_key,
                _GROUP_NAME,
                agent_name,
                min_idle_time=30000,  # 30 seconds in milliseconds
                start_id="0-0",
                count=1,
            )

            # xautoclaim returns (next_id, [messages], deleted_ids)
            # In redis-py: autoclaim_result is a tuple (start_id, claimed_messages)
            if len(autoclaim_result) >= 2:
                claimed_messages = autoclaim_result[1]
                if claimed_messages:
                    redis_id, raw_data = claimed_messages[0]
                    task = self._deserialise_task(raw_data)
                    task["_redis_id"] = redis_id

                    # Update status to IN_PROGRESS
                    project_id = task.get("project_id", "")
                    if project_id:
                        await self._set_task_status(
                            project_id,
                            str(task.get("id", "")),
                            STATUS_IN_PROGRESS,
                            extra={"reclaimed_by": agent_name, "redis_id": redis_id},
                        )

                    await logger.ainfo(
                        "task_reclaimed",
                        agent=agent_name,
                        task_id=task.get("id"),
                        redis_id=redis_id,
                    )
                    return task
        except Exception as exc:
            # XAUTOCLAIM may not be supported in older Redis versions
            await logger.awarning(
                "xautoclaim_failed",
                agent=agent_name,
                error=str(exc),
                note="Falling back to XREADGROUP",
            )

        # Step 2: Read new undelivered messages
        results = await r.xreadgroup(
            _GROUP_NAME,
            agent_name,
            {stream_key: ">"},
            count=1,
            block=block_ms,
            noack=False,
        )

        if not results:
            return None

        for _stream, messages in results:
            for redis_id, raw_data in messages:
                task = self._deserialise_task(raw_data)
                task["_redis_id"] = redis_id  # caller needs this for ack

                # Update status to IN_PROGRESS
                project_id = task.get("project_id", "")
                if project_id:
                    await self._set_task_status(
                        project_id,
                        str(task.get("id", "")),
                        STATUS_IN_PROGRESS,
                        extra={"dequeued_by": agent_name, "redis_id": redis_id},
                    )

                await logger.ainfo(
                    "task_dequeued",
                    agent=agent_name,
                    task_id=task.get("id"),
                    redis_id=redis_id,
                )
                return task

        return None

    async def mark_complete(
        self, project_id: str, task_id: str, stream_key: str, redis_id: str
    ) -> None:
        """Acknowledge successful task completion.

        Updates status hash and ACKs the message in Redis to remove from pending.

        Args:
            project_id: Project identifier.
            task_id: Task identifier.
            stream_key: Redis stream key (e.g., "stream:backend_agent").
            redis_id: Redis message ID to acknowledge.
        """
        r = await self._r()

        await self._set_task_status(
            project_id,
            task_id,
            STATUS_COMPLETE,
            extra={"completed_at": datetime.now(timezone.utc).isoformat()},
        )

        # ACK the message to remove it from pending
        await r.xack(stream_key, _GROUP_NAME, redis_id)

        await logger.ainfo(
            "task_marked_complete",
            project_id=project_id,
            task_id=task_id,
            redis_id=redis_id,
        )

    async def mark_failed(
        self, project_id: str, task_id: str, stream_key: str, redis_id: str, error: str
    ) -> None:
        """Record task failure.

        Updates status hash and ACKs the message in Redis to remove from pending.

        Args:
            project_id: Project identifier.
            task_id: Task identifier.
            stream_key: Redis stream key (e.g., "stream:backend_agent").
            redis_id: Redis message ID to acknowledge.
            error: Human-readable error description.
        """
        r = await self._r()

        await self._set_task_status(
            project_id,
            task_id,
            STATUS_FAILED,
            extra={
                "error": error[:500],  # cap stored error length
                "failed_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # ACK the message to remove it from pending
        await r.xack(stream_key, _GROUP_NAME, redis_id)

        await logger.awarning(
            "task_marked_failed",
            project_id=project_id,
            task_id=task_id,
            redis_id=redis_id,
            error=error[:200],
        )

    async def get_pending_tasks(self, project_id: str) -> List[Dict[str, Any]]:
        """Return all tasks in PENDING or IN_PROGRESS state for a project.

        Reads the project stream from the beginning and cross-references
        the status hash to filter only active tasks.

        Args:
            project_id: Project identifier.

        Returns:
            List of task dicts with their current status attached.
        """
        r = await self._r()
        stream_key = self._stream_key(project_id)
        status_key = self._status_key(project_id)

        # Read all entries ever written to the project stream
        all_entries = await r.xrange(stream_key)
        all_statuses_raw = await r.hgetall(status_key)

        # Decode statuses
        all_statuses: Dict[str, Dict] = {}
        for task_id_key, raw_json in all_statuses_raw.items():
            try:
                all_statuses[task_id_key] = json.loads(raw_json)
            except json.JSONDecodeError:
                all_statuses[task_id_key] = {}

        pending: List[Dict[str, Any]] = []
        seen_ids: set = set()

        for _redis_id, raw_data in all_entries:
            task = self._deserialise_task(raw_data)
            task_id = str(task.get("id", ""))

            if task_id in seen_ids:
                continue  # deduplicate (task can appear in multiple streams)
            seen_ids.add(task_id)

            status_info = all_statuses.get(task_id, {})
            status = status_info.get("status", STATUS_PENDING)

            if status in (STATUS_PENDING, STATUS_IN_PROGRESS):
                task["_status"] = status
                task["_skill"] = status_info.get("skill", "")
                pending.append(task)

        return pending

    # ──────────────────────────────────────────────────────────────────────
    # Internal status helpers
    # ──────────────────────────────────────────────────────────────────────

    async def _set_task_status(
        self,
        project_id: str,
        task_id: str,
        status: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        r = await self._r()
        status_key = self._status_key(project_id)

        # Load existing record
        existing_raw = await r.hget(status_key, task_id)
        existing: Dict[str, Any] = {}
        if existing_raw:
            try:
                existing = json.loads(existing_raw)
            except json.JSONDecodeError:
                pass

        existing["status"] = status
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        if extra:
            existing.update(extra)

        await r.hset(status_key, task_id, json.dumps(existing))
