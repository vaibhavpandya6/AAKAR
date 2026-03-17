"""Frontend agent — generates React/TypeScript components and CSS modules."""

import re
from typing import Any, Dict, List

import structlog

from agents.base_agent import BaseAgent
from agents.frontend_agent.prompts import SYSTEM_PROMPT, format_frontend_task_prompt
from workspace_manager.git_manager import GitManager

logger = structlog.get_logger()

# Glob patterns for backend route files whose endpoints become api_contracts
_BACKEND_ROUTE_GLOBS = ["routers", "backend/routers", "routes", "backend/routes", "api"]
_ROUTE_FILE_EXTS = {".py"}
_MAX_CONTRACT_FILES = 6          # cap to avoid prompt overflow
_MAX_CONTRACT_FILE_CHARS = 2000  # truncate large files


class FrontendAgent(BaseAgent):
    """Generates React/TypeScript components, hooks, and CSS modules.

    Workflow:
        1. Retrieve RAG context
        2. Retrieve similar past fixes
        3. Read existing backend route files → api_contracts
        4. Build prompt and call LLM
        5. Atomically write each generated file
        6. Commit files on task branch
        7. Re-index files in vector store
        8. Publish TASK_COMPLETE
    """

    ALLOWED_DIRS = {"frontend", "src"}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.git = GitManager()
        self.log = logger.bind(agent=self.agent_name, type="frontend")

    # ──────────────────────────────────────────────────────────────────────
    # API Contract discovery
    # ──────────────────────────────────────────────────────────────────────

    async def _collect_api_contracts(self, project_id: str) -> str:
        """Scan workspace for backend route files and extract path/method signatures.

        Reads Python router files from common backend directories, extracts
        @router.get/@router.post decorators plus function signatures, and
        returns a compact summary suitable for prompt injection.

        Args:
            project_id: Project identifier

        Returns:
            Formatted API contract string for prompt injection
        """
        try:
            all_files = await self.workspace_manager.list_files(project_id)
        except Exception:
            return "No backend route files found yet."

        # Pick files that look like route / router modules
        route_files = [
            f for f in all_files
            if any(segment in f.split("/") for segment in _BACKEND_ROUTE_GLOBS)
            and any(f.endswith(ext) for ext in _ROUTE_FILE_EXTS)
        ]

        if not route_files:
            return "No backend route files found yet."

        route_files = route_files[:_MAX_CONTRACT_FILES]

        contracts: List[str] = []
        for file_path in route_files:
            try:
                content = await self.workspace_manager.read_file(project_id, file_path)
            except Exception:
                continue

            # Extract @router.<method> decorator + immediately-following def
            endpoint_pattern = re.compile(
                r'(@(?:router|app)\.\w+\(.*?\))\s*\n\s*(async\s+)?def\s+\w+\([^)]*\)',
                re.DOTALL,
            )
            matches = endpoint_pattern.findall(content[:_MAX_CONTRACT_FILE_CHARS])
            if matches:
                snippet = f"### {file_path}\n"
                snippet += "\n".join(
                    f"{deco}\n  {prefix}def ..." for deco, prefix in matches[:8]
                )
                contracts.append(snippet)
            else:
                # Fall back: include truncated file for context
                contracts.append(
                    f"### {file_path}\n{content[:_MAX_CONTRACT_FILE_CHARS]}"
                )

        return "\n\n".join(contracts) if contracts else "No API endpoints detected yet."

    # ──────────────────────────────────────────────────────────────────────
    # Execute
    # ──────────────────────────────────────────────────────────────────────

    async def execute(self, task: Dict[str, Any], project_id: str) -> Dict[str, Any]:
        """Execute a frontend task end-to-end.

        Args:
            task: Task dict with id, title, description, etc.
            project_id: Project identifier

        Returns:
            Result dict with files_written, notes, branch
        """
        task_id = str(task["id"])
        task_title = task.get("title", "")
        task_description = task.get("description", "")
        acceptance_criteria = task.get("acceptance_criteria", "Not specified")

        await self.log.ainfo(
            "frontend_task_started",
            task_id=task_id,
            project_id=project_id,
            title=task_title,
        )

        try:
            # ── 1. RAG context ─────────────────────────────────────────────
            rag_chunks = await self.vector_store.retrieve(
                project_id=project_id,
                query=f"{task_title} {task_description}",
                top_k=5,
            )
            rag_context = self._format_rag_context(rag_chunks)

            # ── 2. Long-term memory ────────────────────────────────────────
            fixes = await self.long_term_memory.retrieve_similar_fixes(
                error=task_description, top_k=3
            )
            previous_fixes = self._format_previous_fixes(fixes)

            # ── 3. Discover API contracts from workspace ───────────────────
            api_contracts = await self._collect_api_contracts(project_id)

            # ── 4. Build prompt ────────────────────────────────────────────
            user_prompt = format_frontend_task_prompt(
                task_title=task_title,
                task_description=task_description,
                acceptance_criteria=acceptance_criteria,
                api_contracts=api_contracts,
                rag_context=rag_context,
                previous_fixes=previous_fixes,
            )

            # ── 5. Call LLM ────────────────────────────────────────────────
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )

            files: List[Dict[str, str]] = response.get("files", [])
            notes: str = response.get("notes", "")

            if not files:
                raise ValueError("LLM returned no files in response")

            # ── 6. Create task branch ──────────────────────────────────────
            branch = self.git.create_task_branch(
                project_id=project_id,
                agent_name=self.agent_name,
                task_id=task_id,
            )

            # ── 7. Write files atomically ──────────────────────────────────
            files_written: List[str] = []
            for file_entry in files:
                file_path: str = file_entry.get("path", "")
                content: str = file_entry.get("content", "")

                if not file_path or not content:
                    continue

                # Ensure paths are under frontend/
                first_segment = file_path.split("/")[0]
                if first_segment not in self.ALLOWED_DIRS:
                    file_path = f"frontend/{file_path}"

                await self.workspace_manager.write_file_atomic(
                    project_id=project_id,
                    file_path=file_path,
                    content=content,
                    agent=self.agent_name,
                    task_id=task_id,
                )
                files_written.append(file_path)

            # ── 8. Commit ──────────────────────────────────────────────────
            self.git.commit(
                project_id=project_id,
                branch=branch,
                task_id=task_id,
                task_title=task_title,
                agent_name=self.agent_name,
            )

            # ── 9. Re-index ────────────────────────────────────────────────
            for file_path in files_written:
                # Only index TypeScript/JavaScript/CSS-in-text files
                if not file_path.endswith(".css"):
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

            # ── 10. Report ─────────────────────────────────────────────────
            await self.report_complete(
                task_id=task_id,
                project_id=project_id,
                files_written=files_written,
            )

            await self.log.ainfo(
                "frontend_task_completed",
                task_id=task_id,
                files_count=len(files_written),
            )
            return {"files_written": files_written, "notes": notes, "branch": branch}

        except Exception as exc:
            await self.log.aerror(
                "frontend_task_failed",
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
