"""Workspace isolation and Git automation."""

from workspace_manager.git_manager import GitManager, GitError, MergeConflictError
from workspace_manager.manager import (
    WorkspaceManager,
    FileLockTimeout,
    PathTraversalError,
    get_workspace_manager,
)

__all__ = [
    # Workspace Manager
    "WorkspaceManager",
    "FileLockTimeout",
    "PathTraversalError",
    "get_workspace_manager",
    # Git Manager
    "GitManager",
    "GitError",
    "MergeConflictError",
]
