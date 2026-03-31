"""Bootstrap agent — generates project configuration files.

This agent is responsible for creating the essential configuration files
that make a project runnable: requirements.txt, package.json, .env.example,
docker-compose.yml, etc.
"""

import json
from typing import Any, Dict, List

import structlog

from agents.base_agent import BaseAgent
from agents.bootstrap_agent.prompts import SYSTEM_PROMPT, format_bootstrap_task_prompt
from workspace_manager.git_manager import GitManager

logger = structlog.get_logger()


class BootstrapAgent(BaseAgent):
    """Generates project configuration and bootstrap files.

    Workflow:
        1. Analyze project summary and task DAG to detect stack
        2. Check for existing configuration files
        3. Call LLM to generate missing configs
        4. Validate generated configs
        5. Write files atomically
        6. Commit to task branch
        7. Publish TASK_COMPLETE
    """

    # Configuration files this agent is responsible for
    CONFIG_FILE_PATTERNS = {
        # Python
        "requirements.txt",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Pipfile",
        # Node.js
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "tsconfig.json",
        # Environment
        ".env.example",
        ".env.template",
        ".env.sample",
        # Docker
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        ".dockerignore",
        # Git
        ".gitignore",
        # Misc
        "Makefile",
        "README.md",
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.git = GitManager()
        self.log = logger.bind(agent=self.agent_name, type="bootstrap")

    async def execute(self, task: Dict[str, Any], project_id: str) -> Dict[str, Any]:
        """Execute a bootstrap task to generate configuration files.

        Args:
            task: Task dict with project context
            project_id: Project identifier

        Returns:
            Result dict with files_written, notes, branch
        """
        task_id = str(task["id"])
        task_title = task.get("title", "Generate configuration files")
        task_description = task.get("description", "")

        await self.log.ainfo(
            "bootstrap_task_started",
            task_id=task_id,
            project_id=project_id,
            title=task_title,
        )

        try:
            # ── 1. Get project context from state ─────────────────────────────
            project_summary = task.get("project_summary", task_description)
            task_dag = task.get("task_dag", [])
            tech_stack = task.get("tech_stack", "")

            # ── 2. Check existing files ───────────────────────────────────────
            existing_files = await self._get_existing_files(project_id)

            # ── 3. Build prompt and call LLM ──────────────────────────────────
            user_prompt = format_bootstrap_task_prompt(
                project_summary=project_summary,
                task_dag=task_dag,
                tech_stack=tech_stack,
                existing_files=existing_files,
            )

            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )

            files: List[Dict[str, str]] = response.get("files", [])
            notes: str = response.get("notes", "")

            if not files:
                await self.log.awarning(
                    "bootstrap_no_files_generated",
                    task_id=task_id,
                    project_id=project_id,
                )
                # Not an error - might mean configs already exist
                await self.report_complete(
                    task_id=task_id,
                    project_id=project_id,
                    files_written=[],
                )
                return {"files_written": [], "notes": notes, "branch": None}

            # ── 4. Create task branch ─────────────────────────────────────────
            branch = self.git.create_task_branch(
                project_id=project_id,
                agent_name=self.agent_name,
                task_id=task_id,
            )

            # ── 5. Validate and write files ───────────────────────────────────
            files_written: List[str] = []
            validation_warnings: List[str] = []

            for file_entry in files:
                file_path: str = file_entry.get("path", "")
                content: str = file_entry.get("content", "")

                if not file_path or not content:
                    continue

                # Skip files that already exist (unless they're templates)
                if file_path in existing_files and not file_path.endswith(".example"):
                    await self.log.ainfo(
                        "skipping_existing_file",
                        file_path=file_path,
                    )
                    continue

                # Validate the file content
                warnings = self._validate_config_file(file_path, content)
                validation_warnings.extend(warnings)

                await self.workspace_manager.write_file_atomic(
                    project_id=project_id,
                    file_path=file_path,
                    content=content,
                    agent=self.agent_name,
                    task_id=task_id,
                )
                files_written.append(file_path)
                await self.log.ainfo(
                    "config_file_written",
                    task_id=task_id,
                    file_path=file_path,
                    size=len(content),
                )

            if validation_warnings:
                await self.log.awarning(
                    "config_validation_warnings",
                    task_id=task_id,
                    warnings=validation_warnings,
                )

            # ── 6. Commit ─────────────────────────────────────────────────────
            if files_written:
                self.git.commit(
                    project_id=project_id,
                    branch=branch,
                    task_id=task_id,
                    task_title=task_title,
                    agent_name=self.agent_name,
                )

            # ── 7. Report complete ────────────────────────────────────────────
            await self.report_complete(
                task_id=task_id,
                project_id=project_id,
                files_written=files_written,
            )

            await self.log.ainfo(
                "bootstrap_task_completed",
                task_id=task_id,
                files_count=len(files_written),
            )
            return {
                "files_written": files_written,
                "notes": notes,
                "branch": branch,
                "validation_warnings": validation_warnings,
            }

        except Exception as exc:
            await self.log.aerror(
                "bootstrap_task_failed",
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

    async def _get_existing_files(self, project_id: str) -> List[str]:
        """Get list of existing files in the workspace."""
        try:
            return await self.workspace_manager.list_files(project_id)
        except Exception:
            return []

    def _validate_config_file(self, file_path: str, content: str) -> List[str]:
        """Validate a configuration file's content.

        Args:
            file_path: Path of the file
            content: File content

        Returns:
            List of warning messages (empty if valid)
        """
        warnings = []

        # Validate JSON files
        if file_path.endswith(".json"):
            try:
                json.loads(content)
            except json.JSONDecodeError as e:
                warnings.append(f"{file_path}: Invalid JSON - {e}")

        # Validate package.json
        if file_path == "package.json" or file_path.endswith("/package.json"):
            try:
                data = json.loads(content)
                if "name" not in data:
                    warnings.append(f"{file_path}: Missing 'name' field")
                if "version" not in data:
                    warnings.append(f"{file_path}: Missing 'version' field")
            except json.JSONDecodeError:
                pass  # Already caught above

        # Validate requirements.txt
        if file_path == "requirements.txt" or file_path.endswith("/requirements.txt"):
            lines = content.strip().split("\n")
            for i, line in enumerate(lines, 1):
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                # Check for unpinned versions
                if "==" not in line and ">=" not in line and "<" not in line:
                    warnings.append(
                        f"{file_path}:{i}: Unpinned dependency '{line}' - "
                        "consider pinning version for reproducibility"
                    )

        # Validate docker-compose.yml
        if "docker-compose" in file_path and (file_path.endswith(".yml") or file_path.endswith(".yaml")):
            try:
                import yaml
                data = yaml.safe_load(content)
                if not data or "services" not in data:
                    warnings.append(f"{file_path}: Missing 'services' section")
            except ImportError:
                pass  # yaml not available
            except Exception as e:
                warnings.append(f"{file_path}: Invalid YAML - {e}")

        # Validate .env.example has all expected patterns
        if file_path.endswith(".env.example") or file_path.endswith(".env.template"):
            if "DATABASE_URL" not in content and "POSTGRES" not in content:
                if any(db in content.lower() for db in ["postgres", "mysql", "database"]):
                    warnings.append(
                        f"{file_path}: Database mentioned but no DATABASE_URL variable"
                    )

        return warnings
