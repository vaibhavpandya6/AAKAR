"""Backend agent — implements FastAPI endpoints and Python services."""

import json
from typing import Any, Dict, List

import structlog

from agents.base_agent import BaseAgent
from agents.backend_agent.prompts import SYSTEM_PROMPT, format_backend_task_prompt
from workspace_manager.git_manager import GitManager

logger = structlog.get_logger()


class BackendAgent(BaseAgent):
    """Generates FastAPI endpoints, Pydantic schemas, and Python services.

    Workflow:
        1. Retrieve RAG context relevant to the task description
        2. Retrieve similar past fixes from long-term memory
        3. Build prompt and call LLM
        4. Atomically write each generated file
        5. Commit files to task branch
        6. Re-index new files in vector store
        7. Publish TASK_COMPLETE
    """

    # Directories the backend agent is allowed to write to
    ALLOWED_DIRS = {"backend", "routers", "schemas", "services", "models", "core"}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.git = GitManager()
        self.log = logger.bind(agent=self.agent_name, type="backend")

    async def execute(self, task: Dict[str, Any], project_id: str) -> Dict[str, Any]:
        """Execute a backend task end-to-end.

        Args:
            task: Task dict with keys: id, title, description, skill_required,
                  acceptance_criteria (optional), depends_on (optional)
            project_id: Project identifier

        Returns:
            Result dict with files_written, notes, branch_name
        """
        task_id = str(task["id"])
        task_title = task.get("title", "")
        task_description = task.get("description", "")

        # Format acceptance criteria (it's a list from the planner)
        acceptance_criteria_list = task.get("acceptance_criteria", [])
        if isinstance(acceptance_criteria_list, list) and acceptance_criteria_list:
            acceptance_criteria = "\n".join(f"- {criterion}" for criterion in acceptance_criteria_list)
        elif isinstance(acceptance_criteria_list, str):
            acceptance_criteria = acceptance_criteria_list
        else:
            acceptance_criteria = "Not specified"

        await self.log.ainfo(
            "backend_task_started",
            task_id=task_id,
            project_id=project_id,
            title=task_title,
        )

        try:
            # ── 1. RAG context ────────────────────────────────────────────
            rag_chunks = await self.vector_store.retrieve(
                project_id=project_id,
                query=f"{task_title} {task_description}",
                top_k=5,
            )
            rag_context = self._format_rag_context(rag_chunks)

            # ── 2. Long-term memory ───────────────────────────────────────
            fixes = await self.long_term_memory.retrieve_similar_fixes(
                error=task_description, top_k=3
            )
            previous_fixes = self._format_previous_fixes(fixes)

            # ── 3. Build prompt ───────────────────────────────────────────
            user_prompt = format_backend_task_prompt(
                task_title=task_title,
                task_description=task_description,
                acceptance_criteria=acceptance_criteria,
                stack="Python 3.11, FastAPI, SQLAlchemy 2.0 async, PostgreSQL, Pydantic v2",
                rag_context=rag_context,
                previous_fixes=previous_fixes,
            )

            # ── 4. Call LLM ───────────────────────────────────────────────
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )

            files: List[Dict[str, str]] = response.get("files", [])
            notes: str = response.get("notes", "")

            if not files:
                raise ValueError("LLM returned no files in response")

            # ── 5. Create task branch ─────────────────────────────────────
            branch = self.git.create_task_branch(
                project_id=project_id,
                agent_name=self.agent_name,
                task_id=task_id,
            )

            # ── 6. Write files atomically ─────────────────────────────────
            files_written: List[str] = []
            for file_entry in files:
                file_path: str = file_entry.get("path", "")
                content: str = file_entry.get("content", "")

                if not file_path or not content:
                    await self.log.awarning(
                        "skipping_empty_file_entry",
                        task_id=task_id,
                        file_path=file_path,
                    )
                    continue

                # Ensure backend file paths are in allowed directories
                first_segment = file_path.split("/")[0]
                if first_segment not in self.ALLOWED_DIRS:
                    file_path = f"backend/{file_path}"

                await self.workspace_manager.write_file_atomic(
                    project_id=project_id,
                    file_path=file_path,
                    content=content,
                    agent=self.agent_name,
                    task_id=task_id,
                )
                files_written.append(file_path)
                await self.log.ainfo(
                    "file_written",
                    task_id=task_id,
                    file_path=file_path,
                    size=len(content),
                )

            # ── 6.5. Validate Python syntax ───────────────────────────────
            syntax_errors = await self._validate_python_syntax(
                project_id=project_id,
                files=files_written,
            )

            if syntax_errors:
                await self.log.aerror(
                    "python_syntax_errors_detected",
                    task_id=task_id,
                    errors=syntax_errors,
                )
                # Log warning but continue (let QA agent catch it)
                await self.log.awarning(
                    "continuing_despite_syntax_errors",
                    task_id=task_id,
                    error_count=len(syntax_errors),
                )

            # ── 7. Commit ─────────────────────────────────────────────────
            self.git.commit(
                project_id=project_id,
                branch=branch,
                task_id=task_id,
                task_title=task_title,
                agent_name=self.agent_name,
            )

            # ── 8. Re-index new files ─────────────────────────────────────
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

            # ── 9. Report complete ────────────────────────────────────────
            await self.report_complete(
                task_id=task_id,
                project_id=project_id,
                files_written=files_written,
            )

            result = {
                "files_written": files_written,
                "notes": notes,
                "branch": branch,
            }
            await self.log.ainfo(
                "backend_task_completed",
                task_id=task_id,
                files_count=len(files_written),
            )
            return result

        except Exception as exc:
            await self.log.aerror(
                "backend_task_failed",
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

    async def _validate_python_syntax(
        self, project_id: str, files: List[str]
    ) -> List[Dict[str, str]]:
        """Validate Python syntax of generated files.

        Args:
            project_id: Project identifier
            files: List of file paths to validate

        Returns:
            List of syntax errors (empty if all valid)
        """
        syntax_errors = []

        for file_path in files:
            # Only validate Python files
            if not file_path.endswith('.py'):
                continue

            try:
                content = await self.workspace_manager.read_file(project_id, file_path)

                # Compile to check syntax
                compile(content, file_path, 'exec')

                # Check for common async/await mistakes
                if 'await ' in content:
                    # Find all function definitions
                    lines = content.split('\n')
                    for i, line in enumerate(lines, 1):
                        # Look for def (not async def) followed by await usage
                        if line.strip().startswith('def ') and 'async def' not in line:
                            # Check next 50 lines for await usage
                            func_block = '\n'.join(lines[i:i+50])
                            if 'await ' in func_block:
                                syntax_errors.append({
                                    'file': file_path,
                                    'line': i,
                                    'error': 'Function uses await but is not declared as async def',
                                    'snippet': line.strip(),
                                })

            except SyntaxError as e:
                syntax_errors.append({
                    'file': file_path,
                    'line': e.lineno,
                    'error': str(e.msg),
                    'snippet': e.text.strip() if e.text else '',
                })
            except Exception as e:
                await self.log.awarning(
                    "syntax_validation_failed",
                    file_path=file_path,
                    error=str(e),
                )

        return syntax_errors
