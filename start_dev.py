"""Start workers alongside the API server — complete development startup.

This script starts both the FastAPI server and all agent workers together,
providing a complete development environment.

Usage:
    python start_dev.py

The script will:
1. Start Redis (if not already running)
2. Start all agent workers in parallel
3. Start the FastAPI API server
4. Handle graceful shutdown when interrupted
"""

import asyncio
import signal
import subprocess
import sys
import time
from typing import Optional

import structlog

logger = structlog.get_logger()

# Process references for cleanup
_api_process: Optional[subprocess.Popen] = None
_worker_process: Optional[subprocess.Popen] = None


def handle_shutdown_signal(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    logger.info("shutdown_signal_received", signal=signum)
    cleanup_processes()
    sys.exit(0)


def cleanup_processes():
    """Terminate all child processes gracefully."""
    global _api_process, _worker_process

    logger.info("cleaning_up_processes")

    if _worker_process:
        logger.info("terminating_workers")
        _worker_process.terminate()
        try:
            _worker_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("force_killing_workers")
            _worker_process.kill()

    if _api_process:
        logger.info("terminating_api_server")
        _api_process.terminate()
        try:
            _api_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("force_killing_api_server")
            _api_process.kill()

    logger.info("cleanup_complete")


def start_workers():
    """Start all agent workers using the worker manager."""
    global _worker_process

    logger.info("starting_agent_workers")

    _worker_process = subprocess.Popen(
        [sys.executable, "workers.py", "--log-level", "INFO"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Give workers a moment to initialize
    time.sleep(2)

    if _worker_process.poll() is not None:
        logger.error("workers_failed_to_start")
        return False

    logger.info("agent_workers_started", pid=_worker_process.pid)
    return True


def start_api_server():
    """Start the FastAPI server."""
    global _api_process

    logger.info("starting_api_server")

    _api_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app",
         "--reload", "--host", "0.0.0.0", "--port", "8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Give API server a moment to start
    time.sleep(3)

    if _api_process.poll() is not None:
        logger.error("api_server_failed_to_start")
        return False

    logger.info("api_server_started", pid=_api_process.pid)
    return True


def check_redis():
    """Verify Redis is available."""
    try:
        import redis
        client = redis.Redis(host='localhost', port=6379, decode_responses=True)
        client.ping()
        logger.info("redis_connection_ok")
        return True
    except Exception as exc:
        logger.error("redis_connection_failed", error=str(exc))
        logger.info("please_start_redis",
                   command="redis-server or docker run -p 6379:6379 redis:7")
        return False


def main():
    """Main entry point."""
    # Setup structured logging
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )

    # Register signal handlers
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    logger.info("starting_development_environment")

    # Check prerequisites
    if not check_redis():
        sys.exit(1)

    # Start workers first
    if not start_workers():
        logger.error("failed_to_start_workers")
        cleanup_processes()
        sys.exit(1)

    # Start API server
    if not start_api_server():
        logger.error("failed_to_start_api_server")
        cleanup_processes()
        sys.exit(1)

    logger.info("development_environment_ready",
               api_url="http://localhost:8000",
               docs_url="http://localhost:8000/docs")

    try:
        # Wait for processes
        while True:
            time.sleep(1)

            # Check if either process has died
            if _worker_process and _worker_process.poll() is not None:
                logger.error("workers_process_died")
                break

            if _api_process and _api_process.poll() is not None:
                logger.error("api_server_process_died")
                break

    except KeyboardInterrupt:
        logger.info("keyboard_interrupt_received")
    finally:
        cleanup_processes()


if __name__ == "__main__":
    main()