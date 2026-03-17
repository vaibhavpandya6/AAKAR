"""Observability package — logging, tracing, and metrics for ai-dev-platform."""

from observability.logger import (
    AgentActionContext,
    configure_logging,
    get_logger,
    log_agent_action,
    request_id_var,
)
from observability.metrics import get_metrics, increment, reset_metrics
from observability.tracing import (
    get_tracer,
    init_tracing,
    trace_agent_call,
    trace_node,
)

__all__ = [
    # logger
    "configure_logging",
    "get_logger",
    "log_agent_action",
    "request_id_var",
    "AgentActionContext",
    # tracing
    "init_tracing",
    "get_tracer",
    "trace_node",
    "trace_agent_call",
    # metrics
    "increment",
    "get_metrics",
    "reset_metrics",
]
