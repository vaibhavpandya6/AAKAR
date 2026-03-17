"""Central LangGraph StateGraph — the orchestration backbone of ai-dev-platform.

This module wires every node and the fix-retry subgraph into a single
compiled StateGraph.  Import :func:`build_graph` and call it with a
checkpointer to obtain a ready-to-invoke graph.

Graph topology
--------------

    START
      │
    planner ──────────────────────────────► [LLM → task DAG]
      │
    hitl_formatter ───────────────────────► [format plan, DB → AWAITING_APPROVAL]
      │
      ║ ← interrupt_before="router" (HITL pause; resume via /plan/approve)
      │
    router ────────────────────────────────► [validate DAG, enqueue wave 1]
      │
    task_monitor ──► (loop) ──────────────► [poll completions, enqueue next waves]
      │
      ├─ more_tasks ──► task_monitor       (tasks still in flight)
      ├─ failed     ──► END               (all tasks failed)
      └─ qa         ──► qa
                          │
                          ├─ fix     ──► fix_retry ──► (back to qa)
                          ├─ review  ──► reviewer
                          │                 │
                          │                 ├─ deliver ──► delivery ──► END
                          │                 └─ failed  ──► END
                          └─ max_retries_exceeded ──► END
"""

import asyncio
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

from messaging.message_bus import get_message_bus
from messaging.schemas import MessageType
from orchestrator.nodes.delivery_node import delivery_node
from orchestrator.nodes.hitl_node import hitl_node
from orchestrator.nodes.planner_node import planner_node
from orchestrator.nodes.qa_node import qa_node
from orchestrator.nodes.reviewer_node import reviewer_node
from orchestrator.nodes.router_node import router_node
from orchestrator.state import PlatformState, ProjectStatus
from orchestrator.subgraphs.fix_retry_subgraph import (
    FixRetryState,
    fix_retry_subgraph,
)
from task_system.task_graph import TaskGraph
from task_system.task_queue import TaskQueue

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ORCHESTRATOR_STREAM = "stream:orchestrator"
_MONITOR_GROUP = "task-monitor-workers"
_MONITOR_CONSUMER = "task-monitor"
_POLL_TIMEOUT_MS = 3_000    # blocking read duration per poll iteration
_POLL_COUNT = 20            # max messages per XREADGROUP call

#: Global retry ceiling shared with route_after_qa; must match FixRetryState.max_retries
_MAX_TASK_RETRIES = 3


# ===========================================================================
# task_monitor_node
# ===========================================================================


async def task_monitor_node(state: PlatformState) -> dict[str, Any]:
    """Poll Redis Streams for agent completion events and dispatch the next task wave.

    Called in a loop by the graph until all tasks in the DAG are resolved
    (every task is either in ``completed_tasks`` or ``failed_tasks``).

    Responsibilities
    ----------------
    1. Consume up to :data:`_POLL_COUNT` messages from ``stream:orchestrator``
       (3-second blocking read).
    2. Route each message:

       - ``TASK_COMPLETE``   → move task to ``completed_tasks``; record files
       - ``TASK_FAILED``     → move task to ``failed_tasks``
       - ``MERGE_CONFLICT``  → log warning; mark task failed with conflict note
       - ``LOCK_TIMEOUT``    → log warning; mark task failed with timeout note
       - All others          → acknowledge and discard (BUG_REPORT, FILE_WRITTEN, …)

    3. Re-compute the set of *newly ready* tasks using
       :meth:`~task_system.task_graph.TaskGraph.get_ready_tasks` and enqueue
       each one via :class:`~task_system.task_queue.TaskQueue`.

    4. Return updated state slices — LangGraph shallow-merges the dict into
       the parent state before calling the routing function.

    Args:
        state: Current PlatformState.

    Returns:
        Partial state: ``pending_tasks``, ``completed_tasks``,
        ``failed_tasks``, ``files_written``.
    """
    project_id = state.get("project_id", "")
    task_dag: list[dict] = state.get("task_dag") or []

    # Mutable working copies — never mutate the original state lists
    pending_tasks: list[dict] = list(state.get("pending_tasks") or [])
    completed_tasks: list[dict] = list(state.get("completed_tasks") or [])
    failed_tasks: list[dict] = list(state.get("failed_tasks") or [])
    files_written: list[str] = list(state.get("files_written") or [])

    completed_ids: set[str] = {str(t.get("id")) for t in completed_tasks}
    failed_ids: set[str] = {str(t.get("id")) for t in failed_tasks}
    pending_ids: set[str] = {str(t.get("id")) for t in pending_tasks}

    # Fast O(1) look-up of task dicts by ID
    task_by_id: dict[str, dict] = {str(t.get("id")): t for t in task_dag}

    logger.info(
        "task_monitor_tick",
        project_id=project_id,
        pending=len(pending_tasks),
        completed=len(completed_tasks),
        failed=len(failed_tasks),
        total_dag=len(task_dag),
    )

    # ------------------------------------------------------------------
    # 1. Poll the orchestrator stream
    # ------------------------------------------------------------------
    message_bus = await get_message_bus()
    await message_bus.create_consumer_group(
        _ORCHESTRATOR_STREAM, _MONITOR_GROUP, start_id="0"
    )

    try:
        messages = await message_bus.consume(
            _ORCHESTRATOR_STREAM,
            _MONITOR_GROUP,
            _MONITOR_CONSUMER,
            count=_POLL_COUNT,
            timeout=_POLL_TIMEOUT_MS,
        )
    except Exception as exc:
        logger.warning(
            "task_monitor_consume_error",
            project_id=project_id,
            error=str(exc),
        )
        messages = []

    # ------------------------------------------------------------------
    # 2. Process each message
    # ------------------------------------------------------------------
    for msg in messages:
        try:
            _handle_message(
                msg=msg,
                project_id=project_id,
                task_by_id=task_by_id,
                completed_tasks=completed_tasks,
                completed_ids=completed_ids,
                failed_tasks=failed_tasks,
                failed_ids=failed_ids,
                pending_tasks=pending_tasks,
                pending_ids=pending_ids,
                files_written=files_written,
            )
        except Exception as exc:
            logger.warning(
                "task_monitor_message_error",
                project_id=project_id,
                message_id=msg.message_id,
                error=str(exc),
            )
        finally:
            # Always acknowledge — prevents message re-delivery to this consumer group
            await _safe_ack(message_bus, msg.message_id)

    # ------------------------------------------------------------------
    # 3. Dispatch newly ready tasks (next wave)
    # ------------------------------------------------------------------
    already_dispatched = completed_ids | failed_ids | pending_ids
    task_graph = TaskGraph()

    new_ready = [
        t for t in task_graph.get_ready_tasks(task_dag, list(completed_ids))
        if str(t.get("id")) not in already_dispatched
    ]

    if new_ready:
        queue = TaskQueue()
        for task in new_ready:
            tid = str(task.get("id", ""))
            try:
                await queue.enqueue(project_id, task)
                pending_tasks.append(task)
                pending_ids.add(tid)
                logger.info(
                    "task_monitor_next_wave",
                    project_id=project_id,
                    task_id=tid,
                    wave_size=len(new_ready),
                )
            except Exception as exc:
                logger.error(
                    "task_monitor_dispatch_failed",
                    project_id=project_id,
                    task_id=tid,
                    error=str(exc),
                )

    return {
        "pending_tasks": pending_tasks,
        "completed_tasks": completed_tasks,
        "failed_tasks": failed_tasks,
        "files_written": files_written,
    }


def _handle_message(
    msg: Any,
    project_id: str,
    task_by_id: dict[str, dict],
    completed_tasks: list[dict],
    completed_ids: set[str],
    failed_tasks: list[dict],
    failed_ids: set[str],
    pending_tasks: list[dict],
    pending_ids: set[str],
    files_written: list[str],
) -> None:
    """Mutate the in-flight tracking lists based on a single stream message.

    Modifies ``completed_tasks``, ``failed_tasks``, ``pending_tasks``, and
    ``files_written`` in place.  Called from the main monitor loop inside a
    try/except so exceptions are never fatal.

    Args:
        msg: Consumed :class:`~messaging.schemas.Message`.
        project_id: Project identifier for log context.
        task_by_id: Fast ID→task dict lookup built from ``task_dag``.
        completed_tasks: Mutable list of completed task dicts.
        completed_ids: Set of completed task IDs (kept in sync).
        failed_tasks: Mutable list of failed task dicts.
        failed_ids: Set of failed task IDs (kept in sync).
        pending_tasks: Mutable list of pending task dicts.
        pending_ids: Set of pending task IDs (kept in sync).
        files_written: Mutable accumulator of written file paths.
    """
    # correlation_id must be "project_id:task_id"
    parts = msg.correlation_id.split(":")
    if len(parts) != 2:
        return
    _, raw_task_id = parts

    # Strip QA / fix prefixes to recover the underlying implementation task ID
    impl_task_id = _resolve_impl_task_id(raw_task_id)

    # ── TASK_COMPLETE ──────────────────────────────────────────────────────────
    if msg.message_type == MessageType.TASK_COMPLETE:
        # Only track implementation tasks (not qa_* or fix_* sub-tasks)
        if impl_task_id not in task_by_id:
            return
        if impl_task_id in completed_ids:
            return  # already recorded — idempotent

        task_data = task_by_id[impl_task_id]
        new_files: list[str] = msg.payload.get("files_written", [])

        completed_tasks.append(
            {
                **task_data,
                "files_written": new_files,
                "completed_at": msg.payload.get("timestamp", ""),
            }
        )
        completed_ids.add(impl_task_id)

        # Remove from pending
        pending_tasks[:] = [t for t in pending_tasks if str(t.get("id")) != impl_task_id]
        pending_ids.discard(impl_task_id)

        # Accumulate file paths (deduplicated)
        files_written.extend(f for f in new_files if f not in files_written)

        logger.info(
            "task_monitor_complete",
            project_id=project_id,
            task_id=impl_task_id,
            new_files=len(new_files),
        )

    # ── TASK_FAILED ───────────────────────────────────────────────────────────
    elif msg.message_type == MessageType.TASK_FAILED:
        if impl_task_id not in task_by_id:
            return
        if impl_task_id in failed_ids or impl_task_id in completed_ids:
            return  # already recorded — idempotent

        task_data = task_by_id[impl_task_id]
        failed_tasks.append(
            {
                **task_data,
                "error": msg.payload.get("error", "unknown error")[:500],
                "failed_at": msg.payload.get("timestamp", ""),
            }
        )
        failed_ids.add(impl_task_id)

        pending_tasks[:] = [t for t in pending_tasks if str(t.get("id")) != impl_task_id]
        pending_ids.discard(impl_task_id)

        logger.warning(
            "task_monitor_failed",
            project_id=project_id,
            task_id=impl_task_id,
            error=msg.payload.get("error", "")[:200],
        )

    # ── MERGE_CONFLICT ────────────────────────────────────────────────────────
    elif msg.message_type == MessageType.MERGE_CONFLICT:
        logger.warning(
            "task_monitor_merge_conflict",
            project_id=project_id,
            raw_task_id=raw_task_id,
            impl_task_id=impl_task_id,
            sender=msg.sender,
        )
        # Mark the impl task as failed to unblock DAG execution.
        # The delivery_node already handles conflicts by skipping conflicting branches.
        if impl_task_id in task_by_id and impl_task_id not in (completed_ids | failed_ids):
            task_data = task_by_id[impl_task_id]
            failed_tasks.append(
                {
                    **task_data,
                    "error": "Git merge conflict — requires manual resolution before delivery",
                    "failed_at": "",
                    "conflict": True,
                }
            )
            failed_ids.add(impl_task_id)
            pending_tasks[:] = [t for t in pending_tasks if str(t.get("id")) != impl_task_id]
            pending_ids.discard(impl_task_id)

    # ── LOCK_TIMEOUT ──────────────────────────────────────────────────────────
    elif msg.message_type == MessageType.LOCK_TIMEOUT:
        logger.warning(
            "task_monitor_lock_timeout",
            project_id=project_id,
            raw_task_id=raw_task_id,
            impl_task_id=impl_task_id,
            payload=msg.payload,
        )
        # Mark as failed; fix_retry or a human can re-attempt.
        if impl_task_id in task_by_id and impl_task_id not in (completed_ids | failed_ids):
            task_data = task_by_id[impl_task_id]
            failed_tasks.append(
                {
                    **task_data,
                    "error": "File lock acquisition timed out — workspace contention",
                    "failed_at": "",
                    "lock_timeout": True,
                }
            )
            failed_ids.add(impl_task_id)
            pending_tasks[:] = [t for t in pending_tasks if str(t.get("id")) != impl_task_id]
            pending_ids.discard(impl_task_id)

    # All other message types (BUG_REPORT, FILE_WRITTEN, REVIEW_RESULT, …) are
    # consumed by other consumer groups; we just acknowledge and move on.


# ===========================================================================
# Routing functions
# ===========================================================================


def route_after_tasks(state: PlatformState) -> str:
    """Decide whether to keep monitoring, move to QA, or declare hard failure.

    Checks whether all tasks in ``task_dag`` are accounted for in either
    ``completed_tasks``, ``failed_tasks``, or ``pending_tasks``.

    Routes:
        ``"more_tasks"`` — tasks are still in flight or not yet dispatched
        ``"qa"``         — all tasks resolved and at least one succeeded
        ``"failed"``     — all tasks resolved but zero succeeded

    Args:
        state: Current PlatformState after a task_monitor_node invocation.

    Returns:
        Edge label string.
    """
    task_dag: list[dict] = state.get("task_dag") or []
    pending: list[dict] = state.get("pending_tasks") or []
    completed: list[dict] = state.get("completed_tasks") or []
    failed: list[dict] = state.get("failed_tasks") or []

    all_task_ids = {str(t.get("id")) for t in task_dag}
    completed_ids = {str(t.get("id")) for t in completed}
    failed_ids = {str(t.get("id")) for t in failed}
    pending_ids = {str(t.get("id")) for t in pending}
    resolved_ids = completed_ids | failed_ids

    # Tasks not yet in any state (waiting for their dependencies to land)
    unaccounted = all_task_ids - resolved_ids - pending_ids

    if pending or unaccounted:
        # Keep polling — work is still in flight or about to be enqueued
        return "more_tasks"

    # Every task in the DAG is either completed or failed
    if completed_ids:
        logger.info(
            "route_after_tasks_qa",
            project_id=state.get("project_id"),
            completed=len(completed_ids),
            failed=len(failed_ids),
        )
        return "qa"

    # Nothing completed — hard failure
    logger.error(
        "route_after_tasks_all_failed",
        project_id=state.get("project_id"),
        failed_count=len(failed_ids),
    )
    return "failed"


def route_after_qa(state: PlatformState) -> str:
    """Route after a QA pass based on bug reports and retry history.

    Routes:
        ``"review"``              — no bugs found; proceed to code review
        ``"fix"``                 — bugs exist and retries remain
        ``"max_retries_exceeded"``— bugs remain but a task has hit ``_MAX_TASK_RETRIES``

    Args:
        state: Current PlatformState after qa_node returned.

    Returns:
        Edge label string.
    """
    bug_reports: list[dict] = state.get("bug_reports") or []

    if not bug_reports:
        logger.info("route_after_qa_clean", project_id=state.get("project_id"))
        return "review"

    # If the fix-retry subgraph has flagged any task as exhausted, escalate
    retry_counts: dict[str, int] = state.get("retry_counts") or {}
    if any(count >= _MAX_TASK_RETRIES for count in retry_counts.values()):
        logger.warning(
            "route_after_qa_max_retries",
            project_id=state.get("project_id"),
            retry_counts=retry_counts,
            remaining_bugs=len(bug_reports),
        )
        return "max_retries_exceeded"

    logger.info(
        "route_after_qa_fix",
        project_id=state.get("project_id"),
        bug_count=len(bug_reports),
    )
    return "fix"


def route_after_review(state: PlatformState) -> str:
    """Route based on the reviewer agent's decision.

    Routes:
        ``"deliver"`` — reviewer approved; proceed to delivery
        ``"failed"``  — reviewer rejected; terminate with failure

    Args:
        state: Current PlatformState after reviewer_node returned.

    Returns:
        Edge label string.
    """
    review_result: dict = state.get("review_result") or {}

    if review_result.get("approved"):
        logger.info(
            "route_after_review_approved",
            project_id=state.get("project_id"),
        )
        return "deliver"

    issues = [i.get("description", "") for i in (review_result.get("issues") or [])[:5]]
    logger.warning(
        "route_after_review_rejected",
        project_id=state.get("project_id"),
        top_issues=issues,
    )
    return "failed"


# ===========================================================================
# fix_retry wrapper  (PlatformState ↔ FixRetryState translation)
# ===========================================================================


async def _fix_retry_wrapper(state: PlatformState) -> dict[str, Any]:
    """Translate PlatformState into FixRetryState, run the compiled subgraph, merge back.

    :func:`fix_retry_subgraph` uses :class:`~orchestrator.subgraphs.fix_retry_subgraph.FixRetryState`
    which has a different schema from :class:`~orchestrator.state.PlatformState`.
    This wrapper performs the state translation so the subgraph integrates
    seamlessly as a regular node in the parent graph.

    Translation mapping
    -------------------
    In  (PlatformState)              →  FixRetryState
    ─────────────────────────────────────────────────
    ``project_id``                   →  ``project_id``
    ``bug_reports``                  →  ``bug_report``
    ``retry_counts[target_id]``      →  ``retry_count``

    Out (FixRetryState)              →  PlatformState
    ─────────────────────────────────────────────────
    ``bug_report``                   →  ``bug_reports``
    ``retry_count``                  →  ``retry_counts[target_id]``
    ``escalate_to_human``            →  ``error_message`` (human-readable note)

    Args:
        state: Current PlatformState; ``bug_reports`` must be non-empty.

    Returns:
        Partial state dict: ``bug_reports``, ``retry_counts``,
        ``error_message`` (only set on escalation).
    """
    project_id: str = state.get("project_id", "")
    bug_reports: list[dict] = state.get("bug_reports") or []
    retry_counts: dict[str, int] = dict(state.get("retry_counts") or {})

    # Identify which implementation task to target for this fix cycle
    target_task_id = _select_fix_target(
        task_dag=state.get("task_dag") or [],
        completed_ids={str(t.get("id")) for t in (state.get("completed_tasks") or [])},
        failed_tasks=state.get("failed_tasks") or [],
        retry_counts=retry_counts,
        current_task=state.get("current_task"),
    )

    current_retries = retry_counts.get(target_task_id, 0)

    logger.info(
        "fix_retry_wrapper_start",
        project_id=project_id,
        target_task_id=target_task_id,
        retry_count=current_retries,
        bug_count=len(bug_reports),
    )

    subgraph_input = FixRetryState(
        project_id=project_id,
        task_id=target_task_id,
        bug_report=bug_reports,
        retry_count=current_retries,
        max_retries=_MAX_TASK_RETRIES,
        fix_applied=False,
        tests_passing=False,
        escalate_to_human=False,
    )

    # Run the compiled subgraph synchronously from the parent's perspective
    result: FixRetryState = await fix_retry_subgraph.ainvoke(subgraph_input)

    # Update per-task retry count in parent state
    retry_counts[target_task_id] = result.get("retry_count", current_retries + 1)
    remaining_bugs: list[dict] = result.get("bug_report") or []
    escalated: bool = bool(result.get("escalate_to_human", False))

    if escalated:
        logger.warning(
            "fix_retry_wrapper_escalated",
            project_id=project_id,
            target_task_id=target_task_id,
            total_retries=retry_counts[target_task_id],
            remaining_bugs=len(remaining_bugs),
        )

    out: dict[str, Any] = {
        "bug_reports": remaining_bugs,
        "retry_counts": retry_counts,
    }
    if escalated:
        out["error_message"] = (
            f"Fix loop escalated to human after {retry_counts[target_task_id]} "
            f"retries for task '{target_task_id}'"
        )

    return out


# ===========================================================================
# Graph builder
# ===========================================================================


def build_graph(checkpointer: Any) -> Any:
    """Assemble and compile the complete platform orchestration StateGraph.

    Call this once at application startup, passing the checkpointer returned
    by :func:`~orchestrator.checkpointer.get_checkpointer`::

        from orchestrator.checkpointer import get_checkpointer
        from orchestrator.graph import build_graph

        checkpointer = get_checkpointer()
        graph = build_graph(checkpointer)

        # Start a new project run
        config = {"configurable": {"thread_id": project_id}}
        await graph.ainvoke(initial_state(project_id, user_id, prompt), config)

    HITL resume flow::

        # Operator approves via API → graph resumes at "router"
        await graph.aupdate_state(config, {"plan_approved": True})
        async for event in graph.astream(None, config):
            ...

    Args:
        checkpointer: A LangGraph-compatible checkpoint saver instance.
                      Accepts :class:`~langgraph.checkpoint.sqlite.SqliteSaver`
                      or :class:`~langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`.

    Returns:
        Compiled StateGraph ready for ``ainvoke`` / ``astream``.
    """
    graph = StateGraph(PlatformState)

    # ── Node registration ─────────────────────────────────────────────────────
    graph.add_node("planner", planner_node)
    graph.add_node("hitl_formatter", hitl_node)
    graph.add_node("router", router_node)
    graph.add_node("task_monitor", task_monitor_node)
    graph.add_node("qa", qa_node)
    graph.add_node("fix_retry", _fix_retry_wrapper)   # translates state for fix_retry_subgraph
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("delivery", delivery_node)

    # ── Linear spine ──────────────────────────────────────────────────────────
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "hitl_formatter")

    # ╔══════════════════════════════════════════════════════════════════════╗
    # ║  HITL PAUSE POINT                                                    ║
    # ║  interrupt_before=["router"] suspends the graph BEFORE "router"     ║
    # ║  runs.  The graph checkpoints state so no work is lost.             ║
    # ║  Resume by calling:                                                  ║
    # ║    await graph.aupdate_state(config, {"plan_approved": True})       ║
    # ║    await graph.astream(None, config)                                ║
    # ╚══════════════════════════════════════════════════════════════════════╝
    graph.add_edge("hitl_formatter", "router")

    graph.add_edge("router", "task_monitor")

    # ── task_monitor polling loop ─────────────────────────────────────────────
    graph.add_conditional_edges(
        "task_monitor",
        route_after_tasks,
        {
            "more_tasks": "task_monitor",   # loop: tasks still in flight
            "qa": "qa",                     # all resolved → QA
            "failed": END,                  # nothing succeeded → hard fail
        },
    )

    # ── QA → fix cycle or review ──────────────────────────────────────────────
    graph.add_conditional_edges(
        "qa",
        route_after_qa,
        {
            "fix": "fix_retry",             # bugs found, retries available
            "review": "reviewer",           # clean build → human review
            "max_retries_exceeded": END,    # exhausted retries → escalate via HITL
        },
    )

    # After every fix attempt, unconditionally re-run QA to verify resolution
    graph.add_edge("fix_retry", "qa")

    # ── Review decision ───────────────────────────────────────────────────────
    graph.add_conditional_edges(
        "reviewer",
        route_after_review,
        {
            "deliver": "delivery",
            "failed": END,
        },
    )

    graph.add_edge("delivery", END)

    # ── Compile ───────────────────────────────────────────────────────────────
    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["router"],   # HITL gate: pause before dispatching tasks
    )


# ===========================================================================
# Private helpers
# ===========================================================================


def _resolve_impl_task_id(task_id: str) -> str:
    """Strip QA / fix prefixes to recover the underlying implementation task ID.

    The orchestrator names sub-tasks with predictable prefixes so they can be
    routed to specialist agents.  Strip them here to map completion events back
    to the original DAG task.

    Patterns:
        ``qa_{impl_id}_{hex6}``         → ``{impl_id}``
        ``fix_{impl_id}_r{n}_{hex6}``   → ``{impl_id}``
        ``{impl_id}``                   → ``{impl_id}``  (passthrough)

    Args:
        task_id: Raw task ID extracted from a ``correlation_id``.

    Returns:
        The underlying implementation task ID string.
    """
    if task_id.startswith("qa_"):
        # "qa_{impl_id}_{hex6}" — strip prefix and trailing unique suffix
        body = task_id[3:]  # drop "qa_"
        parts = body.rsplit("_", 1)
        # Only strip if the tail looks like a 6-char hex suffix
        return parts[0] if len(parts) == 2 and len(parts[1]) == 6 else body

    if task_id.startswith("fix_"):
        # "fix_{impl_id}_r{n}_{hex6}" — impl_id is everything before "_r{n}"
        body = task_id[4:]  # drop "fix_"
        idx = body.find("_r")
        return body[:idx] if idx != -1 else body

    return task_id


def _select_fix_target(
    task_dag: list[dict],
    completed_ids: set[str],
    failed_tasks: list[dict],
    retry_counts: dict[str, int],
    current_task: dict | None,
) -> str:
    """Choose which implementation task the fix-retry loop should target.

    Priority order:
    1. Failed implementation task with the fewest retries so far (fairest dispatch)
    2. ``current_task`` from PlatformState if set
    3. First incomplete task in ``task_dag``
    4. Fallback sentinel ``"project-wide-fix"``

    Only non-QA, non-fix tasks are considered for fix targeting.

    Args:
        task_dag: Full list of implementation task dicts.
        completed_ids: IDs of successfully completed tasks.
        failed_tasks: Tasks currently in the failed list.
        retry_counts: Current retry counts per task ID.
        current_task: Optional ``current_task`` field from PlatformState.

    Returns:
        Target task ID string.
    """
    # Eligible: failed impl tasks that still have retry budget
    eligible = [
        ft for ft in failed_tasks
        if (
            not str(ft.get("id", "")).startswith("qa_")
            and not str(ft.get("id", "")).startswith("fix_")
            and retry_counts.get(str(ft.get("id", "")), 0) < _MAX_TASK_RETRIES
        )
    ]
    if eligible:
        # Pick the one with the fewest retries (gives each task equal opportunity)
        return str(
            min(eligible, key=lambda t: retry_counts.get(str(t.get("id", "")), 0)).get("id", "")
        )

    # Fall back to current_task
    if current_task:
        return str(current_task.get("id", ""))

    # Scan task_dag for first incomplete task
    for task in task_dag:
        tid = str(task.get("id", ""))
        if tid not in completed_ids:
            return tid

    return "project-wide-fix"


async def _safe_ack(message_bus: Any, message_id: str) -> None:
    """Acknowledge a stream message, swallowing errors silently.

    A failed ACK means the message will be redelivered on the next poll,
    which is safe because all handlers are idempotent (they check
    ``completed_ids``/``failed_ids`` before writing).

    Args:
        message_bus: Active MessageBus instance.
        message_id: Redis stream message ID to acknowledge.
    """
    try:
        await message_bus.acknowledge(_ORCHESTRATOR_STREAM, _MONITOR_GROUP, message_id)
    except Exception as exc:
        logger.warning(
            "task_monitor_ack_failed",
            message_id=message_id,
            error=str(exc),
        )
