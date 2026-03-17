"""Reviewer agent — performs final code review and issues approval or rejection."""

import json
from typing import Any, Dict, List

import structlog

from agents.base_agent import BaseAgent
from agents.reviewer_agent.prompts import SYSTEM_PROMPT, format_reviewer_task_prompt
from messaging.schemas import Message, MessageType
from workspace_manager.git_manager import GitManager, MergeConflictError

logger = structlog.get_logger()


class ReviewerAgent(BaseAgent):
    """Reviews all implementation files and approves or rejects for delivery.

    Workflow:
        1. Read all project files from workspace
        2. Read QA results from task payload
        3. Build full-codebase review prompt
        4. Call LLM → parse approved + issues + summary
        5. If approved  → merge task branch to main → tag → publish REVIEW_RESULT(approved=True)
        6. If rejected  → publish REVIEW_RESULT(approved=False) with issues list
        7. On merge conflict → publish MERGE_CONFLICT → report failure
    """

    # Max chars per file before truncation in prompt (avoid context overflow)
    _MAX_FILE_CHARS = 3000
    # Max total files included in prompt
    _MAX_FILES = 20

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.git = GitManager()
        self.log = logger.bind(agent=self.agent_name, type="reviewer")

    # ──────────────────────────────────────────────────────────────────────
    # File collection
    # ──────────────────────────────────────────────────────────────────────

    async def _collect_all_files(self, project_id: str) -> str:
        """Read all project workspace files for review.

        Skips binary-like files (.pyc, .db) and truncates large files.

        Args:
            project_id: Project identifier

        Returns:
            Formatted block of all file contents.
        """
        skip_exts = {".pyc", ".db", ".sqlite", ".png", ".jpg", ".ico", ".woff"}

        try:
            all_paths = await self.workspace_manager.list_files(project_id)
        except Exception:
            return "Could not list workspace files."

        blocks: List[str] = []
        for fp in all_paths[: self._MAX_FILES]:
            if any(fp.endswith(ext) for ext in skip_exts):
                continue
            try:
                content = await self.workspace_manager.read_file(project_id, fp)
                truncated = content[: self._MAX_FILE_CHARS]
                suffix = f"\n... [{len(content) - self._MAX_FILE_CHARS} chars truncated]" \
                         if len(content) > self._MAX_FILE_CHARS else ""
                blocks.append(f"### {fp}\n```\n{truncated}{suffix}\n```")
            except Exception:
                blocks.append(f"### {fp}\n[Could not read file]")

        return "\n\n".join(blocks) if blocks else "No files found in workspace."

    # ──────────────────────────────────────────────────────────────────────
    # Execute
    # ──────────────────────────────────────────────────────────────────────

    async def execute(self, task: Dict[str, Any], project_id: str) -> Dict[str, Any]:
        """Execute a review task end-to-end.

        Args:
            task: Task dict. payload may include qa_results, branch, original_requirements.
            project_id: Project identifier

        Returns:
            Result dict with approved, issues, summary, branch (if merged)
        """
        task_id = str(task["id"])
        task_title = task.get("title", "Code Review")
        payload = task.get("payload", {})
        branch = payload.get("branch", "")
        original_requirements = payload.get("original_requirements", task.get("description", ""))
        qa_results = payload.get("qa_results", "No QA results provided.")

        await self.log.ainfo(
            "reviewer_task_started",
            task_id=task_id,
            project_id=project_id,
            branch=branch,
        )

        try:
            # ── 1. Collect all files ───────────────────────────────────────
            all_files_block = await self._collect_all_files(project_id)

            # ── 2. Build project summary from manifest ─────────────────────
            manifest = await self.workspace_manager.get_manifest(project_id)
            file_count = len(manifest.get("files", {}))
            project_summary = (
                f"Project: {project_id}\n"
                f"Files: {file_count}\n"
                f"Created at: {manifest.get('created_at', 'unknown')}"
            )

            # ── 3. Build prompt ────────────────────────────────────────────
            user_prompt = format_reviewer_task_prompt(
                project_summary=project_summary,
                all_files=all_files_block,
                original_requirements=original_requirements,
                qa_results=qa_results,
            )

            # ── 4. Call LLM ────────────────────────────────────────────────
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )

            approved: bool = bool(response.get("approved", False))
            issues: List[Dict[str, Any]] = response.get("issues", [])
            summary: str = response.get("summary", "")
            suggestions: str = response.get("suggestions", "")

            await self.log.ainfo(
                "review_decision",
                task_id=task_id,
                approved=approved,
                issue_count=len(issues),
            )

            # ── 5. Merge if approved ───────────────────────────────────────
            merged = False
            if approved and branch:
                try:
                    self.git.merge_to_main(project_id=project_id, branch=branch)
                    self.git.tag(
                        project_id=project_id,
                        tag_name=f"review-approved-{task_id[:8]}",
                        message=f"Approved by {self.agent_name}: {summary}",
                    )
                    merged = True
                    await self.log.ainfo(
                        "branch_merged_and_tagged",
                        task_id=task_id,
                        branch=branch,
                    )
                except MergeConflictError as conflict_exc:
                    # Publish MERGE_CONFLICT and fail
                    conflict_message = Message(
                        correlation_id=f"{project_id}:{task_id}",
                        sender=self.agent_name,
                        recipient="orchestrator",
                        message_type=MessageType.MERGE_CONFLICT,
                        payload={
                            "task_id": task_id,
                            "project_id": project_id,
                            "branch": branch,
                            "error": str(conflict_exc),
                        },
                    )
                    await self.message_bus.publish("stream:orchestrator", conflict_message)
                    await self.report_failure(
                        task_id=task_id,
                        project_id=project_id,
                        error=f"Merge conflict: {conflict_exc}",
                    )
                    return {
                        "approved": False,
                        "issues": issues,
                        "summary": f"Merge conflict prevented delivery: {conflict_exc}",
                        "merged": False,
                    }

            # ── 6. Publish REVIEW_RESULT ───────────────────────────────────
            review_message = Message(
                correlation_id=f"{project_id}:{task_id}",
                sender=self.agent_name,
                recipient="orchestrator",
                message_type=MessageType.REVIEW_RESULT,
                payload={
                    "task_id": task_id,
                    "project_id": project_id,
                    "approved": approved,
                    "issues": issues,
                    "summary": summary,
                    "suggestions": suggestions,
                    "merged": merged,
                    "branch": branch,
                },
            )
            await self.message_bus.publish("stream:orchestrator", review_message)

            # ── 7. Report complete or failure ──────────────────────────────
            if approved:
                await self.report_complete(
                    task_id=task_id,
                    project_id=project_id,
                    files_written=[],
                )
            else:
                critical_or_high = [
                    i for i in issues if i.get("severity") in ("critical", "high")
                ]
                rejection_summary = (
                    f"Review rejected: {len(issues)} issues "
                    f"({len(critical_or_high)} critical/high). {summary}"
                )
                await self.report_failure(
                    task_id=task_id,
                    project_id=project_id,
                    error=rejection_summary,
                )

            return {
                "approved": approved,
                "issues": issues,
                "summary": summary,
                "suggestions": suggestions,
                "merged": merged,
            }

        except Exception as exc:
            await self.log.aerror(
                "reviewer_task_failed",
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
