"""Workspace isolation and file management."""

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import redis.asyncio as redis

from config import settings

logger = logging.getLogger(__name__)


# ============================================================================
# Exceptions
# ============================================================================


class FileLockTimeout(Exception):
    """Raised when file lock cannot be acquired within timeout."""

    pass


class PathTraversalError(Exception):
    """Raised when path attempts to escape workspace directory."""

    pass


# ============================================================================
# WorkspaceManager
# ============================================================================


class WorkspaceManager:
    """Manages isolated project workspaces with atomic file operations."""

    LOCK_TIMEOUT_SEC = 5
    LOCK_TTL_MS = 30000

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        """Initialize workspace manager.

        Args:
            redis_client: Optional Redis client for file locking.
        """
        self.redis = redis_client
        self.base_path = Path(settings.workspace_base_path)

    async def ensure_initialized(self):
        """Ensure Redis connection is initialized."""
        if not self.redis:
            self.redis = await redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )

    async def create_workspace(self, project_id: str) -> Path:
        """Create isolated workspace for project.

        Args:
            project_id: Unique project identifier.

        Returns:
            Path to created workspace root directory.
        """
        workspace_path = self.base_path / project_id
        workspace_path.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        for subdir in ["backend", "frontend", "tests", "migrations"]:
            (workspace_path / subdir).mkdir(exist_ok=True)

        # Create manifest
        manifest = {
            "project_id": project_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": {},
        }

        manifest_path = workspace_path / "workspace.manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        logger.info(
            "Workspace created",
            project_id=project_id,
            path=str(workspace_path),
        )

        return workspace_path

    def _get_workspace_path(self, project_id: str) -> Path:
        """Get workspace root path for project.

        Args:
            project_id: Project identifier.

        Returns:
            Path to workspace.
        """
        workspace_path = self.base_path / project_id
        if not workspace_path.exists():
            raise FileNotFoundError(f"Workspace not found for project {project_id}")
        return workspace_path

    def _validate_path(self, workspace_path: Path, file_path: str) -> Path:
        """Validate file path to prevent traversal attacks.

        Args:
            workspace_path: Root workspace path.
            file_path: Relative file path to validate.

        Returns:
            Resolved absolute path.

        Raises:
            PathTraversalError: If path escapes workspace.
        """
        # Normalize and resolve path
        target = (workspace_path / file_path).resolve()
        workspace_resolved = workspace_path.resolve()

        # Ensure target is within workspace
        try:
            target.relative_to(workspace_resolved)
        except ValueError:
            logger.error(
                "Path traversal attempt detected",
                file_path=file_path,
                workspace=str(workspace_resolved),
            )
            raise PathTraversalError(
                f"Path '{file_path}' escapes workspace boundaries"
            )

        return target

    def _normalize_path_for_lock(self, file_path: str) -> str:
        """Normalize path for use as lock key.

        Args:
            file_path: File path to normalize.

        Returns:
            Normalized path string.
        """
        return str(Path(file_path)).replace("\\", "/")

    async def acquire_file_lock(
        self, project_id: str, file_path: str, ttl_ms: int = LOCK_TTL_MS
    ) -> bool:
        """Acquire exclusive file lock using Redis.

        Args:
            project_id: Project identifier.
            file_path: Path to file within workspace.
            ttl_ms: Lock time-to-live in milliseconds.

        Returns:
            True if lock acquired, False if timeout.

        Raises:
            PathTraversalError: If path is invalid.
        """
        await self.ensure_initialized()

        # Validate path
        workspace_path = self._get_workspace_path(project_id)
        self._validate_path(workspace_path, file_path)

        normalized_path = self._normalize_path_for_lock(file_path)
        lock_key = f"filelock:{project_id}:{normalized_path}"

        # Try to acquire lock with timeout
        start_time = time.time()
        while time.time() - start_time < self.LOCK_TIMEOUT_SEC:
            try:
                # SET NX PX: Set if not exists, with TTL
                acquired = await self.redis.set(
                    lock_key,
                    "locked",
                    nx=True,
                    px=ttl_ms,
                )

                if acquired:
                    logger.debug(
                        "File lock acquired",
                        project_id=project_id,
                        file_path=file_path,
                        lock_key=lock_key,
                    )
                    return True

                # Lock not acquired, wait before retry
                await asyncio.sleep(0.1)

            except Exception as e:
                logger.error(
                    "Error acquiring file lock",
                    lock_key=lock_key,
                    error=str(e),
                )
                raise

        logger.warning(
            "File lock timeout",
            project_id=project_id,
            file_path=file_path,
            lock_key=lock_key,
        )
        return False

    async def release_file_lock(self, project_id: str, file_path: str) -> None:
        """Release file lock.

        Args:
            project_id: Project identifier.
            file_path: Path to file within workspace.
        """
        await self.ensure_initialized()

        normalized_path = self._normalize_path_for_lock(file_path)
        lock_key = f"filelock:{project_id}:{normalized_path}"

        try:
            await self.redis.delete(lock_key)
            logger.debug(
                "File lock released",
                project_id=project_id,
                file_path=file_path,
            )
        except Exception as e:
            logger.error(
                "Error releasing file lock",
                lock_key=lock_key,
                error=str(e),
            )

    async def write_file_atomic(
        self,
        project_id: str,
        file_path: str,
        content: str,
        agent: str,
        task_id: str,
    ) -> None:
        """Write file atomically with lock and manifest tracking.

        Args:
            project_id: Project identifier.
            file_path: Path to file within workspace.
            content: File content to write.
            agent: Name of agent writing file.
            task_id: Task ID associated with write.

        Raises:
            FileLockTimeout: If lock cannot be acquired.
            PathTraversalError: If path is invalid.
        """
        workspace_path = self._get_workspace_path(project_id)
        target_path = self._validate_path(workspace_path, file_path)

        # Acquire lock
        if not await self.acquire_file_lock(project_id, file_path):
            raise FileLockTimeout(
                f"Could not acquire lock for '{file_path}' in project {project_id}"
            )

        try:
            # Ensure parent directories exist
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # Write to temporary file first
            tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
            tmp_path.write_text(content, encoding="utf-8")

            # Atomic rename
            tmp_path.replace(target_path)

            # Calculate hash
            content_hash = hashlib.sha256(content.encode()).hexdigest()

            # Update manifest
            await self._update_manifest(
                project_id,
                file_path,
                agent,
                task_id,
                content_hash,
            )

            logger.info(
                "File written atomically",
                project_id=project_id,
                file_path=file_path,
                agent=agent,
                task_id=task_id,
            )

        finally:
            # Always release lock
            await self.release_file_lock(project_id, file_path)

    async def _update_manifest(
        self,
        project_id: str,
        file_path: str,
        agent: str,
        task_id: str,
        content_hash: str,
    ) -> None:
        """Update workspace manifest with file entry.

        Args:
            project_id: Project identifier.
            file_path: Relative file path.
            agent: Agent name.
            task_id: Task ID.
            content_hash: SHA256 hash of content.
        """
        workspace_path = self._get_workspace_path(project_id)
        manifest_path = workspace_path / "workspace.manifest.json"

        # Read current manifest
        manifest = json.loads(manifest_path.read_text())

        # Update entry
        manifest["files"][file_path] = {
            "agent": agent,
            "task_id": task_id,
            "sha256": content_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Write manifest
        manifest_path.write_text(json.dumps(manifest, indent=2))

    async def read_file(self, project_id: str, file_path: str) -> str:
        """Read file from workspace.

        Args:
            project_id: Project identifier.
            file_path: Path to file within workspace.

        Returns:
            File content as string.

        Raises:
            PathTraversalError: If path is invalid.
            FileNotFoundError: If file doesn't exist.
        """
        workspace_path = self._get_workspace_path(project_id)
        target_path = self._validate_path(workspace_path, file_path)

        if not target_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = target_path.read_text(encoding="utf-8")
        logger.debug(
            "File read",
            project_id=project_id,
            file_path=file_path,
            size=len(content),
        )
        return content

    async def list_files(self, project_id: str) -> List[str]:
        """List all files in workspace (excluding subdirs and manifest).

        Args:
            project_id: Project identifier.

        Returns:
            List of relative file paths.
        """
        workspace_path = self._get_workspace_path(project_id)
        files = []

        for file_path in workspace_path.rglob("*"):
            if file_path.is_file() and file_path.name != "workspace.manifest.json":
                # Get relative path
                rel_path = file_path.relative_to(workspace_path)
                files.append(str(rel_path).replace("\\", "/"))

        logger.debug("Files listed", project_id=project_id, count=len(files))
        return sorted(files)

    async def get_manifest(self, project_id: str) -> Dict:
        """Get workspace manifest.

        Args:
            project_id: Project identifier.

        Returns:
            Manifest dictionary.
        """
        workspace_path = self._get_workspace_path(project_id)
        manifest_path = workspace_path / "workspace.manifest.json"

        manifest = json.loads(manifest_path.read_text())
        logger.debug("Manifest retrieved", project_id=project_id)
        return manifest


# Global workspace manager instance
_workspace_manager_instance: Optional[WorkspaceManager] = None


async def get_workspace_manager() -> WorkspaceManager:
    """Get or create global workspace manager instance.

    Returns:
        WorkspaceManager instance.
    """
    global _workspace_manager_instance
    if _workspace_manager_instance is None:
        _workspace_manager_instance = WorkspaceManager()
        await _workspace_manager_instance.ensure_initialized()
    return _workspace_manager_instance
