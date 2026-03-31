"""Bootstrap agent worker — consumes bootstrap tasks from Redis and executes them.

Run this as a standalone process:
    python -m agents.bootstrap_agent.worker

The worker runs in an infinite loop, dequeuing tasks from stream:bootstrap_agent
and invoking the BootstrapAgent to generate configuration files.
"""

import asyncio
import logging
import signal
import sys
from typing import Optional

import structlog

# Configure structlog BEFORE importing any modules that use structlog.get_logger()
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

from agents.bootstrap_agent.agent import BootstrapAgent
from config.llm_factory import create_llm
from memory.long_term import LongTermMemory
from memory.vector_store import VectorStore
from messaging.message_bus import get_message_bus
from task_system.task_queue import TaskQueue
from workspace_manager import get_workspace_manager

logger = structlog.get_logger()

# Worker configuration
AGENT_NAME = "bootstrap-agent-1"
AGENT_STREAM = "stream:bootstrap_agent"
CONSUMER_GROUP = "workers"
POLL_TIMEOUT_MS = 5000
POLL_INTERVAL_SEC = 1.0


class BootstrapWorker:
    """Bootstrap agent worker process."""

    def __init__(self) -> None:
        self.agent: Optional[BootstrapAgent] = None
        self.task_queue: Optional[TaskQueue] = None
        self.running = False
        self.log = logger.bind(worker="bootstrap", agent_name=AGENT_NAME)

    async def initialize(self) -> None:
        """Initialize all dependencies."""
        await self.log.ainfo("worker_initializing", agent_name=AGENT_NAME)

        # Initialize message bus
        message_bus = await get_message_bus()

        # Create consumer group for bootstrap agent stream
        await message_bus.create_consumer_group(
            AGENT_STREAM,
            CONSUMER_GROUP,
            start_id="0",
        )

        # Initialize LLM
        llm = create_llm()

        # Initialize vector store for RAG
        vector_store = VectorStore()

        # Initialize long-term memory
        long_term_memory = LongTermMemory()

        # Initialize workspace manager
        workspace_manager = await get_workspace_manager()

        # Create agent instance
        self.agent = BootstrapAgent(
            agent_name=AGENT_NAME,
            llm=llm,
            vector_store=vector_store,
            long_term_memory=long_term_memory,
            message_bus=message_bus,
            workspace_manager=workspace_manager,
        )

        # Initialize task queue
        self.task_queue = TaskQueue()

        self.running = True
        await self.log.ainfo(
            "worker_ready",
            agent_name=AGENT_NAME,
            stream=AGENT_STREAM,
        )

    async def run(self) -> None:
        """Main worker loop — dequeue and execute tasks."""
        await self.log.ainfo("worker_starting", agent_name=AGENT_NAME)

        while self.running:
            try:
                # Dequeue next task from Redis stream
                task = await self.task_queue.dequeue(
                    agent_name=AGENT_NAME,
                    stream_key=AGENT_STREAM,
                    block_ms=POLL_TIMEOUT_MS,
                )

                if not task:
                    await asyncio.sleep(POLL_INTERVAL_SEC)
                    continue

                task_id = str(task.get("id", "unknown"))
                project_id = task.get("project_id", "")
                redis_id = task.get("_redis_id", "")

                await self.log.ainfo(
                    "task_received",
                    task_id=task_id,
                    project_id=project_id,
                    redis_id=redis_id,
                    title=task.get("title", "")[:80],
                )

                try:
                    result = await self.agent.execute(task, project_id)

                    await self.log.ainfo(
                        "task_completed_successfully",
                        task_id=task_id,
                        project_id=project_id,
                        files_written=len(result.get("files_written", [])),
                    )

                    await self.task_queue.mark_complete(
                        project_id=project_id,
                        task_id=task_id,
                        stream_key=AGENT_STREAM,
                        redis_id=redis_id,
                    )

                    await self.task_queue.merge_branch_after_completion(
                        project_id=project_id,
                        task_id=task_id,
                        agent_name=AGENT_NAME,
                    )

                except Exception as task_error:
                    await self.log.aerror(
                        "task_execution_failed",
                        task_id=task_id,
                        project_id=project_id,
                        error=str(task_error),
                    )

                    await self.task_queue.mark_failed(
                        project_id=project_id,
                        task_id=task_id,
                        stream_key=AGENT_STREAM,
                        redis_id=redis_id,
                        error=str(task_error),
                    )

            except asyncio.CancelledError:
                await self.log.ainfo("worker_cancelled", agent_name=AGENT_NAME)
                break
            except Exception as exc:
                await self.log.aerror(
                    "worker_loop_error",
                    agent_name=AGENT_NAME,
                    error=str(exc),
                )
                await asyncio.sleep(2.0)

        await self.log.ainfo("worker_stopped", agent_name=AGENT_NAME)

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        await self.log.ainfo("worker_shutting_down", agent_name=AGENT_NAME)
        self.running = False


_worker_instance: Optional[BootstrapWorker] = None


def handle_shutdown_signal(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    global _worker_instance
    if _worker_instance:
        asyncio.create_task(_worker_instance.shutdown())


async def main():
    """Entry point for bootstrap agent worker."""
    global _worker_instance

    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    worker = BootstrapWorker()
    _worker_instance = worker

    try:
        await worker.initialize()
        await worker.run()
    except KeyboardInterrupt:
        await worker.log.ainfo("worker_interrupted")
    except Exception as exc:
        await worker.log.aerror("worker_fatal_error", error=str(exc))
        sys.exit(1)
    finally:
        await worker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
