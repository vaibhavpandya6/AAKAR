"""QA node — enqueues QA tasks for all completed work and aggregates results.

Creates one QA task per completed implementation task, enqueues them to
the QA agent, then polls the orchestrator stream for results within a
configurable timeout window.
"""

import asyncio
import time
import uuid
from typing import Any

import structlog

from messaging.message_bus import get_message_bus
from messaging.schemas import MessageType
from orchestrator.state import PlatformState, ProjectStatus
from task_system.task_queue import TaskQueue
from workspace_manager.manager import get_workspace_manager

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

_MAX_QA_WAIT_SECONDS = 300      # 5-minute hard timeout
_POLL_TIMEOUT_MS = 2_000        # block per XREADGROUP call
_ORCHESTRATOR_STREAM = "stream:orchestrator"
_QA_GROUP = "qa-node-workers"
_QA_CONSUMER = "qa-node"
_POLL_COUNT = 10                # messages to fetch per polling iteration


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


async def qa_node(state: PlatformState) -> dict[str, Any]:
    """Enqueue QA tasks for every completed implementation task and collect results.

    Workflow:
    1. Load workspace manifest to enumerate all written files.
    2. Create one QA sub-task per entry in ``completed_tasks``.
    3. Enqueue each QA sub-task via :class:`~task_system.task_queue.TaskQueue`.
    4. Poll ``stream:orchestrator`` for ``TASK_COMPLETE`` / ``BUG_REPORT``
       messages whose correlation IDs match the enqueued QA task IDs.
    5. Aggregate bug reports from all results.

    Args:
        state: Current PlatformState.

    Returns:
        Partial state dict.  If bugs found: ``bug_reports`` + ``IN_PROGRESS``.
        If clean: empty ``bug_reports`` + ``REVIEW``.
    """
    project_id = state.get("project_id", "")
    completed_tasks: list[dict] = state.get("completed_tasks") or []

    logger.info(
        "qa_node_start",
        project_id=project_id,
        completed_task_count=len(completed_tasks),
    )

    if not completed_tasks:
        logger.warning("qa_node_no_completed_tasks", project_id=project_id)
        return {
            "bug_reports": [],
            "project_status": ProjectStatus.REVIEW,
        }

    # ── Load workspace manifest ────────────────────────────────────────────────
    workspace_files = await _collect_workspace_files(project_id)

    # ── Create and enqueue QA tasks ─────────────────────────────────────────
    queue = TaskQueue()
    qa_task_ids: set[str] = set()

    for impl_task in completed_tasks:
        impl_id = str(impl_task.get("id", ""))
        qa_task_id = f"qa_{impl_id}_{uuid.uuid4().hex[:6]}"

        qa_task: dict[str, Any] = {
            "id": qa_task_id,
            "title": f"Test: {impl_task.get('title', impl_id)}",
            "description": (
                f"Write and run tests for the following completed task:\n\n"
                f"Original task: {impl_task.get('title', '')}\n"
                f"{impl_task.get('description', '')}\n\n"
                f"Acceptance criteria:\n"
                + "\n".join(
                    f"- {c}"
                    for c in (impl_task.get("acceptance_criteria") or [])
                )
            ),
            "skill_required": "qa",
            "depends_on": [],
            "acceptance_criteria": [
                "All acceptance criteria from the original task must have matching tests.",
                "Test suite passes with zero failures.",
                "No high or critical security issues.",
            ],
            "project_files": workspace_files[:50],  # cap payload size
            "impl_task_id": impl_id,
        }

        try:
            await queue.enqueue(project_id, qa_task)
            qa_task_ids.add(qa_task_id)
            logger.info(
                "qa_task_enqueued",
                project_id=project_id,
                qa_task_id=qa_task_id,
                impl_task_id=impl_id,
            )
        except Exception as exc:
            logger.error(
                "qa_task_enqueue_failed",
                project_id=project_id,
                qa_task_id=qa_task_id,
                error=str(exc),
            )

    if not qa_task_ids:
        logger.error("qa_node_all_enqueues_failed", project_id=project_id)
        return {
            "bug_reports": [],
            "project_status": ProjectStatus.REVIEW,
            "error_message": "All QA task enqueues failed; skipping QA.",
        }

    # ── Poll orchestrator stream for QA results ──────────────────────────────
    message_bus = await get_message_bus()
    await message_bus.create_consumer_group(_ORCHESTRATOR_STREAM, _QA_GROUP, start_id="0")

    collected_results: dict[str, dict] = {}  # qa_task_id → result payload
    deadline = time.monotonic() + _MAX_QA_WAIT_SECONDS

    while len(collected_results) < len(qa_task_ids):
        if time.monotonic() > deadline:
            missing = qa_task_ids - set(collected_results)
            logger.warning(
                "qa_node_poll_timeout",
                project_id=project_id,
                pending_task_ids=list(missing),
                elapsed_seconds=_MAX_QA_WAIT_SECONDS,
            )
            break

        try:
            messages = await message_bus.consume(
                _ORCHESTRATOR_STREAM,
                _QA_GROUP,
                _QA_CONSUMER,
                count=_POLL_COUNT,
                timeout=_POLL_TIMEOUT_MS,
            )
        except Exception as exc:
            logger.warning("qa_node_consume_error", error=str(exc))
            await asyncio.sleep(1)
            continue

        for msg in messages:
            if msg.message_type not in (
                MessageType.TASK_COMPLETE,
                MessageType.BUG_REPORT,
                MessageType.TASK_FAILED,
            ):
                continue

            # correlation_id is "project_id:task_id"
            parts = msg.correlation_id.split(":")
            if len(parts) != 2:
                continue
            _, msg_task_id = parts

            if msg_task_id not in qa_task_ids:
                continue

            collected_results[msg_task_id] = {
                "message_type": msg.message_type.value,
                "payload": msg.payload,
            }

            try:
                await message_bus.acknowledge(
                    _ORCHESTRATOR_STREAM, _QA_GROUP, msg.message_id
                )
            except Exception as exc:
                logger.warning("qa_node_ack_failed", error=str(exc))

            logger.info(
                "qa_result_received",
                project_id=project_id,
                qa_task_id=msg_task_id,
                message_type=msg.message_type.value,
            )

    # ── Aggregate bug reports ─────────────────────────────────────────────────
    all_bug_reports: list[dict] = []

    for task_id, result in collected_results.items():
        bug_reports_from_task: list = result["payload"].get("bug_reports", [])
        all_bug_reports.extend(bug_reports_from_task)

    logger.info(
        "qa_node_complete",
        project_id=project_id,
        total_bugs=len(all_bug_reports),
        results_collected=len(collected_results),
        tasks_queued=len(qa_task_ids),
    )

    if all_bug_reports:
        return {
            "bug_reports": all_bug_reports,
            "project_status": ProjectStatus.IN_PROGRESS,
            "error_message": None,
        }

    return {
        "bug_reports": [],
        "project_status": ProjectStatus.REVIEW,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect_workspace_files(project_id: str) -> list[str]:
    """Return the list of file paths from the project workspace manifest.

    Args:
        project_id: Project identifier.

    Returns:
        List of relative file paths recorded in the workspace manifest.
        Empty list if manifest cannot be read.
    """
    try:
        workspace_manager = await get_workspace_manager()
        manifest = await workspace_manager.get_manifest(project_id)
        return list(manifest.get("files", {}).keys())
    except Exception as exc:
        logger.warning(
            "qa_node_manifest_read_failed",
            project_id=project_id,
            error=str(exc),
        )
        return []
