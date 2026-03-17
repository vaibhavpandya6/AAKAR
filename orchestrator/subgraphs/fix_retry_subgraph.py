"""Fix-retry subgraph — cycles bug reports through agent fixes and QA re-runs.

Handles the inner loop for a *single failing task*:

    bug found → backend agent applies fix → DockerSandbox re-runs tests
              → check retry limit → (loop | succeed | escalate to HITL)

The compiled subgraph is exported as ``fix_retry_subgraph`` and embedded
as a node inside the main platform orchestration graph.  The parent graph
invokes it by passing a ``FixRetryState`` slice derived from the first
entry in ``bug_reports``.
"""

import asyncio
import re
import time
import uuid
from typing import Any

import structlog
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from memory.long_term import get_long_term_memory
from messaging.message_bus import get_message_bus
from messaging.schemas import MessageType
from task_system.task_queue import TaskQueue
from tools.docker_executor import DockerSandbox

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

_ORCHESTRATOR_STREAM = "stream:orchestrator"
_FIX_CONSUMER_GROUP = "fix-retry-workers"
_FIX_CONSUMER = "fix-retry-node"

_MAX_FIX_WAIT_SECONDS = 300     # 5-minute hard timeout waiting for the agent
_POLL_TIMEOUT_MS = 2_000        # block per XREADGROUP call
_POLL_COUNT = 10                # messages to fetch per iteration

# Docker image + pytest command (mirrors QAAgent._PYTEST_CMD)
_PYTHON_TEST_IMAGE = "python:3.11-alpine"
_PYTEST_CMD = (
    "pip install pytest pytest-asyncio httpx --quiet --no-cache-dir "
    "&& cd /app/workspace && python -m pytest tests/ -v --tb=short 2>&1"
)
_PYTEST_CMD_WITH_DEPS = (
    "pip install -r /app/workspace/requirements.txt --quiet --no-cache-dir 2>/dev/null || true "
    "&& pip install pytest pytest-asyncio httpx --quiet --no-cache-dir "
    "&& cd /app/workspace && python -m pytest tests/ -v --tb=short 2>&1"
)

# pytest output patterns (self-contained; mirrors QAAgent to avoid import cycles)
_RE_PASSED = re.compile(r"(\d+)\s+passed")
_RE_FAILED = re.compile(r"(\d+)\s+failed")
_RE_ERROR = re.compile(r"(\d+)\s+error")
_RE_FAIL_LINE = re.compile(
    r"^FAILED\s+([\w/.]+::[\w]+)\s+-\s+(.+)$", re.MULTILINE
)


# ---------------------------------------------------------------------------
# Subgraph state
# ---------------------------------------------------------------------------


class FixRetryState(TypedDict):
    """Isolated state for the fix-retry inner loop.

    Scoped to a single failing task.  The parent graph is responsible for
    extracting this slice from ``PlatformState.bug_reports`` and merging
    results back after the subgraph exits.
    """

    # Task context
    project_id: str
    task_id: str            # ID of the original implementation task

    # Bug information fed to the fixer
    bug_report: list[dict]  # Active list of unresolved bugs

    # Retry bookkeeping
    retry_count: int
    max_retries: int        # Ceiling; default 3

    # Result flags written by nodes
    fix_applied: bool       # Did the backend agent acknowledge completion?
    tests_passing: bool     # Did the re-run sandbox exit cleanly?
    escalate_to_human: bool # Should a human review this task?


# ---------------------------------------------------------------------------
# Node: apply_fix_node
# ---------------------------------------------------------------------------


async def apply_fix_node(state: FixRetryState) -> dict[str, Any]:
    """Store bugs in long-term memory, then enqueue a fix task to the backend agent.

    Workflow:
    1. Persist every bug to :class:`~memory.long_term.LongTermMemory` so that
       future agents can learn from this failure pattern.
    2. Build a targeted fix task whose description contains the full bug report.
    3. Enqueue the task to the backend agent stream via
       :class:`~task_system.task_queue.TaskQueue`.
    4. Poll ``stream:orchestrator`` for a ``TASK_COMPLETE`` or ``TASK_FAILED``
       message whose ``correlation_id`` matches ``{project_id}:{fix_task_id}``
       (5-minute hard timeout, 2-second read blocks).

    Args:
        state: Current FixRetryState.

    Returns:
        Partial state: ``fix_applied`` (bool) and ``retry_count`` incremented by 1.
    """
    project_id: str = state["project_id"]
    task_id: str = state["task_id"]
    bug_report: list[dict] = state.get("bug_report") or []
    retry_count: int = state.get("retry_count", 0)

    logger.info(
        "apply_fix_node_start",
        project_id=project_id,
        task_id=task_id,
        bug_count=len(bug_report),
        attempt=retry_count + 1,
    )

    # ── 1. Persist bugs to long-term memory ──────────────────────────────────
    try:
        ltm = get_long_term_memory()
        for bug in bug_report:
            severity = bug.get("severity", "medium")
            description = bug.get("description", "unknown error")
            suggestion = bug.get("suggestion", "see bug report")
            # Store all severity levels so patterns accumulate over time
            await ltm.store_fix(
                task_id=task_id,
                error=description,
                fix=suggestion,
                agent="fix-retry-subgraph",
            )
    except Exception as exc:
        # Non-fatal: long-term memory failure should not block the fix attempt
        logger.warning("apply_fix_ltm_store_failed", project_id=project_id, error=str(exc))

    # ── 2. Build the fix task ─────────────────────────────────────────────────
    fix_task_id = f"fix_{task_id}_r{retry_count}_{uuid.uuid4().hex[:6]}"

    bug_lines: list[str] = []
    for idx, bug in enumerate(bug_report, 1):
        bug_lines.append(
            f"{idx}. [{bug.get('severity', 'medium').upper()}] "
            f"{bug.get('description', 'No description')}\n"
            f"   File   : {bug.get('file', 'unknown')}\n"
            f"   Line   : {bug.get('line', 'N/A')}\n"
            f"   Fix hint: {bug.get('suggestion', 'No suggestion provided')}"
        )

    fix_description = (
        f"Fix the following bugs discovered during QA for task `{task_id}` "
        f"(fix attempt {retry_count + 1}).\n\n"
        f"Bug report:\n" + "\n\n".join(bug_lines) + "\n\n"
        "Requirements for the fix:\n"
        "- Resolve each bug listed above.\n"
        "- Do NOT break any existing passing tests.\n"
        "- Keep changes minimal and targeted to the reported issues.\n"
        "- No debug statements, no hardcoded values, no commented-out code.\n"
        "- All fixes must be production-safe and follow the project's existing patterns."
    )

    fix_task: dict[str, Any] = {
        "id": fix_task_id,
        "title": f"Fix: task {task_id} (attempt {retry_count + 1})",
        "description": fix_description,
        "skill_required": "backend",
        "depends_on": [],
        "acceptance_criteria": [
            "All bugs listed in the bug report are resolved.",
            "All previously passing tests continue to pass after the fix.",
            "No new linting or type errors are introduced.",
        ],
        # Metadata so the agent knows which original task this fixes
        "original_task_id": task_id,
        "bug_report": bug_report,
    }

    # ── 3. Enqueue to backend agent stream ───────────────────────────────────
    queue = TaskQueue()
    try:
        await queue.enqueue(project_id, fix_task)
        logger.info(
            "fix_task_enqueued",
            project_id=project_id,
            fix_task_id=fix_task_id,
            original_task_id=task_id,
        )
    except Exception as exc:
        logger.error(
            "fix_task_enqueue_failed",
            project_id=project_id,
            fix_task_id=fix_task_id,
            error=str(exc),
        )
        return {
            "fix_applied": False,
            "retry_count": retry_count + 1,
        }

    # ── 4. Poll orchestrator stream for agent completion ─────────────────────
    fix_applied = await _await_agent_result(project_id, fix_task_id)

    logger.info(
        "apply_fix_node_complete",
        project_id=project_id,
        fix_task_id=fix_task_id,
        fix_applied=fix_applied,
        new_retry_count=retry_count + 1,
    )

    return {
        "fix_applied": fix_applied,
        "retry_count": retry_count + 1,
    }


# ---------------------------------------------------------------------------
# Node: rerun_tests_node
# ---------------------------------------------------------------------------


async def rerun_tests_node(state: FixRetryState) -> dict[str, Any]:
    """Re-run the full test suite in a fresh Docker sandbox.

    Re-uses the test files already written to ``tests/`` during the initial
    QA pass.  Does not regenerate tests — only re-executes them against the
    (hopefully fixed) source code.

    Retries once with dependency installation if zero tests are collected
    on the first run (handles cases where the sandbox image is missing
    ``requirements.txt`` dependencies).

    Args:
        state: Current FixRetryState.

    Returns:
        Partial state: ``tests_passing`` (bool) and a fresh ``bug_report``
        containing any remaining failures (empty list on full pass).
    """
    project_id: str = state["project_id"]
    task_id: str = state["task_id"]
    retry_count: int = state.get("retry_count", 0)

    logger.info(
        "rerun_tests_node_start",
        project_id=project_id,
        task_id=task_id,
        retry_count=retry_count,
    )

    sandbox = DockerSandbox()
    sandbox_task_id = f"{task_id}_rerun_r{retry_count}"

    # ── Run tests ─────────────────────────────────────────────────────────────
    try:
        result = await sandbox.run(
            project_id=project_id,
            task_id=sandbox_task_id,
            command=_PYTEST_CMD,
            image=_PYTHON_TEST_IMAGE,
        )
    except Exception as exc:
        logger.error(
            "rerun_tests_sandbox_error",
            project_id=project_id,
            task_id=task_id,
            error=str(exc),
        )
        return {
            "tests_passing": False,
            "bug_report": [
                {
                    "severity": "high",
                    "description": f"Test sandbox execution failed: {exc}",
                    "file": "sandbox",
                    "line": None,
                    "suggestion": (
                        "Check Docker daemon health and confirm the sandbox image "
                        f"({_PYTHON_TEST_IMAGE}) is available."
                    ),
                }
            ],
        }

    stdout: str = result.get("stdout", "")
    stderr: str = result.get("stderr", "")
    timed_out: bool = result.get("timed_out", False)
    exit_code: int = result.get("exit_code", -1)

    # ── Retry with dep install if zero tests collected ────────────────────────
    if _total_tests(stdout + stderr) == 0 and not timed_out:
        logger.warning(
            "rerun_tests_zero_collected_retrying",
            project_id=project_id,
            task_id=task_id,
        )
        try:
            result = await sandbox.run(
                project_id=project_id,
                task_id=f"{sandbox_task_id}_dep_retry",
                command=_PYTEST_CMD_WITH_DEPS,
                image=_PYTHON_TEST_IMAGE,
            )
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            timed_out = result.get("timed_out", False)
            exit_code = result.get("exit_code", -1)
        except Exception as exc:
            logger.warning("rerun_tests_dep_retry_failed", error=str(exc))

    # ── Parse test output ─────────────────────────────────────────────────────
    combined = stdout + "\n" + stderr
    passed = _re_int(_RE_PASSED, combined)
    failed = _re_int(_RE_FAILED, combined)
    errors = _re_int(_RE_ERROR, combined)

    failed_tests = [
        {"name": m.group(1), "reason": m.group(2).strip()}
        for m in _RE_FAIL_LINE.finditer(combined)
    ]

    tests_passing = (
        failed == 0
        and errors == 0
        and not timed_out
        and exit_code == 0
        and (passed > 0 or _total_tests(combined) == 0)
    )

    logger.info(
        "rerun_tests_node_complete",
        project_id=project_id,
        task_id=task_id,
        passed=passed,
        failed=failed,
        errors=errors,
        timed_out=timed_out,
        tests_passing=tests_passing,
    )

    # ── Build updated bug report from remaining failures ──────────────────────
    new_bug_report: list[dict] = []

    for fail in failed_tests:
        new_bug_report.append(
            {
                "severity": "high",
                "description": (
                    f"Test still failing after fix: {fail['name']} — {fail['reason']}"
                ),
                "file": fail["name"].split("::")[0],
                "line": None,
                "suggestion": "Investigate why the applied fix did not resolve this failure.",
            }
        )

    if timed_out:
        new_bug_report.append(
            {
                "severity": "high",
                "description": (
                    "Test container timed out after the applied fix — "
                    "possible infinite loop or unresolved blocking call."
                ),
                "file": "tests/",
                "line": None,
                "suggestion": (
                    "Add timeout guards; audit async code for blocking calls "
                    "and resource leaks introduced by the fix."
                ),
            }
        )

    return {
        "tests_passing": tests_passing,
        "bug_report": new_bug_report,
    }


# ---------------------------------------------------------------------------
# Node: check_retry_limit_node
# ---------------------------------------------------------------------------


def check_retry_limit_node(state: FixRetryState) -> dict[str, Any]:
    """Gate node that sets ``escalate_to_human`` when retries are exhausted.

    Consulted by the conditional edge after every test re-run.  Does not
    by itself terminate the graph — routing is handled by
    :func:`_route_after_check`.

    Args:
        state: Current FixRetryState.

    Returns:
        Partial state: ``escalate_to_human`` (bool).
    """
    retry_count: int = state.get("retry_count", 0)
    max_retries: int = state.get("max_retries", 3)
    escalate = retry_count >= max_retries

    logger.info(
        "check_retry_limit",
        retry_count=retry_count,
        max_retries=max_retries,
        escalate_to_human=escalate,
        tests_passing=state.get("tests_passing", False),
    )

    return {"escalate_to_human": escalate}


# ---------------------------------------------------------------------------
# Routing function
# ---------------------------------------------------------------------------


def _route_after_check(state: FixRetryState) -> str:
    """Select the next graph node after the retry-limit gate.

    Priority (evaluated top to bottom):

    1. ``tests_passing`` → ``"success"`` → :data:`~langgraph.graph.END`
    2. ``escalate_to_human`` → ``"escalate"`` → :data:`~langgraph.graph.END`
    3. Otherwise → ``"retry"`` → back to ``apply_fix``

    Args:
        state: Current FixRetryState (after check_retry_limit_node ran).

    Returns:
        One of ``"success"``, ``"escalate"``, or ``"retry"``.
    """
    if state.get("tests_passing"):
        logger.info("fix_retry_route_success", task_id=state.get("task_id"))
        return "success"

    if state.get("escalate_to_human"):
        logger.warning(
            "fix_retry_route_escalate",
            task_id=state.get("task_id"),
            retry_count=state.get("retry_count"),
        )
        return "escalate"

    logger.info(
        "fix_retry_route_retry",
        task_id=state.get("task_id"),
        retry_count=state.get("retry_count"),
    )
    return "retry"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

_subgraph: StateGraph = StateGraph(FixRetryState)

_subgraph.add_node("apply_fix", apply_fix_node)
_subgraph.add_node("rerun_tests", rerun_tests_node)
_subgraph.add_node("check_retry_limit", check_retry_limit_node)

_subgraph.set_entry_point("apply_fix")

_subgraph.add_edge("apply_fix", "rerun_tests")
_subgraph.add_edge("rerun_tests", "check_retry_limit")
_subgraph.add_conditional_edges(
    "check_retry_limit",
    _route_after_check,
    {
        "success": END,     # tests passed — exit cleanly
        "escalate": END,    # out of retries — exit as escalation for HITL
        "retry": "apply_fix",  # still failing — loop back
    },
)

#: Compiled subgraph — import and embed in the main StateGraph as a node:
#:
#:   main_graph.add_node("fix_retry", fix_retry_subgraph)
fix_retry_subgraph = _subgraph.compile()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _await_agent_result(project_id: str, task_id: str) -> bool:
    """Poll ``stream:orchestrator`` until the given fix task resolves.

    Blocks until a ``TASK_COMPLETE`` or ``TASK_FAILED`` message arrives
    with ``correlation_id == "{project_id}:{task_id}"``, or until
    :data:`_MAX_FIX_WAIT_SECONDS` elapse.

    Acknowledges every matched message before returning so it is not
    redelivered to other consumers.

    Args:
        project_id: Project identifier.
        task_id: Fix task ID to wait for.

    Returns:
        ``True`` on ``TASK_COMPLETE``, ``False`` on ``TASK_FAILED`` or timeout.
    """
    expected_cid = f"{project_id}:{task_id}"
    message_bus = await get_message_bus()

    # Idempotent group creation (catches BUSYGROUP internally)
    await message_bus.create_consumer_group(
        _ORCHESTRATOR_STREAM, _FIX_CONSUMER_GROUP, start_id="0"
    )

    deadline = time.monotonic() + _MAX_FIX_WAIT_SECONDS

    while True:
        if time.monotonic() > deadline:
            logger.warning(
                "apply_fix_wait_timeout",
                project_id=project_id,
                task_id=task_id,
                timeout_seconds=_MAX_FIX_WAIT_SECONDS,
            )
            return False

        try:
            messages = await message_bus.consume(
                _ORCHESTRATOR_STREAM,
                _FIX_CONSUMER_GROUP,
                _FIX_CONSUMER,
                count=_POLL_COUNT,
                timeout=_POLL_TIMEOUT_MS,
            )
        except Exception as exc:
            logger.warning("apply_fix_consume_error", error=str(exc))
            await asyncio.sleep(1)
            continue

        for msg in messages:
            if msg.message_type not in (
                MessageType.TASK_COMPLETE,
                MessageType.TASK_FAILED,
            ):
                # Not relevant to us — leave it for other consumers
                continue

            if msg.correlation_id != expected_cid:
                continue

            # Acknowledge so the message leaves the PEL for this group
            try:
                await message_bus.acknowledge(
                    _ORCHESTRATOR_STREAM, _FIX_CONSUMER_GROUP, msg.message_id
                )
            except Exception as exc:
                logger.warning("apply_fix_ack_failed", error=str(exc))

            if msg.message_type == MessageType.TASK_COMPLETE:
                logger.info(
                    "apply_fix_agent_complete",
                    project_id=project_id,
                    task_id=task_id,
                )
                return True

            # TASK_FAILED
            logger.warning(
                "apply_fix_agent_failed",
                project_id=project_id,
                task_id=task_id,
                error=msg.payload.get("error", "unknown"),
            )
            return False


def _re_int(pattern: re.Pattern, text: str) -> int:
    """Extract the first integer captured by ``pattern`` from ``text``.

    Returns 0 if no match is found.

    Args:
        pattern: Compiled regex with one capturing group.
        text: Text to search.

    Returns:
        Captured integer value, or 0.
    """
    m = pattern.search(text)
    return int(m.group(1)) if m else 0


def _total_tests(combined: str) -> int:
    """Return sum of passed + failed + error counts from pytest output.

    Args:
        combined: Concatenated stdout + stderr from the test run.

    Returns:
        Total test count (0 if no tests were collected).
    """
    return (
        _re_int(_RE_PASSED, combined)
        + _re_int(_RE_FAILED, combined)
        + _re_int(_RE_ERROR, combined)
    )
