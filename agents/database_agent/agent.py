"""Database agent — generates Alembic migrations and schema changes."""

import re
from typing import Any, Dict, List, Tuple

import structlog

from agents.base_agent import BaseAgent
from agents.database_agent.prompts import SYSTEM_PROMPT, format_database_task_prompt
from workspace_manager.git_manager import GitManager

logger = structlog.get_logger()

# Alembic migration function signatures
_UPGRADE_PATTERN = re.compile(r"^\s*def\s+upgrade\s*\(\s*\)", re.MULTILINE)
_DOWNGRADE_PATTERN = re.compile(r"^\s*def\s+downgrade\s*\(\s*\)", re.MULTILINE)

# Unsafe SQL patterns — string-concatenated queries
_UNSAFE_CONCAT_PATTERN = re.compile(
    r"""(f['"]{1,3}(?:[^'"]*\bSELECT\b|[^'"]*\bINSERT\b|[^'"]*\bUPDATE\b|[^'"]*\bDELETE\b)[^'"]*['"]{1,3})""",
    re.IGNORECASE,
)


class MigrationValidationError(Exception):
    """Raised when a generated migration fails structural validation."""
    pass


class DatabaseAgent(BaseAgent):
    """Generates Alembic migration files for schema changes.

    Workflow:
        1. Retrieve RAG context (existing schema / prior migrations)
        2. Build prompt and call LLM
        3. Validate each migration: has upgrade() + downgrade(), no unsafe SQL
        4. Atomically write files to migrations/ subdir
        5. Commit on task branch
        6. Publish TASK_COMPLETE
    """

    MIGRATIONS_DIR = "migrations"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.git = GitManager()
        self.log = logger.bind(agent=self.agent_name, type="database")

    # ──────────────────────────────────────────────────────────────────────
    # Migration validation
    # ──────────────────────────────────────────────────────────────────────

    def _validate_migration(self, file_path: str, content: str) -> Tuple[bool, str]:
        """Validate an Alembic migration file.

        Checks:
        - Contains def upgrade() function
        - Contains def downgrade() function
        - No f-string SQL concatenation (injection risk)

        Args:
            file_path: Path of the migration file (for error reporting)
            content: File content string

        Returns:
            Tuple (is_valid: bool, message: str)
        """
        issues: List[str] = []

        # Must have upgrade()
        if not _UPGRADE_PATTERN.search(content):
            issues.append("Missing def upgrade() function")

        # Must have downgrade()
        if not _DOWNGRADE_PATTERN.search(content):
            issues.append("Missing def downgrade() function — migration is not reversible")

        # Warn (not fail) on f-string SQL
        unsafe_matches = _UNSAFE_CONCAT_PATTERN.findall(content)
        if unsafe_matches:
            issues.append(
                f"Potential SQL injection risk: found f-string SQL on "
                f"{len(unsafe_matches)} line(s): {unsafe_matches[:2]}"
            )

        if issues:
            return False, "; ".join(issues)
        return True, "OK"

    # ──────────────────────────────────────────────────────────────────────
    # Execute
    # ──────────────────────────────────────────────────────────────────────

    async def execute(self, task: Dict[str, Any], project_id: str) -> Dict[str, Any]:
        """Execute a database migration task end-to-end.

        Args:
            task: Task dict with id, title, description, etc.
            project_id: Project identifier

        Returns:
            Result dict with files_written, notes, branch
        """
        task_id = str(task["id"])
        task_title = task.get("title", "")
        task_description = task.get("description", "")

        await self.log.ainfo(
            "database_task_started",
            task_id=task_id,
            project_id=project_id,
            title=task_title,
        )

        try:
            # ── 1. RAG context (existing schema / migrations) ──────────────
            rag_chunks = await self.vector_store.retrieve(
                project_id=project_id,
                query=f"{task_title} {task_description} database schema migration",
                top_k=4,
            )
            rag_context = self._format_rag_context(rag_chunks)

            # ── 2. Build prompt ────────────────────────────────────────────
            user_prompt = format_database_task_prompt(
                task_title=task_title,
                task_description=task_description,
                db_type="PostgreSQL 15 with asyncpg driver",
                rag_context=rag_context,
            )

            # ── 3. Call LLM ────────────────────────────────────────────────
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )

            files: List[Dict[str, str]] = response.get("files", [])
            notes: str = response.get("notes", "")

            if not files:
                raise ValueError("LLM returned no files in response")

            # ── 4. Validate migrations before writing ──────────────────────
            for file_entry in files:
                file_path = file_entry.get("path", "")
                content = file_entry.get("content", "")

                # Only validate .py files that look like Alembic migrations
                if file_path.endswith(".py") and "migration" in file_path.lower():
                    valid, reason = self._validate_migration(file_path, content)
                    if not valid:
                        raise MigrationValidationError(
                            f"Migration validation failed for '{file_path}': {reason}"
                        )
                    await self.log.ainfo(
                        "migration_validated",
                        file_path=file_path,
                        task_id=task_id,
                    )

            # ── 5. Create task branch ──────────────────────────────────────
            branch = self.git.create_task_branch(
                project_id=project_id,
                agent_name=self.agent_name,
                task_id=task_id,
            )

            # ── 6. Write files atomically ──────────────────────────────────
            files_written: List[str] = []
            for file_entry in files:
                file_path = file_entry.get("path", "")
                content = file_entry.get("content", "")

                if not file_path or not content:
                    continue

                # Ensure all migration files land in the migrations/ dir
                if not file_path.startswith(self.MIGRATIONS_DIR):
                    file_path = f"{self.MIGRATIONS_DIR}/{file_path.lstrip('/')}"

                await self.workspace_manager.write_file_atomic(
                    project_id=project_id,
                    file_path=file_path,
                    content=content,
                    agent=self.agent_name,
                    task_id=task_id,
                )
                files_written.append(file_path)
                await self.log.ainfo(
                    "migration_file_written",
                    task_id=task_id,
                    file_path=file_path,
                )

            # ── 7. Commit ──────────────────────────────────────────────────
            self.git.commit(
                project_id=project_id,
                branch=branch,
                task_id=task_id,
                task_title=task_title,
                agent_name=self.agent_name,
            )

            # ── 8. Index migration files ───────────────────────────────────
            for file_path in files_written:
                try:
                    content = await self.workspace_manager.read_file(project_id, file_path)
                    await self.vector_store.index_file(
                        project_id=project_id,
                        file_path=file_path,
                        content=content,
                        task_id=task_id,
                    )
                except Exception as idx_err:
                    await self.log.awarning(
                        "index_file_failed",
                        file_path=file_path,
                        error=str(idx_err),
                    )

            # ── 9. Report complete ─────────────────────────────────────────
            await self.report_complete(
                task_id=task_id,
                project_id=project_id,
                files_written=files_written,
            )

            await self.log.ainfo(
                "database_task_completed",
                task_id=task_id,
                migrations=len(files_written),
            )
            return {"files_written": files_written, "notes": notes, "branch": branch}

        except MigrationValidationError as exc:
            # Store failed migration pattern in long-term memory
            await self.long_term_memory.store_fix(
                task_id=task_id,
                error=f"Migration validation failed: {exc}",
                fix="Ensure every migration has def upgrade() and def downgrade(). "
                    "Never use f-string SQL; use sa.text(':param') instead.",
                agent=self.agent_name,
            )
            await self.log.aerror(
                "migration_validation_failed",
                task_id=task_id,
                error=str(exc),
            )
            await self.report_failure(
                task_id=task_id,
                project_id=project_id,
                error=str(exc),
            )
            raise

        except Exception as exc:
            await self.log.aerror(
                "database_task_failed",
                task_id=task_id,
                project_id=project_id,
                error=str(exc),
            )
            await self.report_failure(
                task_id=task_id,
                project_id=project_id,
                error=str(exc),
            )
            raise
