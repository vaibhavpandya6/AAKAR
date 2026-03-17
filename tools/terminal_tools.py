"""Terminal command execution tools for agents."""

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Dict

import structlog

from config import settings

logger = logging.getLogger(__name__)
struct_logger = structlog.get_logger()

# Dangerous command patterns to block
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"dd\s+if=/dev/",
    r"mkfs\.",
    r"sudo\s+",
    r"su\s+-",
    r":(){.*:",  # fork bomb
]

# Curl whitelist for external tools
ALLOWED_CURL_DOMAINS = [
    "github.com",
    "githubusercontent.com",
    "npmjs.com",
    "pypi.org",
    "api.github.com",
]


def _validate_working_directory(cwd: str) -> Path:
    """Validate working directory is within workspace.

    Args:
        cwd: Working directory path.

    Returns:
        Resolved Path object.

    Raises:
        ValueError: If directory escapes workspace boundaries.
    """
    workspace_base = Path(settings.workspace_base_path).resolve()
    cwd_path = Path(cwd).resolve()

    try:
        cwd_path.relative_to(workspace_base)
    except ValueError:
        logger.error(
            "Working directory outside workspace",
            cwd=cwd,
            workspace_base=str(workspace_base),
        )
        raise ValueError(f"Working directory must be under {workspace_base}")

    return cwd_path


def _validate_command(command: str) -> None:
    """Validate command for dangerous patterns.

    Args:
        command: Shell command to validate.

    Raises:
        ValueError: If command matches dangerous patterns.
    """
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            logger.warning("Dangerous command blocked", command=command[:50])
            raise ValueError(f"Command contains blocked pattern: {pattern}")

    # Special validation for curl
    if "curl" in command:
        # Check if domain is whitelisted
        domain_matches = re.findall(r"://([^/]+)", command)
        for domain in domain_matches:
            if not any(allowed in domain for allowed in ALLOWED_CURL_DOMAINS):
                logger.warning(
                    "Curl to non-whitelisted domain blocked",
                    domain=domain,
                    command=command[:50],
                )
                raise ValueError(f"Curl to domain '{domain}' not allowed")


async def run_command(
    command: str, cwd: str, timeout: int = 30
) -> Dict[str, any]:
    """Execute shell command in workspace with safety checks.

    Args:
        command: Shell command to execute.
        cwd: Working directory (must be under WORKSPACE_BASE_PATH).
        timeout: Command timeout in seconds.

    Returns:
        Dictionary with keys:
        - stdout: Standard output
        - stderr: Standard error
        - returncode: Exit code
        - duration_ms: Execution time in milliseconds
        - timed_out: Boolean indicating timeout

    Raises:
        ValueError: If command is dangerous or cwd is invalid.
    """
    await struct_logger.ainfo(
        "command_execution_started",
        command=command[:100],
        cwd=cwd,
        timeout=timeout,
    )

    # Validate directory and command
    try:
        cwd_path = _validate_working_directory(cwd)
        _validate_command(command)
    except ValueError as e:
        await struct_logger.aerror(
            "command_validation_failed",
            command=command[:50],
            error=str(e),
        )
        raise

    start_time = time.time()
    timed_out = False
    stdout = ""
    stderr = ""
    returncode = -1

    try:
        # Create subprocess
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd_path),
        )

        # Wait with timeout
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            returncode = process.returncode
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            await process.wait()
            stderr = f"Command timed out after {timeout} seconds"
            logger.warning("Command timeout", command=command[:50])

    except Exception as e:
        stderr = str(e)
        logger.error("Command execution error", error=str(e), command=command[:50])

    duration_ms = int((time.time() - start_time) * 1000)

    result = {
        "stdout": stdout,
        "stderr": stderr,
        "returncode": returncode,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
    }

    await struct_logger.ainfo(
        "command_execution_completed",
        command=command[:100],
        returncode=returncode,
        duration_ms=duration_ms,
        timed_out=timed_out,
    )

    return result


async def install_package(
    package: str, manager: str, cwd: str
) -> Dict[str, any]:
    """Install package using npm or pip.

    Args:
        package: Package name to install.
        manager: Package manager ("npm" or "pip").
        cwd: Working directory for installation.

    Returns:
        Result dictionary from run_command.

    Raises:
        ValueError: If manager is unsupported.
    """
    if manager not in ["npm", "pip"]:
        raise ValueError(f"Unsupported package manager: {manager}")

    if manager == "npm":
        command = f"npm install {package}"
    else:  # pip
        command = f"pip install {package}"

    await struct_logger.ainfo(
        "package_installation_started",
        package=package,
        manager=manager,
        cwd=cwd,
    )

    result = await run_command(command, cwd, timeout=120)

    await struct_logger.ainfo(
        "package_installation_completed",
        package=package,
        manager=manager,
        returncode=result["returncode"],
    )

    return result
