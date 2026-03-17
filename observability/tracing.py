"""OpenTelemetry distributed tracing for ai-dev-platform.

Provides:
- ``init_tracing(service_name)`` — one-shot OTel SDK setup (OTLP or console).
- ``get_tracer(name)``           — returns an OTel :class:`Tracer` (or stub).
- ``trace_node(node_name)``      — decorator for LangGraph node functions.
- ``trace_agent_call(...)``      — decorator for agent ``execute()`` methods.

OTLP configuration (all optional):
    ``OTLP_ENDPOINT``   — gRPC exporter endpoint, e.g. ``http://localhost:4317``
    ``OTLP_HEADERS``    — comma-separated ``key=value`` pairs for auth headers
    ``OTEL_SERVICE_NAME`` — overrides the ``service_name`` arg passed to ``init_tracing``

If ``OTLP_ENDPOINT`` is absent **and** the ``opentelemetry-sdk`` package is not
installed, all decorators fall through to no-ops so the application boots cleanly
in environments without a tracing back-end.

Span attributes emitted on every span:
    ``project_id``, ``task_id``, ``agent_name``, ``node_name``
"""

from __future__ import annotations

import functools
import os
from typing import Any, Callable

import structlog

logger = structlog.get_logger().bind(logger_name="observability.tracing")

# ---------------------------------------------------------------------------
# Optional OpenTelemetry import — degrade gracefully if not installed
# ---------------------------------------------------------------------------

try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.trace import StatusCode

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    StatusCode = None  # type: ignore[assignment]

try:
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    _OTLP_AVAILABLE = True
except ImportError:
    _OTLP_AVAILABLE = False


# ---------------------------------------------------------------------------
# No-op stubs (used when OTel SDK is absent)
# ---------------------------------------------------------------------------


class _NoOpSpan:
    """Minimal span stub that satisfies the context-manager contract."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D401
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


class _NoOpTracer:
    """Minimal tracer stub returned when OTel SDK is not installed."""

    def start_as_current_span(
        self,
        name: str,
        **kwargs: Any,
    ) -> _NoOpSpan:  # type: ignore[override]
        return _NoOpSpan()


# ---------------------------------------------------------------------------
# Tracer registry
# ---------------------------------------------------------------------------

_provider: Any = None  # TracerProvider | None


def init_tracing(service_name: str) -> None:
    """Initialise the OpenTelemetry SDK with the configured exporter.

    Safe to call multiple times — only the first call has any effect.

    Exporter selection (in priority order):

    1. **OTLP gRPC** — if ``OTLP_ENDPOINT`` env var is set *and*
       ``opentelemetry-exporter-otlp-proto-grpc`` is installed.
    2. **Console** — if the OTel SDK is installed but no endpoint is
       configured (useful for local debugging; set ``OTEL_CONSOLE=1``).
    3. **No-op** — OTel SDK not installed; all spans are discarded.

    Args:
        service_name: Logical service name embedded in every span's resource.
                      Can be overridden by the ``OTEL_SERVICE_NAME`` env var.
    """
    global _provider

    if _provider is not None:
        return  # already initialised

    if not _OTEL_AVAILABLE:
        logger.warning(
            "tracing_otel_unavailable",
            detail="opentelemetry-sdk not installed; tracing disabled.",
        )
        return

    effective_name = os.getenv("OTEL_SERVICE_NAME", service_name)
    resource = Resource.create({SERVICE_NAME: effective_name})
    provider = TracerProvider(resource=resource)

    otlp_endpoint = os.getenv("OTLP_ENDPOINT", "").strip()

    if otlp_endpoint and _OTLP_AVAILABLE:
        # Parse optional auth headers: "key1=v1,key2=v2"
        raw_headers = os.getenv("OTLP_HEADERS", "")
        headers: dict[str, str] = {}
        for pair in raw_headers.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                headers[k.strip()] = v.strip()

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, headers=headers or None)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("tracing_otlp_configured", endpoint=otlp_endpoint, service=effective_name)

    elif os.getenv("OTEL_CONSOLE", "").strip() in ("1", "true", "yes"):
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("tracing_console_configured", service=effective_name)

    else:
        # No exporter — provider is still registered so span context propagates
        # correctly inside the process (useful for correlation IDs).
        logger.info("tracing_no_exporter", service=effective_name)

    _otel_trace.set_tracer_provider(provider)
    _provider = provider


def get_tracer(name: str) -> Any:
    """Return an OTel :class:`~opentelemetry.trace.Tracer` (or no-op stub).

    If ``init_tracing()`` has not been called or the OTel SDK is absent, a
    :class:`_NoOpTracer` is returned so callers need no conditional logic.

    Args:
        name: Instrumentation scope name (e.g. ``"orchestrator.nodes"``).

    Returns:
        A real OTel tracer, or :class:`_NoOpTracer`.
    """
    if not _OTEL_AVAILABLE:
        return _NoOpTracer()
    return _otel_trace.get_tracer(name)


# ---------------------------------------------------------------------------
# trace_node — decorator for LangGraph node functions
# ---------------------------------------------------------------------------


def trace_node(node_name: str) -> Callable:
    """Decorator that wraps a LangGraph node function in an OTel span.

    The decorated function must have the signature::

        async def my_node(state: dict) -> dict: ...

    Span attributes set automatically:
        - ``node_name``  — the ``node_name`` argument passed to this decorator
        - ``project_id`` — ``state["project_id"]``
        - ``task_id``    — ``state["current_task"]["id"]`` if present

    Args:
        node_name: Human-readable node identifier (e.g. ``"planner_node"``).

    Returns:
        Decorated async function.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(state: dict, *args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer("orchestrator.nodes")
            project_id: str = str(state.get("project_id") or "")
            current_task = state.get("current_task") or {}
            task_id: str = str(
                current_task.get("id") if isinstance(current_task, dict) else ""
            )

            with tracer.start_as_current_span(f"node.{node_name}") as span:
                span.set_attribute("node_name", node_name)
                span.set_attribute("project_id", project_id)
                span.set_attribute("task_id", task_id)
                span.set_attribute("agent_name", "")  # nodes are not agents

                try:
                    result = await fn(state, *args, **kwargs)
                    return result
                except Exception as exc:
                    span.record_exception(exc)
                    if StatusCode is not None:
                        span.set_status(StatusCode.ERROR, str(exc))
                    raise

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# trace_agent_call — decorator for agent execute() methods
# ---------------------------------------------------------------------------


def trace_agent_call(agent_name: str, task_id: str = "") -> Callable:
    """Decorator that wraps an agent's ``execute()`` method in an OTel span.

    Designed for instance methods with the signature::

        async def execute(self, task: dict, project_id: str, ...) -> dict: ...

    Span attributes set automatically:
        - ``agent_name`` — the ``agent_name`` argument passed to this decorator
        - ``task_id``    — ``task["id"]`` if ``task`` is a dict, else ``task_id``
        - ``project_id`` — second positional argument (``project_id: str``)
        - ``node_name``  — empty string (agent calls are not graph nodes)

    Args:
        agent_name: Human-readable agent identifier (e.g. ``"backend_agent"``).
        task_id:    Fallback task ID when ``task`` dict has no ``"id"`` key.

    Returns:
        Decorated async method.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(self: Any, task: Any, project_id: str = "", *args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer(f"agents.{agent_name}")

            effective_task_id: str = task_id
            if isinstance(task, dict):
                effective_task_id = str(task.get("id") or task_id)

            effective_project_id: str = str(project_id or "")

            with tracer.start_as_current_span(f"agent.{agent_name}.execute") as span:
                span.set_attribute("agent_name", agent_name)
                span.set_attribute("task_id", effective_task_id)
                span.set_attribute("project_id", effective_project_id)
                span.set_attribute("node_name", "")  # not a graph node

                try:
                    result = await fn(self, task, project_id, *args, **kwargs)
                    return result
                except Exception as exc:
                    span.record_exception(exc)
                    if StatusCode is not None:
                        span.set_status(StatusCode.ERROR, str(exc))
                    raise

        return wrapper

    return decorator
