"""Short-term task context management for agents."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

logger = logging.getLogger(__name__)
struct_logger = structlog.get_logger()


class ShortTermMemory:
    """Short-term memory wrapper around LangGraph execution state.

    Manages current task context, agent state, and temporary variables
    within a single execution graph.
    """

    # Standard state keys for task execution
    PROJECT_ID = "project_id"
    TASK_ID = "task_id"
    CURRENT_AGENT = "current_agent"
    TASK_STATUS = "task_status"
    TASK_CONTEXT = "task_context"
    RAG_CHUNKS = "rag_chunks"
    PREVIOUS_ATTEMPTS = "previous_attempts"
    ERRORS = "errors"

    @staticmethod
    def get_context(state: Dict[str, Any], key: str) -> Any:
        """Get value from execution state.

        Args:
            state: LangGraph execution state dict.
            key: Context key to retrieve.

        Returns:
            Value from state, or None if not set.
        """
        return state.get(key)

    @staticmethod
    def set_context(state: Dict[str, Any], key: str, value: Any) -> Dict[str, Any]:
        """Set value in execution state (immutable update).

        Args:
            state: Current state dict.
            key: Context key to set.
            value: Value to store.

        Returns:
            Updated state dict (creates new copy).
        """
        updated_state = state.copy()
        updated_state[key] = value
        logger.debug("State context updated", key=key)
        return updated_state

    @staticmethod
    def format_task_context(
        task: Dict[str, Any],
        rag_chunks: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Format task and retrieved code context for agent prompt.

        Args:
            task: Task dictionary with title, description, etc.
            rag_chunks: Optional list of retrieved code chunks from vector store.

        Returns:
            Formatted context string for agent prompt.
        """
        context_parts = []

        # Task summary
        context_parts.append("# TASK CONTEXT\n")
        context_parts.append(f"Task ID: {task.get('id', 'unknown')}")
        context_parts.append(f"Title: {task.get('title', 'No title')}")
        context_parts.append(f"Description: {task.get('description', 'No description')}")

        if task.get("skill_required"):
            context_parts.append(f"Required Skill: {task.get('skill_required')}")

        # Relevant code context from vector search
        if rag_chunks:
            context_parts.append("\n# RELEVANT CODE CONTEXT (from vector search)\n")
            for idx, chunk in enumerate(rag_chunks, 1):
                context_parts.append(f"\n## Context {idx}")
                context_parts.append(f"File: {chunk.get('file_path', 'unknown')}")
                context_parts.append(f"Similarity: {chunk.get('similarity_score', 0):.2%}")
                context_parts.append(f"\n```\n{chunk.get('content', '')}\n```")

        # Dependencies if any
        if task.get("depends_on"):
            context_parts.append(f"\n# DEPENDENCIES\nDepends on: {', '.join(task.get('depends_on', []))}")

        return "\n".join(context_parts)

    @staticmethod
    def add_error(state: Dict[str, Any], error_message: str, agent: str) -> Dict[str, Any]:
        """Add error to state tracking.

        Args:
            state: Current state.
            error_message: Error message to record.
            agent: Agent that encountered the error.

        Returns:
            Updated state with error added.
        """
        errors = state.get(ShortTermMemory.ERRORS, [])
        errors.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": agent,
                "message": error_message,
            }
        )
        return ShortTermMemory.set_context(state, ShortTermMemory.ERRORS, errors)

    @staticmethod
    def record_attempt(
        state: Dict[str, Any],
        agent: str,
        result: str,
        success: bool,
    ) -> Dict[str, Any]:
        """Record agent attempt in state.

        Args:
            state: Current state.
            agent: Agent name.
            result: Result of attempt.
            success: Whether attempt succeeded.

        Returns:
            Updated state with attempt recorded.
        """
        attempts = state.get(ShortTermMemory.PREVIOUS_ATTEMPTS, [])
        attempts.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": agent,
                "result": result,
                "success": success,
            }
        )
        return ShortTermMemory.set_context(state, ShortTermMemory.PREVIOUS_ATTEMPTS, attempts)

    @staticmethod
    def get_full_context_snapshot(state: Dict[str, Any]) -> str:
        """Get formatted snapshot of full execution state for debugging.

        Args:
            state: Current execution state.

        Returns:
            Formatted context snapshot.
        """
        lines = []
        lines.append("# EXECUTION STATE SNAPSHOT\n")

        # Task info
        lines.append(f"Project: {state.get(ShortTermMemory.PROJECT_ID)}")
        lines.append(f"Task: {state.get(ShortTermMemory.TASK_ID)}")
        lines.append(f"Current Agent: {state.get(ShortTermMemory.CURRENT_AGENT)}")
        lines.append(f"Status: {state.get(ShortTermMemory.TASK_STATUS, 'unknown')}")

        # Previous attempts
        attempts = state.get(ShortTermMemory.PREVIOUS_ATTEMPTS, [])
        if attempts:
            lines.append(f"\n## Previous Attempts ({len(attempts)})")
            for attempt in attempts:
                lines.append(
                    f"  - [{attempt.get('agent')}] {attempt.get('result')[:50]}... "
                    f"(success={attempt.get('success')})"
                )

        # Errors
        errors = state.get(ShortTermMemory.ERRORS, [])
        if errors:
            lines.append(f"\n## Errors ({len(errors)})")
            for error in errors:
                lines.append(f"  - [{error.get('agent')}] {error.get('message')}")

        return "\n".join(lines)
