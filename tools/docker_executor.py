"""Docker sandbox execution for untrusted code."""

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Dict, Optional

import structlog

from config import settings

logger = logging.getLogger(__name__)
struct_logger = structlog.get_logger()


class DockerSandbox:
    """Manages Docker sandbox containers for safe code execution.

    This is the ONLY place Docker is used in the entire codebase.
    """

    def __init__(self):
        """Initialize Docker sandbox."""
        self.image_name = settings.sandbox_image or "node:18-alpine"
        self.workspace_base = Path(settings.workspace_base_path)

    async def _run_docker_command(self, args: list) -> tuple[str, str, int]:
        """Execute docker CLI command.

        Args:
            args: Docker command arguments.

        Returns:
            Tuple of (stdout, stderr, returncode).
        """
        cmd = ["docker"] + args
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        return (
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
            process.returncode,
        )

    async def build_sandbox_image(self) -> bool:
        """Build sandbox Docker image from Dockerfile.sandbox.

        Returns:
            True if successful, False otherwise.
        """
        # Check if custom image from settings starts with docker registry syntax
        if self.image_name and not self.image_name.startswith(("node:", "python:", "alpine")):
            # External image, assume it exists
            logger.info(
                "Using external sandbox image",
                image=self.image_name,
            )
            return True

        # Check if image already exists
        stdout, stderr, code = await self._run_docker_command(
            ["images", "-q", self.image_name]
        )
        if code == 0 and stdout.strip():
            logger.debug("Sandbox image already exists", image=self.image_name)
            return True

        # Build image from Dockerfile
        dockerfile_path = Path(__file__).parent.parent / "sandbox" / "Dockerfile.sandbox"
        if not dockerfile_path.exists():
            logger.error(
                "Dockerfile.sandbox not found",
                path=str(dockerfile_path),
            )
            return False

        try:
            await struct_logger.ainfo(
                "sandbox_image_build_started",
                image=self.image_name,
                dockerfile=str(dockerfile_path),
            )

            stdout, stderr, code = await self._run_docker_command(
                [
                    "build",
                    "-t",
                    self.image_name,
                    "-f",
                    str(dockerfile_path),
                    str(dockerfile_path.parent),
                ]
            )

            if code == 0:
                await struct_logger.ainfo(
                    "sandbox_image_build_completed",
                    image=self.image_name,
                )
                return True
            else:
                logger.error(
                    "Failed to build sandbox image",
                    stderr=stderr[:500],
                )
                return False

        except Exception as e:
            logger.error(
                "Error building sandbox image",
                error=str(e),
            )
            return False

    async def run(
        self, project_id: str, task_id: str, command: str, image: Optional[str] = None
    ) -> Dict[str, any]:
        """Run command in ephemeral Docker sandbox.

        Args:
            project_id: Project identifier.
            task_id: Task identifier.
            command: Command to execute in container.
            image: Optional Docker image (uses default if not provided).

        Returns:
            Dictionary with keys:
            - stdout: Standard output
            - stderr: Standard error
            - exit_code: Container exit code
            - duration_ms: Execution time
            - timed_out: Boolean indicating timeout

        Security flags used:
        - --rm: Auto-destroy container
        - --user 1000:1000: Non-root user
        - --read-only: Immutable filesystem
        - --tmpfs: Temporary writable mount
        - --network=none: No network access
        - --cpus/--memory: Resource limits
        - --security-opt seccomp: Syscall filtering
        - -v (read-only): Workspace mount
        """
        image_to_use = image or self.image_name

        await struct_logger.ainfo(
            "sandbox_execution_started",
            project_id=project_id,
            task_id=task_id,
            image=image_to_use,
        )

        # Validate workspace exists
        workspace_path = self.workspace_base / project_id
        if not workspace_path.exists():
            logger.error(
                "Workspace not found for sandbox",
                project_id=project_id,
            )
            return {
                "stdout": "",
                "stderr": f"Workspace not found for project {project_id}",
                "exit_code": -1,
                "duration_ms": 0,
                "timed_out": False,
            }

        # Build Docker run arguments
        container_name = f"sandbox-{project_id}-{task_id}-{int(time.time())}"

        seccomp_path = Path(__file__).parent.parent / "security" / "sandbox_seccomp.json"
        if not seccomp_path.exists():
            logger.warning(
                "Seccomp profile not found, running without it",
                path=str(seccomp_path),
            )
            seccomp_arg = []
        else:
            seccomp_arg = ["--security-opt", f"seccomp={str(seccomp_path)}"]

        docker_args = [
            "run",
            "--rm",
            "--name",
            container_name,
            "--user",
            "1000:1000",
            "--read-only",
            "--tmpfs",
            "/app/workspace:rw,size=256m",
            "--network",
            "none",
            "--cpus",
            settings.sandbox_cpu_limit,
            "--memory",
            settings.sandbox_memory_limit,
            "-v",
            f"{str(workspace_path)}:/app/workspace:ro",
        ] + seccomp_arg + [
            image_to_use,
            "/bin/sh",
            "-c",
            command,
        ]

        start_time = time.time()
        timeout = settings.sandbox_timeout_seconds
        timed_out = False
        stdout = ""
        stderr = ""
        exit_code = -1

        try:
            # Run container with timeout
            try:
                stdout, stderr, exit_code = await asyncio.wait_for(
                    self._run_docker_command(docker_args),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                timed_out = True
                # Kill the container
                await self._run_docker_command(["kill", container_name])
                stderr = f"Container execution timed out after {timeout} seconds"
                logger.warning(
                    "Sandbox timeout",
                    project_id=project_id,
                    task_id=task_id,
                    container=container_name,
                )

        except Exception as e:
            stderr = f"Docker execution error: {str(e)}"
            logger.error(
                "Sandbox execution error",
                project_id=project_id,
                error=str(e),
            )

        duration_ms = int((time.time() - start_time) * 1000)

        result = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "timed_out": timed_out,
        }

        await struct_logger.ainfo(
            "sandbox_execution_completed",
            project_id=project_id,
            task_id=task_id,
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
        )

        return result
