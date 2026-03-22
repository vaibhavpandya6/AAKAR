"""QA agent — generates tests, runs them in Docker sandbox, reports bugs."""

import re
from typing import Any, Dict, List, Optional, Tuple

import structlog

from agents.base_agent import BaseAgent
from agents.qa_agent.prompts import SYSTEM_PROMPT, format_qa_task_prompt
from messaging.schemas import Message, MessageType
from tools.docker_executor import DockerSandbox
from workspace_manager.git_manager import GitManager

logger = structlog.get_logger()

# ── Test output parsing ───────────────────────────────────────────────────────
# pytest output patterns
_PYTEST_PASSED = re.compile(r"(\d+)\s+passed")
_PYTEST_FAILED = re.compile(r"(\d+)\s+failed")
_PYTEST_ERROR = re.compile(r"(\d+)\s+error")
_PYTEST_WARNINGS = re.compile(r"(\d+)\s+warning")

# Individual failure capture: FAILED tests/test_X.py::test_name - ErrorType
_FAIL_LINE = re.compile(r"^FAILED\s+([\w/.]+::[\w]+)\s+-\s+(.+)$", re.MULTILINE)

# Stack trace header
_TRACEBACK = re.compile(r"(ERROR\s+[\w/.]+::[\w]+\n.*?(?=\n(?:FAILED|ERROR|PASSED|\n)))", re.DOTALL)


class TestRunResult:
    """Structured representation of a pytest run."""

    def __init__(
        self,
        passed: int,
        failed: int,
        errors: int,
        failed_tests: List[Dict[str, str]],
        raw_output: str,
        timed_out: bool,
    ) -> None:
        self.passed = passed
        self.failed = failed
        self.errors = errors
        self.failed_tests = failed_tests  # [{name, reason}]
        self.raw_output = raw_output
        self.timed_out = timed_out

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.errors == 0 and not self.timed_out

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors


class QAAgent(BaseAgent):
    """Generates tests, executes them in Docker, and reports bugs.

    Workflow:
        1. Read all task-related workspace files
        2. Retrieve RAG context
        3. Build prompt with file contents
        4. Call LLM → parse test_files + bug_report
        5. Write test files to tests/
        6. Run tests in Docker sandbox
        7. Parse test output; retry once on setup failures
        8. If bugs found → store fix in long-term memory + publish BUG_REPORT
        9. If all pass → publish TASK_COMPLETE
    """

    TESTS_DIR = "tests"
    # Docker image used for Python tests
    _PYTHON_TEST_IMAGE = "python:3.11-alpine"
    # pytest install + run command (workspace is mounted at /app/workspace)
    _PYTEST_CMD = (
        "pip install pytest pytest-asyncio httpx --quiet --no-cache-dir "
        "&& cd /app/workspace && python -m pytest tests/ -v --tb=short 2>&1"
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.git = GitManager()
        self.sandbox = DockerSandbox()
        self.log = logger.bind(agent=self.agent_name, type="qa")

    # ──────────────────────────────────────────────────────────────────────
    # File collection
    # ──────────────────────────────────────────────────────────────────────

    async def _read_task_files(
        self, project_id: str, task_description: str
    ) -> str:
        """Read workspace files relevant to the task.

        Uses vector search to find best-matching files, then reads their
        full content for inclusion in the QA prompt.

        Args:
            project_id: Project identifier
            task_description: Task description for similarity search

        Returns:
            Formatted block of file contents for prompt injection
        """
        rag_chunks = await self.vector_store.retrieve(
            project_id=project_id,
            query=task_description,
            top_k=8,
        )

        # Deduplicate file paths
        seen: dict = {}
        for chunk in rag_chunks:
            fp = chunk.get("file_path")
            if fp and fp not in seen:
                seen[fp] = chunk.get("similarity_score", 0)

        blocks: List[str] = []
        for fp in sorted(seen, key=lambda x: seen[x], reverse=True):
            try:
                content = await self.workspace_manager.read_file(project_id, fp)
                blocks.append(f"### {fp}\n```\n{content[:3000]}\n```")
            except Exception:
                pass

        return "\n\n".join(blocks) if blocks else "No source files found."

    # ──────────────────────────────────────────────────────────────────────
    # Test output parsing
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_test_output(stdout: str, stderr: str, timed_out: bool) -> TestRunResult:
        """Parse pytest stdout/stderr into a structured result.

        Args:
            stdout: pytest stdout
            stderr: pytest stderr
            timed_out: Whether container timed out

        Returns:
            TestRunResult with parsed counts and failures
        """
        combined = stdout + "\n" + stderr

        passed = int((_PYTEST_PASSED.search(combined) or type("", (), {"group": lambda *a: "0"})()).group(1) or 0)
        failed = int((_PYTEST_FAILED.search(combined) or type("", (), {"group": lambda *a: "0"})()).group(1) or 0)
        errors = int((_PYTEST_ERROR.search(combined) or type("", (), {"group": lambda *a: "0"})()).group(1) or 0)

        failed_tests = [
            {"name": m.group(1), "reason": m.group(2).strip()}
            for m in _FAIL_LINE.finditer(combined)
        ]

        return TestRunResult(
            passed=passed,
            failed=failed,
            errors=errors,
            failed_tests=failed_tests,
            raw_output=combined[:4000],  # cap for storage
            timed_out=timed_out,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Execute
    # ──────────────────────────────────────────────────────────────────────

    async def execute(self, task: Dict[str, Any], project_id: str) -> Dict[str, Any]:
        """Execute a QA task: generate tests, run in sandbox, report.

        Args:
            task: Task dict with id, title, description, etc.
            project_id: Project identifier

        Returns:
            Result dict with test_files_written, bug_report, test_result
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
            "qa_task_started",
            task_id=task_id,
            project_id=project_id,
            title=task_title,
        )

        try:
            # ── 1. Read relevant source files ──────────────────────────────
            files_to_test_block = await self._read_task_files(project_id, task_description)

            # ── 2. RAG context ─────────────────────────────────────────────
            rag_chunks = await self.vector_store.retrieve(
                project_id=project_id,
                query=f"test {task_title} {task_description}",
                top_k=4,
            )
            rag_context = self._format_rag_context(rag_chunks)

            # ── 3. Build prompt ────────────────────────────────────────────
            user_prompt = format_qa_task_prompt(
                task_title=task_title,
                files_to_test=files_to_test_block,
                acceptance_criteria=acceptance_criteria,
                rag_context=rag_context,
            )

            # ── 4. Call LLM ────────────────────────────────────────────────
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )

            test_files: List[Dict[str, str]] = response.get("test_files", [])
            llm_bug_report: List[Dict[str, Any]] = response.get("bug_report", [])
            notes: str = response.get("notes", "")

            if not test_files:
                raise ValueError("LLM returned no test files in response")

            # ── 5. Create task branch ──────────────────────────────────────
            branch = self.git.create_task_branch(
                project_id=project_id,
                agent_name=self.agent_name,
                task_id=task_id,
            )

            # ── 6. Write test files ────────────────────────────────────────
            test_files_written: List[str] = []
            for file_entry in test_files:
                file_path: str = file_entry.get("path", "")
                content: str = file_entry.get("content", "")

                if not file_path or not content:
                    continue

                # Force path under tests/
                if not file_path.startswith(self.TESTS_DIR):
                    file_path = f"{self.TESTS_DIR}/{file_path.lstrip('/')}"

                await self.workspace_manager.write_file_atomic(
                    project_id=project_id,
                    file_path=file_path,
                    content=content,
                    agent=self.agent_name,
                    task_id=task_id,
                )
                test_files_written.append(file_path)
                await self.log.ainfo(
                    "test_file_written",
                    task_id=task_id,
                    file_path=file_path,
                )

            # ── 7. Commit test files ───────────────────────────────────────
            self.git.commit(
                project_id=project_id,
                branch=branch,
                task_id=task_id,
                task_title=f"tests: {task_title}",
                agent_name=self.agent_name,
            )

            # ── 8. Run tests in Docker sandbox ─────────────────────────────
            await self.log.ainfo(
                "sandbox_test_run_starting",
                task_id=task_id,
                image=self._PYTHON_TEST_IMAGE,
            )

            sandbox_result = await self.sandbox.run(
                project_id=project_id,
                task_id=task_id,
                command=self._PYTEST_CMD,
                image=self._PYTHON_TEST_IMAGE,
            )

            test_result = self._parse_test_output(
                stdout=sandbox_result.get("stdout", ""),
                stderr=sandbox_result.get("stderr", ""),
                timed_out=sandbox_result.get("timed_out", False),
            )

            await self.log.ainfo(
                "sandbox_test_run_complete",
                task_id=task_id,
                passed=test_result.passed,
                failed=test_result.failed,
                errors=test_result.errors,
                timed_out=test_result.timed_out,
            )

            # ── 9. Retry once on container setup error (exit code 1, 0 tests) ──
            if test_result.total == 0 and not test_result.timed_out:
                await self.log.awarning(
                    "no_tests_collected_retrying",
                    task_id=task_id,
                    stderr_sample=sandbox_result.get("stderr", "")[:400],
                )
                # Install deps from requirements.txt if present
                retry_cmd = (
                    "pip install -r /app/workspace/requirements.txt --quiet --no-cache-dir 2>/dev/null || true "
                    "&& pip install pytest pytest-asyncio httpx --quiet --no-cache-dir "
                    "&& cd /app/workspace && python -m pytest tests/ -v --tb=short 2>&1"
                )
                sandbox_result = await self.sandbox.run(
                    project_id=project_id,
                    task_id=f"{task_id}-retry",
                    command=retry_cmd,
                    image=self._PYTHON_TEST_IMAGE,
                )
                test_result = self._parse_test_output(
                    stdout=sandbox_result.get("stdout", ""),
                    stderr=sandbox_result.get("stderr", ""),
                    timed_out=sandbox_result.get("timed_out", False),
                )

            # ── 10. Handle bugs: LLM report + runtime failures ────────────
            combined_bugs = list(llm_bug_report)

            # Add runtime failures to bug report
            for fail in test_result.failed_tests:
                combined_bugs.append(
                    {
                        "severity": "high",
                        "description": f"Test failed at runtime: {fail['name']} — {fail['reason']}",
                        "file": fail["name"].split("::")[0],
                        "line": None,
                        "suggestion": "Investigate test failure and fix root cause.",
                    }
                )

            if test_result.timed_out:
                combined_bugs.append(
                    {
                        "severity": "high",
                        "description": "Test container timed out — possible infinite loop or deadlock",
                        "file": "tests/",
                        "line": None,
                        "suggestion": "Add timeout guards and check for blocking async calls.",
                    }
                )

            # ── 11. Store fix knowledge for failures ───────────────────────
            for bug in combined_bugs:
                if bug.get("severity") in ("critical", "high"):
                    await self.long_term_memory.store_fix(
                        task_id=task_id,
                        error=bug.get("description", ""),
                        fix=bug.get("suggestion", ""),
                        agent=self.agent_name,
                    )

            # ── 12. Publish result message ─────────────────────────────────
            if combined_bugs:
                # Publish BUG_REPORT alongside complete (so orchestrator can triage)
                bug_message = Message(
                    correlation_id=f"{project_id}:{task_id}",
                    sender=self.agent_name,
                    recipient="orchestrator",
                    message_type=MessageType.BUG_REPORT,
                    payload={
                        "task_id": task_id,
                        "project_id": project_id,
                        "bugs": combined_bugs,
                        "test_summary": {
                            "passed": test_result.passed,
                            "failed": test_result.failed,
                            "errors": test_result.errors,
                            "timed_out": test_result.timed_out,
                        },
                        "raw_output_sample": test_result.raw_output[:1000],
                    },
                )
                await self.message_bus.publish("stream:orchestrator", bug_message)
                await self.log.awarning(
                    "bug_report_published",
                    task_id=task_id,
                    bug_count=len(combined_bugs),
                )

            if test_result.all_passed:
                await self.report_complete(
                    task_id=task_id,
                    project_id=project_id,
                    files_written=test_files_written,
                )
                await self.log.ainfo(
                    "qa_task_all_passed",
                    task_id=task_id,
                    passed=test_result.passed,
                )
            else:
                # Tests failed; report failure with details
                failure_summary = (
                    f"{test_result.failed} tests failed, "
                    f"{test_result.errors} errors, "
                    f"timed_out={test_result.timed_out}. "
                    f"Failures: {[f['name'] for f in test_result.failed_tests[:5]]}"
                )
                await self.report_failure(
                    task_id=task_id,
                    project_id=project_id,
                    error=failure_summary,
                )

            return {
                "test_files_written": test_files_written,
                "bug_report": combined_bugs,
                "test_result": {
                    "passed": test_result.passed,
                    "failed": test_result.failed,
                    "errors": test_result.errors,
                    "timed_out": test_result.timed_out,
                    "all_passed": test_result.all_passed,
                },
                "notes": notes,
                "branch": branch,
            }

        except Exception as exc:
            await self.log.aerror(
                "qa_task_failed",
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
