"""Backend agent — implements FastAPI endpoints and Python services."""

import ast
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import structlog

from agents.base_agent import BaseAgent
from agents.backend_agent.prompts import SYSTEM_PROMPT, format_backend_task_prompt
from workspace_manager.git_manager import GitManager

logger = structlog.get_logger()

# Standard library modules that are always available
_STDLIB_MODULES = frozenset(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else frozenset({
    'abc', 'asyncio', 'collections', 'contextlib', 'copy', 'datetime', 'enum',
    'functools', 'hashlib', 'io', 'itertools', 'json', 'logging', 'math', 'os',
    'pathlib', 'pickle', 're', 'secrets', 'shutil', 'subprocess', 'sys', 'tempfile',
    'threading', 'time', 'typing', 'unittest', 'uuid', 'warnings', 'weakref',
})

# Common third-party packages that are expected to be installed
_COMMON_PACKAGES = frozenset({
    'fastapi', 'pydantic', 'sqlalchemy', 'uvicorn', 'starlette', 'httpx',
    'aiohttp', 'requests', 'pytest', 'structlog', 'alembic', 'asyncpg',
    'redis', 'celery', 'langchain', 'openai', 'chromadb', 'numpy', 'pandas',
})


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
                # Critical syntax errors should fail the task immediately
                # These include: await in non-async function, invalid Python syntax
                critical_errors = [
                    e for e in syntax_errors
                    if "await" in e.get("error", "").lower()
                    or "syntax" in e.get("error", "").lower()
                ]
                if critical_errors:
                    error_msg = f"Critical syntax errors in generated code: {critical_errors[:3]}"
                    await self.report_failure(task_id, project_id, error_msg)
                    return

            # ── 6.6. Validate imports ─────────────────────────────────────────
            import_errors = await self._validate_imports(
                project_id=project_id,
                files=files_written,
            )

            if import_errors:
                await self.log.awarning(
                    "import_validation_issues",
                    task_id=task_id,
                    error_count=len(import_errors),
                    errors=import_errors[:5],  # Log first 5
                )
                # Store import errors for QA to review
                # NOTE: We continue despite import errors as they might be
                # resolved by other tasks generating the missing modules

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

    async def _validate_imports(
        self, project_id: str, files: List[str]
    ) -> List[Dict[str, Any]]:
        """Validate that all imports in generated files can be resolved.

        Checks:
        1. Standard library imports
        2. Third-party packages (common ones expected in requirements.txt)
        3. Local project imports (relative/absolute within workspace)

        Args:
            project_id: Project identifier
            files: List of file paths to validate

        Returns:
            List of import errors (empty if all valid)
        """
        import_errors = []
        workspace_path = self.workspace_manager._get_workspace_path(project_id)

        # Build a set of local module paths in the workspace
        local_modules = await self._discover_local_modules(project_id)

        for file_path in files:
            if not file_path.endswith('.py'):
                continue

            try:
                content = await self.workspace_manager.read_file(project_id, file_path)
                tree = ast.parse(content)
                file_dir = os.path.dirname(file_path)

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            error = self._check_import(
                                alias.name, file_path, file_dir,
                                local_modules, workspace_path
                            )
                            if error:
                                import_errors.append(error)

                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        # Handle relative imports
                        if node.level > 0:
                            error = self._check_relative_import(
                                module, node.level, file_path, file_dir,
                                local_modules
                            )
                        else:
                            error = self._check_import(
                                module, file_path, file_dir,
                                local_modules, workspace_path
                            )
                        if error:
                            import_errors.append(error)

            except SyntaxError:
                pass  # Already caught by syntax validation
            except Exception as e:
                await self.log.awarning(
                    "import_validation_failed",
                    file_path=file_path,
                    error=str(e),
                )

        return import_errors

    async def _discover_local_modules(self, project_id: str) -> set:
        """Discover all Python modules available in the workspace.

        Returns:
            Set of module paths (e.g., {'backend', 'backend.routers', 'models'})
        """
        local_modules = set()
        try:
            all_files = await self.workspace_manager.list_files(project_id)
            for f in all_files:
                if f.endswith('.py'):
                    # Convert path to module notation
                    # backend/routers/notes.py -> backend.routers.notes
                    module_path = f[:-3].replace('/', '.').replace('\\', '.')

                    # Add the module and all parent packages
                    parts = module_path.split('.')
                    for i in range(len(parts)):
                        local_modules.add('.'.join(parts[:i+1]))

                    # Handle __init__.py specially - the directory itself is a module
                    if f.endswith('__init__.py'):
                        parent = module_path.rsplit('.', 1)[0] if '.' in module_path else ''
                        if parent:
                            local_modules.add(parent)
        except Exception:
            pass

        return local_modules

    def _check_import(
        self,
        module_name: str,
        file_path: str,
        file_dir: str,
        local_modules: set,
        workspace_path: str,
    ) -> Dict[str, Any] | None:
        """Check if an import can be resolved.

        Returns:
            Error dict if import cannot be resolved, None otherwise
        """
        if not module_name:
            return None

        # Get the top-level package name
        top_level = module_name.split('.')[0]

        # 1. Check stdlib
        if top_level in _STDLIB_MODULES:
            return None

        # 2. Check common third-party packages
        if top_level in _COMMON_PACKAGES:
            return None

        # 3. Check local modules
        if module_name in local_modules or top_level in local_modules:
            return None

        # 4. Check if it's a relative import from current directory context
        if file_dir:
            potential_local = f"{file_dir.replace('/', '.').replace(os.sep, '.')}.{module_name}"
            if potential_local in local_modules:
                return None

        # 5. Try to find it in the Python path (for installed packages)
        try:
            spec = importlib.util.find_spec(top_level)
            if spec is not None:
                return None
        except (ModuleNotFoundError, ValueError, ImportError):
            pass

        return {
            'file': file_path,
            'import': module_name,
            'error': f"Cannot resolve import '{module_name}' - not found in stdlib, "
                     f"common packages, or project modules",
            'suggestion': self._suggest_import_fix(module_name, local_modules),
        }

    def _check_relative_import(
        self,
        module: str,
        level: int,
        file_path: str,
        file_dir: str,
        local_modules: set,
    ) -> Dict[str, Any] | None:
        """Check if a relative import can be resolved.

        Args:
            module: The module being imported (may be empty for 'from . import x')
            level: Number of dots (1 = ., 2 = .., etc.)
            file_path: Path of the importing file
            file_dir: Directory of the importing file
            local_modules: Set of known local modules

        Returns:
            Error dict if import cannot be resolved, None otherwise
        """
        if not file_dir:
            return {
                'file': file_path,
                'import': f"{'.' * level}{module}",
                'error': "Relative import in file without parent package",
                'suggestion': "Use absolute imports or organize code into packages",
            }

        # Navigate up directories based on level
        parts = file_dir.replace('\\', '/').split('/')
        if level > len(parts):
            return {
                'file': file_path,
                'import': f"{'.' * level}{module}",
                'error': f"Relative import level ({level}) exceeds package depth ({len(parts)})",
                'suggestion': "Reduce relative import level or restructure package hierarchy",
            }

        # Build the resolved module path
        base_parts = parts[:len(parts) - level + 1]
        resolved = '.'.join(base_parts)
        if module:
            resolved = f"{resolved}.{module}"

        if resolved in local_modules or resolved.split('.')[0] in local_modules:
            return None

        return {
            'file': file_path,
            'import': f"{'.' * level}{module}",
            'error': f"Cannot resolve relative import - resolved to '{resolved}' which doesn't exist",
            'suggestion': f"Create {resolved.replace('.', '/')}.py or {resolved.replace('.', '/')}/__init__.py",
        }

    def _suggest_import_fix(self, module_name: str, local_modules: set) -> str:
        """Suggest a fix for an unresolved import."""
        top_level = module_name.split('.')[0]

        # Check for similar module names (typo detection)
        from difflib import get_close_matches
        all_known = list(_STDLIB_MODULES) + list(_COMMON_PACKAGES) + list(local_modules)
        matches = get_close_matches(top_level, all_known, n=3, cutoff=0.6)

        if matches:
            return f"Did you mean: {', '.join(matches)}?"

        # Suggest adding to requirements.txt
        return f"Add '{top_level}' to requirements.txt or create the module locally"
