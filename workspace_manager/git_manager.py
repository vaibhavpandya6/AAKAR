"""Git automation and version control management."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog
from git import Repo, GitCommandError

from config import settings

logger = structlog.get_logger()


# ============================================================================
# Exceptions
# ============================================================================


class MergeConflictError(Exception):
    """Raised when merge conflict is detected."""

    pass


class GitError(Exception):
    """Base exception for Git operations."""

    pass


# ============================================================================
# GitManager
# ============================================================================


class GitManager:
    """Manages Git operations for project workspaces."""

    def __init__(self):
        """Initialize Git manager."""
        self.base_path = Path(settings.workspace_base_path).resolve()

    def _get_repo_path(self, project_id: str) -> Path:
        """Get repository path for project.

        Args:
            project_id: Project identifier.

        Returns:
            Path to project repository.
        """
        repo_path = self.base_path / project_id
        if not repo_path.exists():
            raise FileNotFoundError(f"Workspace not found for project {project_id}")
        return repo_path

    def _get_repo(self, project_id: str) -> Repo:
        """Get Git repository object.

        Args:
            project_id: Project identifier.

        Returns:
            GitPython Repo object.

        Raises:
            GitError: If repository not found or invalid.
        """
        try:
            repo_path = self._get_repo_path(project_id)
            return Repo(repo_path)
        except Exception as e:
            logger.error(
                "Failed to access repository",
                project_id=project_id,
                error=str(e),
            )
            raise GitError(f"Repository error for {project_id}: {str(e)}") from e

    def _get_main_branch(self, repo: Repo):
        """Get the main/master branch from a repository.

        Handles GitPython's IterableList by searching for branch by name.
        Creates initial commit if repository is empty.

        Args:
            repo: GitPython Repo object.

        Returns:
            Branch reference (Head object).

        Raises:
            GitError: If no main or master branch found.
        """
        # Search for main or master branch by name
        for branch in repo.heads:
            if branch.name == "main":
                return branch
        for branch in repo.heads:
            if branch.name == "master":
                return branch

        # No branches exist - need to create initial commit first
        if len(repo.heads) == 0:
            # Check if HEAD is valid (has any commits)
            try:
                repo.head.commit
            except ValueError:
                # No commits exist - create an initial empty commit
                logger.info("Creating initial commit for empty repository")
                repo.index.commit("Initial commit: workspace created")

            # Now create main branch
            main_branch = repo.create_head("main")
            main_branch.checkout()
            return main_branch

        # Fall back to first branch if neither main nor master found
        return repo.heads[0]

    def init(self, project_id: str) -> None:
        """Initialize Git repository for project.

        Args:
            project_id: Project identifier.

        Raises:
            GitError: If initialization fails.
        """
        try:
            repo_path = self._get_repo_path(project_id)

            # Initialize repository
            repo = Repo.init(repo_path)

            # Set user config for commits
            with repo.config_writer() as git_config:
                git_config.set_value("user", "name", "ai-dev-platform")
                git_config.set_value("user", "email", "platform@aidev.local")

            # Create initial commit with manifest
            manifest_file = repo_path / "workspace.manifest.json"
            if manifest_file.exists():
                repo.index.add([str(manifest_file)])
                repo.index.commit("Initial commit: project workspace initialized")

            logger.info("Git repository initialized", project_id=project_id)

        except Exception as e:
            logger.error(
                "Failed to initialize repository",
                project_id=project_id,
                error=str(e),
            )
            raise GitError(f"Failed to initialize repo for {project_id}") from e

    def create_task_branch(
        self, project_id: str, agent_name: str, task_id: str
    ) -> str:
        """Create task-specific branch for agent.

        Args:
            project_id: Project identifier.
            agent_name: Name of agent.
            task_id: Task identifier.

        Returns:
            Branch name in format: agent/{agent_name}/task-{task_id}

        Raises:
            GitError: If branch creation fails.
        """
        try:
            repo = self._get_repo(project_id)
            branch_name = f"agent/{agent_name}/task-{task_id}"

            # Create branch from main/master
            main_branch = self._get_main_branch(repo)

            # Create new branch
            new_branch = repo.create_head(branch_name, main_branch)
            new_branch.checkout()

            logger.info(
                "Task branch created",
                project_id=project_id,
                branch_name=branch_name,
                agent=agent_name,
                task_id=task_id,
            )

            return branch_name

        except Exception as e:
            logger.error(
                "Failed to create task branch",
                project_id=project_id,
                agent_name=agent_name,
                task_id=task_id,
                error=str(e),
            )
            raise GitError(f"Failed to create branch for task {task_id}") from e

    def commit(
        self,
        project_id: str,
        branch: str,
        task_id: str,
        task_title: str,
        agent_name: str,
    ) -> str:
        """Commit changes with structured message.

        Args:
            project_id: Project identifier.
            branch: Branch name.
            task_id: Task identifier.
            task_title: Task title/description.
            agent_name: Name of agent committing.

        Returns:
            Commit hash.

        Raises:
            GitError: If commit fails.
        """
        try:
            repo = self._get_repo(project_id)

            # Ensure we're on correct branch
            repo.heads[branch].checkout()

            # Build structured commit message
            commit_message = f"[{agent_name}] task #{task_id}: {task_title}\n\nCorrelation: {project_id}:{task_id}"

            # Stage all changes (use git command directly for -A flag)
            repo.git.add(A=True)

            # Commit
            if repo.index.diff("HEAD"):
                commit = repo.index.commit(commit_message)
                commit_hash = commit.hexsha[:8]

                logger.info(
                    "Changes committed",
                    project_id=project_id,
                    branch=branch,
                    commit_hash=commit_hash,
                    agent=agent_name,
                    task_id=task_id,
                )

                return commit_hash
            else:
                logger.debug(
                    "No changes to commit",
                    project_id=project_id,
                    branch=branch,
                )
                return ""

        except Exception as e:
            logger.error(
                "Failed to commit changes",
                project_id=project_id,
                branch=branch,
                error=str(e),
            )
            raise GitError(f"Commit failed for branch {branch}") from e

    def merge_to_main(self, project_id: str, branch: str) -> bool:
        """Merge branch to main, detecting conflicts without auto-resolution.

        Args:
            project_id: Project identifier.
            branch: Branch to merge.

        Returns:
            True if merge successful, False if conflicts detected.

        Raises:
            MergeConflictError: If merge conflicts are detected.
            GitError: If merge operation fails.
        """
        try:
            repo = self._get_repo(project_id)
            repo_path = self._get_repo_path(project_id)

            # Ensure clean state before merge
            try:
                repo.git.merge(abort=True)
            except GitCommandError:
                pass  # No merge in progress

            # Determine main branch
            main_branch = self._get_main_branch(repo)

            # Checkout main
            main_branch.checkout(force=True)

            # Get source branch
            source_branch = repo.heads[branch]

            # Use git merge command directly (handles conflicts better)
            try:
                # Perform merge with --no-ff to preserve history
                repo.git.merge(branch, no_ff=True, m=f"Merge branch '{branch}' into {main_branch.name}")

                logger.info(
                    "Branch merged successfully",
                    project_id=project_id,
                    branch=branch,
                    target=main_branch.name,
                )

                return True

            except GitCommandError as e:
                # Check if it's a conflict or "already merged" case
                if "conflict" in str(e).lower():
                    # Check if it's only manifest conflicts (auto-resolvable)
                    manifest_path = repo_path / "workspace.manifest.json"
                    if self._try_auto_resolve_manifest(repo, manifest_path, branch, main_branch.name):
                        return True

                    logger.error(
                        "Merge conflict detected",
                        project_id=project_id,
                        branch=branch,
                        error=str(e),
                    )
                    # Abort merge to leave repo in clean state
                    try:
                        repo.git.merge(abort=True)
                    except GitCommandError:
                        pass
                    raise MergeConflictError(str(e)) from e
                elif "already up to date" in str(e).lower():
                    logger.info(
                        "Branch already merged",
                        project_id=project_id,
                        branch=branch,
                    )
                    return False
                raise

        except MergeConflictError:
            raise
        except Exception as e:
            logger.error(
                "Merge operation failed",
                project_id=project_id,
                branch=branch,
                error=str(e),
            )
            raise GitError(f"Merge failed: {str(e)}") from e

    def _try_auto_resolve_manifest(
        self, repo: Repo, manifest_path: Path, branch: str, target: str
    ) -> bool:
        """Auto-resolve workspace.manifest.json conflicts by merging JSON.

        Combines file entries from both sides of the merge.

        Args:
            repo: Git repository
            manifest_path: Path to workspace.manifest.json
            branch: Source branch being merged
            target: Target branch (usually main/master)

        Returns:
            True if conflict was auto-resolved, False if manual intervention needed
        """
        import json

        try:
            # Check what files are in conflict
            status = repo.git.status(porcelain=True)
            if "workspace.manifest.json" not in status:
                return False

            # Check if ONLY manifest is conflicted (allow auto-resolve)
            unmerged_files = [
                line.split(" ")[-1]
                for line in status.split("\n")
                if line.startswith("UU ")  # Both modified (conflict)
            ]

            # Read both versions of the manifest
            ours = repo.git.show(f"HEAD:workspace.manifest.json")
            theirs = repo.git.show(f"{branch}:workspace.manifest.json")

            ours_data = json.loads(ours)
            theirs_data = json.loads(theirs)

            # Merge file dictionaries (union of files from both)
            merged_files = {**ours_data.get("files", {}), **theirs_data.get("files", {})}

            # Create merged manifest (keep metadata from ours)
            merged_data = {
                "project_id": ours_data.get("project_id"),
                "created_at": ours_data.get("created_at"),
                "files": merged_files,
            }

            # Write resolved version
            manifest_path.write_text(json.dumps(merged_data, indent=2))

            # Stage resolved file
            repo.index.add(["workspace.manifest.json"])

            # If there are other conflicts, don't auto-commit
            if len(unmerged_files) > 1:
                logger.info(
                    "Manifest auto-resolved but other conflicts remain",
                    unmerged_files=unmerged_files,
                )
                return False

            # Commit the merge
            repo.index.commit(f"Merge branch '{branch}' into {target}\n\nAuto-resolved workspace.manifest.json")

            logger.info(
                "Auto-resolved manifest conflict",
                branch=branch,
                target=target,
            )
            return True

        except Exception as e:
            logger.warning("Failed to auto-resolve manifest", error=str(e))
            return False

    def tag(self, project_id: str, tag_name: str, message: str = "") -> None:
        """Create annotated tag at current commit.

        Args:
            project_id: Project identifier.
            tag_name: Tag name.
            message: Optional tag message.

        Raises:
            GitError: If tagging fails.
        """
        try:
            repo = self._get_repo(project_id)

            # Create annotated tag
            repo.create_tag(
                tag_name,
                ref=repo.head.commit,
                message=message or f"Release {tag_name}",
            )

            logger.info(
                "Tag created",
                project_id=project_id,
                tag=tag_name,
            )

        except Exception as e:
            logger.error(
                "Failed to create tag",
                project_id=project_id,
                tag=tag_name,
                error=str(e),
            )
            raise GitError(f"Failed to create tag {tag_name}") from e

    def create_release_branch(self, project_id: str) -> str:
        """Create release branch with timestamp.

        Args:
            project_id: Project identifier.

        Returns:
            Release branch name in format: release/{project_id}-{timestamp}

        Raises:
            GitError: If branch creation fails.
        """
        try:
            repo = self._get_repo(project_id)

            # Get timestamp
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            branch_name = f"release/{project_id}-{timestamp}"

            # Create branch from main
            main_branch = self._get_main_branch(repo)

            release_branch = repo.create_head(branch_name, main_branch)
            release_branch.checkout()

            logger.info(
                "Release branch created",
                project_id=project_id,
                branch=branch_name,
            )

            return branch_name

        except Exception as e:
            logger.error(
                "Failed to create release branch",
                project_id=project_id,
                error=str(e),
            )
            raise GitError(f"Failed to create release branch") from e

    def rollback_to_tag(self, project_id: str, tag: str) -> None:
        """Rollback repository to specified tag.

        Args:
            project_id: Project identifier.
            tag: Tag name to rollback to.

        Raises:
            GitError: If rollback fails.
        """
        try:
            repo = self._get_repo(project_id)

            # Verify tag exists
            if tag not in [t.name for t in repo.tags]:
                raise ValueError(f"Tag '{tag}' not found")

            # Get tag commit
            tag_commit = repo.commit(tag)

            # Reset to tag
            repo.head.reset(tag_commit, index=True, working_tree=True)

            logger.warning(
                "Repository rolled back to tag",
                project_id=project_id,
                tag=tag,
                commit=tag_commit.hexsha[:8],
            )

        except Exception as e:
            logger.error(
                "Failed to rollback",
                project_id=project_id,
                tag=tag,
                error=str(e),
            )
            raise GitError(f"Rollback to {tag} failed") from e

    def get_diff(self, project_id: str, branch: str) -> str:
        """Get diff between branch and main.

        Args:
            project_id: Project identifier.
            branch: Branch to compare.

        Returns:
            Diff string.

        Raises:
            GitError: If diff retrieval fails.
        """
        try:
            repo = self._get_repo(project_id)

            # Determine main branch
            main_branch = self._get_main_branch(repo)

            # Get diff
            diffs = main_branch.commit.diff(branch)

            # Build readable diff
            diff_str = ""
            for diff in diffs:
                diff_str += f"--- {diff.a_path}\n+++ {diff.b_path}\n"
                diff_str += str(diff) + "\n"

            logger.debug(
                "Diff retrieved",
                project_id=project_id,
                branch=branch,
                size=len(diff_str),
            )

            return diff_str

        except Exception as e:
            logger.error(
                "Failed to get diff",
                project_id=project_id,
                branch=branch,
                error=str(e),
            )
            raise GitError(f"Failed to get diff for {branch}") from e
