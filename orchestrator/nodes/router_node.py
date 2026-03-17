"""Router node — validates the DAG, computes the first execution wave,
assigns skills, and enqueues ready tasks to Redis Streams.
"""

from typing import Any

import structlog

from orchestrator.state import PlatformState, ProjectStatus
from task_system.task_graph import InvalidDAGError, TaskGraph
from task_system.task_queue import TaskQueue

logger = structlog.get_logger()


async def router_node(state: PlatformState) -> dict[str, Any]:
    """Validate the task DAG and enqueue the first wave of ready tasks.

    Reads ``task_dag`` from state.  Validates the full graph, retrieves
    tasks whose dependencies are already satisfied (first wave has none),
    and enqueues each one to the appropriate Redis Stream via
    :class:`~task_system.task_queue.TaskQueue`.

    Args:
        state: Current PlatformState.

    Returns:
        Partial state dict with ``pending_tasks`` and
        ``project_status = IN_PROGRESS``.  On failure returns
        ``error_message`` and ``project_status = FAILED``.
    """
    project_id = state.get("project_id", "")
    task_dag: list[dict] = state.get("task_dag") or []

    logger.info(
        "router_node_start",
        project_id=project_id,
        task_count=len(task_dag),
    )

    # ── Validate DAG ─────────────────────────────────────────────────────────
    graph = TaskGraph()

    try:
        graph.build_from_dag(task_dag)
    except InvalidDAGError as exc:
        logger.error(
            "router_dag_invalid",
            project_id=project_id,
            reason=exc.reason,
            details=exc.details,
        )
        return {
            "error_message": f"DAG validation failed in router: {exc}",
            "project_status": ProjectStatus.FAILED,
        }

    # ── Compute first-wave ready tasks ────────────────────────────────────────
    # No tasks are completed yet on first entry; pass empty list.
    already_completed: list[str] = [
        str(t.get("id", "")) for t in (state.get("completed_tasks") or [])
    ]
    ready_tasks = graph.get_ready_tasks(task_dag, already_completed)

    if not ready_tasks:
        logger.warning(
            "router_no_ready_tasks",
            project_id=project_id,
            completed_count=len(already_completed),
            total_tasks=len(task_dag),
        )
        # All tasks already done or DAG is empty
        return {
            "pending_tasks": [],
            "project_status": ProjectStatus.IN_PROGRESS,
        }

    # ── Enqueue ready tasks ───────────────────────────────────────────────────
    queue = TaskQueue()
    enqueued: list[dict] = []

    for task in ready_tasks:
        task_id = str(task.get("id", ""))
        try:
            redis_id = await queue.enqueue(project_id, task)
            enqueued.append({**task, "_redis_id": redis_id})
            logger.info(
                "task_enqueued",
                project_id=project_id,
                task_id=task_id,
                redis_id=redis_id,
            )
        except Exception as exc:
            logger.error(
                "task_enqueue_failed",
                project_id=project_id,
                task_id=task_id,
                error=str(exc),
            )
            # Don't abort the entire dispatch — partial enqueue is better
            # than a full stop; the retry mechanism can handle stragglers.
            enqueued.append({**task, "_enqueue_error": str(exc)})

    logger.info(
        "router_node_complete",
        project_id=project_id,
        enqueued=len(enqueued),
        wave_size=len(ready_tasks),
    )

    return {
        "pending_tasks": enqueued,
        "project_status": ProjectStatus.IN_PROGRESS,
        "error_message": None,
    }
