"""In-process metrics counters for ai-dev-platform.

No external metrics server is required — all counters live in-process and are
served via ``GET /health`` (or any other endpoint that calls :func:`get_metrics`).

Tracked metrics
~~~~~~~~~~~~~~~
``tasks_completed_total``       — implementation tasks completed successfully
``tasks_failed_total``          — implementation tasks that errored or timed out
``llm_calls_total``             — total LLM API calls across all agents
``llm_tokens_used_total``       — cumulative prompt + completion tokens consumed
``sandbox_executions_total``    — Docker sandbox invocations
``sandbox_timeouts_total``      — sandbox invocations that hit the timeout limit
``per_agent_task_counts``       — ``{agent_name: completed_count}`` by agent

Public API
~~~~~~~~~~
:func:`increment`   — thread-safe counter increment (value defaults to 1).
:func:`get_metrics` — snapshot dict (safe for JSON serialisation).
:func:`reset_metrics` — zero all counters (primarily for testing).
"""

from __future__ import annotations

import copy
import threading
from typing import Any

# ---------------------------------------------------------------------------
# Known metrics and their zero values
# ---------------------------------------------------------------------------

#: Supported top-level counter names.
KNOWN_METRICS: frozenset[str] = frozenset(
    {
        "tasks_completed_total",
        "tasks_failed_total",
        "llm_calls_total",
        "llm_tokens_used_total",
        "sandbox_executions_total",
        "sandbox_timeouts_total",
    }
)

#: Task-related metric names — when these are incremented with an ``agent``
#: label, ``per_agent_task_counts[agent]`` is also updated.
_TASK_METRICS: frozenset[str] = frozenset(
    {"tasks_completed_total", "tasks_failed_total"}
)

# ---------------------------------------------------------------------------
# Mutable state (module-level singleton)
# ---------------------------------------------------------------------------

_lock = threading.Lock()

_counters: dict[str, int] = {name: 0 for name in KNOWN_METRICS}
_per_agent: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def increment(
    metric: str,
    labels: dict[str, Any] | None = None,
    value: int = 1,
) -> None:
    """Atomically increment a counter by *value* (default 1).

    Handles two special cases:
    - ``metric == "per_agent_task_counts"`` with ``labels={"agent": name}``
      increments ``per_agent_task_counts[name]`` directly.
    - Any metric in :data:`_TASK_METRICS` with ``labels={"agent": name}``
      additionally increments ``per_agent_task_counts[name]``.

    Unknown metric names are accepted (stored as extra keys in the snapshot) so
    callers can define ad-hoc counters without modifying this module.

    Args:
        metric: Counter name (see :data:`KNOWN_METRICS` for the standard set).
        labels: Optional key/value annotations.  Currently active keys:
                ``"agent"`` — agent name used for per-agent breakdown.
        value:  Amount to add (default ``1``).

    Raises:
        ValueError: If *value* is not a positive integer.
    """
    if value < 1:
        raise ValueError(f"increment value must be >= 1, got {value!r}")

    labels = labels or {}
    agent: str | None = labels.get("agent")

    with _lock:
        if metric == "per_agent_task_counts":
            # Direct per-agent increment
            if agent:
                _per_agent[agent] = _per_agent.get(agent, 0) + value
            # Don't touch top-level counters
            return

        # Increment the named counter (create on first use for ad-hoc metrics)
        _counters[metric] = _counters.get(metric, 0) + value

        # Side-effect: keep per-agent breakdown in sync for task metrics
        if agent and metric in _TASK_METRICS:
            _per_agent[agent] = _per_agent.get(agent, 0) + value


def get_metrics() -> dict[str, Any]:
    """Return a deep-copied snapshot of all current counter values.

    The snapshot is safe to serialise to JSON and will not change even if
    :func:`increment` is called concurrently after this function returns.

    Returns:
        Dict with all top-level counter keys plus ``"per_agent_task_counts"``.

    Example::

        {
            "tasks_completed_total": 42,
            "tasks_failed_total": 3,
            "llm_calls_total": 120,
            "llm_tokens_used_total": 98350,
            "sandbox_executions_total": 17,
            "sandbox_timeouts_total": 1,
            "per_agent_task_counts": {
                "backend_agent": 25,
                "frontend_agent": 17,
            },
        }
    """
    with _lock:
        snapshot = copy.copy(_counters)
        snapshot["per_agent_task_counts"] = copy.copy(_per_agent)
    return snapshot


def reset_metrics() -> None:
    """Reset all counters to zero.

    Intended for use in tests only.  Not safe to call in production while
    concurrent requests are in-flight.
    """
    with _lock:
        for key in list(_counters.keys()):
            _counters[key] = 0
        _per_agent.clear()
