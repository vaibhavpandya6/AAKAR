"""Worker process manager — starts and manages all agent workers.

Run all workers together:
    python workers.py

Or run specific workers:
    python workers.py --workers backend,frontend

The manager coordinates worker processes and handles graceful shutdown.
"""

import argparse
import asyncio
import signal
import sys
from typing import Dict, List, Optional, Set

import structlog

from agents.backend_agent.worker import BackendWorker
from agents.database_agent.worker import DatabaseWorker
from agents.frontend_agent.worker import FrontendWorker
from agents.qa_agent.worker import QAWorker

logger = structlog.get_logger()

# Available worker types
AVAILABLE_WORKERS = {
    "backend": BackendWorker,
    "frontend": FrontendWorker,
    "database": DatabaseWorker,
    "qa": QAWorker,
}

# Default worker set (all workers)
DEFAULT_WORKERS = list(AVAILABLE_WORKERS.keys())


class WorkerManager:
    """Manages multiple agent worker processes."""

    def __init__(self, worker_types: List[str]) -> None:
        self.worker_types = worker_types
        self.workers: Dict[str, object] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.running = False
        self.log = logger.bind(component="worker_manager")

    async def start_all(self) -> None:
        """Initialize and start all configured workers."""
        await self.log.ainfo(
            "worker_manager_starting",
            worker_types=self.worker_types,
        )

        # Initialize all workers
        for worker_type in self.worker_types:
            if worker_type not in AVAILABLE_WORKERS:
                await self.log.aerror(
                    "unknown_worker_type",
                    worker_type=worker_type,
                    available=list(AVAILABLE_WORKERS.keys()),
                )
                continue

            worker_class = AVAILABLE_WORKERS[worker_type]
            worker = worker_class()

            try:
                await worker.initialize()
                self.workers[worker_type] = worker
                await self.log.ainfo(
                    "worker_initialized",
                    worker_type=worker_type,
                )
            except Exception as exc:
                await self.log.aerror(
                    "worker_initialization_failed",
                    worker_type=worker_type,
                    error=str(exc),
                )
                await worker.shutdown() if hasattr(worker, 'shutdown') else None
                continue

        if not self.workers:
            await self.log.aerror("no_workers_initialized")
            return

        # Start all worker tasks
        self.running = True
        for worker_type, worker in self.workers.items():
            task = asyncio.create_task(
                worker.run(),
                name=f"worker-{worker_type}"
            )
            self.tasks[worker_type] = task
            await self.log.ainfo(
                "worker_task_started",
                worker_type=worker_type,
            )

        await self.log.ainfo(
            "all_workers_started",
            count=len(self.workers),
        )

    async def stop_all(self) -> None:
        """Gracefully shutdown all worker processes."""
        await self.log.ainfo("worker_manager_stopping")
        self.running = False

        # Shutdown workers
        for worker_type, worker in self.workers.items():
            try:
                await worker.shutdown()
                await self.log.ainfo(
                    "worker_shutdown",
                    worker_type=worker_type,
                )
            except Exception as exc:
                await self.log.aerror(
                    "worker_shutdown_failed",
                    worker_type=worker_type,
                    error=str(exc),
                )

        # Cancel tasks
        for worker_type, task in self.tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                await self.log.ainfo(
                    "worker_task_cancelled",
                    worker_type=worker_type,
                )

        self.workers.clear()
        self.tasks.clear()
        await self.log.ainfo("worker_manager_stopped")

    async def wait_for_completion(self) -> None:
        """Wait for all worker tasks to complete or fail."""
        if not self.tasks:
            return

        await self.log.ainfo("waiting_for_workers")

        try:
            # Wait for any task to complete (should run forever)
            done, pending = await asyncio.wait(
                self.tasks.values(),
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Log which worker(s) finished unexpectedly
            for task in done:
                worker_type = next(
                    (name for name, t in self.tasks.items() if t == task),
                    "unknown"
                )

                if task.exception():
                    await self.log.aerror(
                        "worker_task_failed",
                        worker_type=worker_type,
                        error=str(task.exception()),
                    )
                else:
                    await self.log.awarning(
                        "worker_task_completed_unexpectedly",
                        worker_type=worker_type,
                    )

        except Exception as exc:
            await self.log.aerror(
                "wait_for_completion_error",
                error=str(exc),
            )


# Global worker manager for signal handling
_manager: Optional[WorkerManager] = None


def handle_shutdown_signal(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    global _manager
    if _manager:
        asyncio.create_task(_manager.stop_all())


async def main():
    """Entry point for worker manager."""
    global _manager

    # Setup structured logging
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="AI Development Platform Worker Manager")
    parser.add_argument(
        "--workers",
        type=str,
        default=",".join(DEFAULT_WORKERS),
        help=f"Comma-separated list of workers to start. Available: {', '.join(AVAILABLE_WORKERS.keys())}",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set logging level",
    )

    args = parser.parse_args()

    # Parse worker types
    worker_types = [w.strip() for w in args.workers.split(",") if w.strip()]
    unknown_workers = set(worker_types) - set(AVAILABLE_WORKERS.keys())
    if unknown_workers:
        print(f"Error: Unknown worker types: {', '.join(unknown_workers)}")
        print(f"Available workers: {', '.join(AVAILABLE_WORKERS.keys())}")
        sys.exit(1)

    # Register signal handlers
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    manager = WorkerManager(worker_types)
    _manager = manager

    try:
        await manager.start_all()
        if manager.workers:
            await manager.wait_for_completion()

    except KeyboardInterrupt:
        await logger.ainfo("worker_manager_interrupted")
    except Exception as exc:
        await logger.aerror("worker_manager_fatal_error", error=str(exc))
        sys.exit(1)
    finally:
        await manager.stop_all()


if __name__ == "__main__":
    asyncio.run(main())