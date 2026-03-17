"""File operation tools for agents."""

import logging
from typing import Dict

import structlog

from workspace_manager import get_workspace_manager

logger = logging.getLogger(__name__)
struct_logger = structlog.get_logger()


async def read_file(project_id: str, path: str) -> str:
    """Read file from project workspace.

    Args:
        project_id: Project identifier.
        path: Relative file path.

    Returns:
        File content as string.

    Raises:
        FileNotFoundError: If file doesn't exist.
    """
    await struct_logger.ainfo(
        "file_read_started",
        project_id=project_id,
        path=path,
    )

    try:
        ws_manager = await get_workspace_manager()
        content = await ws_manager.read_file(project_id, path)

        await struct_logger.ainfo(
            "file_read_completed",
            project_id=project_id,
            path=path,
            size=len(content),
        )
        return content

    except Exception as e:
        await struct_logger.aerror(
            "file_read_failed",
            project_id=project_id,
            path=path,
            error=str(e),
        )
        raise


async def write_file(
    project_id: str,
    path: str,
    content: str,
    agent: str,
    task_id: str,
) -> Dict[str, str]:
    """Write file to project workspace atomically.

    Args:
        project_id: Project identifier.
        path: Relative file path.
        content: File content to write.
        agent: Name of agent writing file.
        task_id: Task ID associated with write.

    Returns:
        Manifest entry dictionary with file metadata.

    Raises:
        FileLockTimeout: If lock unavailable.
        PathTraversalError: If path is invalid.
    """
    await struct_logger.ainfo(
        "file_write_started",
        project_id=project_id,
        path=path,
        agent=agent,
        task_id=task_id,
        size=len(content),
    )

    try:
        ws_manager = await get_workspace_manager()
        await ws_manager.write_file_atomic(
            project_id, path, content, agent, task_id
        )

        # Get manifest to return file entry
        manifest = await ws_manager.get_manifest(project_id)
        file_entry = manifest["files"].get(path, {})

        await struct_logger.ainfo(
            "file_write_completed",
            project_id=project_id,
            path=path,
            agent=agent,
            task_id=task_id,
        )

        return file_entry

    except Exception as e:
        await struct_logger.aerror(
            "file_write_failed",
            project_id=project_id,
            path=path,
            agent=agent,
            task_id=task_id,
            error=str(e),
        )
        raise


async def list_files(project_id: str) -> list[str]:
    """List all files in project workspace.

    Args:
        project_id: Project identifier.

    Returns:
        Sorted list of relative file paths.
    """
    await struct_logger.ainfo(
        "file_list_started",
        project_id=project_id,
    )

    try:
        ws_manager = await get_workspace_manager()
        files = await ws_manager.list_files(project_id)

        await struct_logger.ainfo(
            "file_list_completed",
            project_id=project_id,
            count=len(files),
        )

        return files

    except Exception as e:
        await struct_logger.aerror(
            "file_list_failed",
            project_id=project_id,
            error=str(e),
        )
        raise


async def file_exists(project_id: str, path: str) -> bool:
    """Check if file exists in project workspace.

    Args:
        project_id: Project identifier.
        path: Relative file path.

    Returns:
        True if file exists, False otherwise.
    """
    try:
        await read_file(project_id, path)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False
