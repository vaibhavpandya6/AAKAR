"""Abstract base class for all specialized agents."""

import json
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage

from config import settings
from memory.long_term import LongTermMemory
from memory.vector_store import VectorStore
from messaging.message_bus import MessageBus
from messaging.schemas import Message, MessageType
from security.jwt_handler import create_service_token
from workspace_manager.manager import WorkspaceManager

logger = structlog.get_logger()

# JSON enforcement system addition
_JSON_ENFORCEMENT = (
    "\n\nYou MUST respond with ONLY valid JSON. "
    "No markdown fences, no prose, no comments. "
    "Start your response with { and end with }."
)


class LLMCallError(Exception):
    """Raised when LLM fails to return valid JSON after retries."""
    pass


class BaseAgent(ABC):
    """Abstract base for all specialized agents.

    Agents receive tasks from the message bus, use LLM + tools to execute,
    and publish results back to the bus.
    """

    MAX_LLM_RETRIES = 3
    RETRY_DELAY_SEC = 2.0

    def __init__(
        self,
        agent_name: str,
        llm: ChatOpenAI,
        vector_store: VectorStore,
        long_term_memory: LongTermMemory,
        message_bus: MessageBus,
        workspace_manager: WorkspaceManager,
    ) -> None:
        """Initialize agent.

        Args:
            agent_name: Unique agent identifier (e.g. "backend-agent-1")
            llm: LangChain ChatOpenAI instance
            vector_store: ChromaDB vector store for RAG
            long_term_memory: Cross-project fix memory
            message_bus: Redis Streams message bus
            workspace_manager: Workspace file and lock manager
        """
        self.agent_name = agent_name
        self.llm = llm
        self.vector_store = vector_store
        self.long_term_memory = long_term_memory
        self.message_bus = message_bus
        self.workspace_manager = workspace_manager
        self.log = logger.bind(agent=agent_name)

    # ──────────────────────────────────────────────────────────────────────
    # LLM Interaction
    # ──────────────────────────────────────────────────────────────────────

    async def call_llm(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Call LLM with JSON response enforcement and retry logic.

        Args:
            system_prompt: Agent system prompt (role + constraints)
            user_prompt: Task-specific user prompt

        Returns:
            Parsed JSON response as dict.

        Raises:
            LLMCallError: If valid JSON not returned after MAX_LLM_RETRIES.
        """
        # Inject JSON enforcement instructions
        enforced_system = system_prompt + _JSON_ENFORCEMENT

        last_error: Optional[Exception] = None
        last_raw: str = ""

        for attempt in range(1, self.MAX_LLM_RETRIES + 1):
            t_start = time.monotonic()
            try:
                messages = [
                    SystemMessage(content=enforced_system),
                    HumanMessage(content=user_prompt),
                ]

                response = await self.llm.ainvoke(messages)
                raw = response.content.strip()
                duration_ms = int((time.monotonic() - t_start) * 1000)

                # Extract token usage
                usage = getattr(response, "response_metadata", {}).get("token_usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)

                await self.log.ainfo(
                    "llm_call_success",
                    model=self.llm.model_name,
                    attempt=attempt,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    duration_ms=duration_ms,
                )

                # Strip optional markdown fence
                clean = raw
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[-1]
                    clean = clean.rsplit("```", 1)[0].strip()

                parsed = json.loads(clean)
                return parsed

            except json.JSONDecodeError as exc:
                duration_ms = int((time.monotonic() - t_start) * 1000)
                last_error = exc
                last_raw = raw if "raw" in dir() else ""

                await self.log.awarning(
                    "llm_json_parse_failed",
                    attempt=attempt,
                    max_retries=self.MAX_LLM_RETRIES,
                    error=str(exc),
                    raw_sample=last_raw[:200],
                    duration_ms=duration_ms,
                )

                if attempt < self.MAX_LLM_RETRIES:
                    # Feed back parse error so the model self-corrects
                    user_prompt = (
                        f"{user_prompt}\n\n"
                        f"[PREVIOUS ATTEMPT {attempt} FAILED]\n"
                        f"Your response could not be parsed as JSON. Error: {exc}\n"
                        f"Raw output start: {last_raw[:300]}\n"
                        "Please respond with ONLY valid JSON."
                    )
                    time.sleep(self.RETRY_DELAY_SEC * attempt)

            except Exception as exc:
                duration_ms = int((time.monotonic() - t_start) * 1000)
                last_error = exc
                await self.log.aerror(
                    "llm_call_error",
                    attempt=attempt,
                    error=str(exc),
                    duration_ms=duration_ms,
                )
                if attempt < self.MAX_LLM_RETRIES:
                    time.sleep(self.RETRY_DELAY_SEC * attempt)

        raise LLMCallError(
            f"{self.agent_name} failed to get valid JSON from LLM after "
            f"{self.MAX_LLM_RETRIES} attempts. Last error: {last_error}. "
            f"Last raw sample: {last_raw[:300]}"
        )

    # ──────────────────────────────────────────────────────────────────────
    # Execution (abstract)
    # ──────────────────────────────────────────────────────────────────────

    @abstractmethod
    async def execute(self, task: Dict[str, Any], project_id: str) -> Dict[str, Any]:
        """Execute a task received from the message bus.

        Args:
            task: Task dictionary with id, title, description, etc.
            project_id: Project the task belongs to.

        Returns:
            Execution result dictionary.
        """
        ...

    # ──────────────────────────────────────────────────────────────────────
    # Reporting
    # ──────────────────────────────────────────────────────────────────────

    async def report_complete(
        self,
        task_id: str,
        project_id: str,
        files_written: List[str],
    ) -> None:
        """Publish TASK_COMPLETE message to orchestrator stream.

        Args:
            task_id: Completed task identifier
            project_id: Project identifier
            files_written: List of file paths written during task
        """
        message = Message(
            correlation_id=f"{project_id}:{task_id}",
            sender=self.agent_name,
            recipient="orchestrator",
            message_type=MessageType.TASK_COMPLETE,
            payload={
                "task_id": task_id,
                "project_id": project_id,
                "files_written": files_written,
                "agent": self.agent_name,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        await self.message_bus.publish("stream:orchestrator", message)
        await self.log.ainfo(
            "task_complete_reported",
            task_id=task_id,
            project_id=project_id,
            files_count=len(files_written),
        )

    async def report_failure(
        self,
        task_id: str,
        project_id: str,
        error: str,
    ) -> None:
        """Publish TASK_FAILED message to orchestrator stream.

        Args:
            task_id: Failed task identifier
            project_id: Project identifier
            error: Error description
        """
        message = Message(
            correlation_id=f"{project_id}:{task_id}",
            sender=self.agent_name,
            recipient="orchestrator",
            message_type=MessageType.TASK_FAILED,
            payload={
                "task_id": task_id,
                "project_id": project_id,
                "error": error,
                "agent": self.agent_name,
                "failed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        await self.message_bus.publish("stream:orchestrator", message)
        await self.log.aerror(
            "task_failure_reported",
            task_id=task_id,
            project_id=project_id,
            error=error,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Auth
    # ──────────────────────────────────────────────────────────────────────

    def get_service_token(self) -> str:
        """Get a short-lived service token for this agent.

        Returns:
            JWT service token string.
        """
        return create_service_token(self.agent_name)

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    def _format_rag_context(self, chunks: List[Dict[str, Any]]) -> str:
        """Format RAG chunks into readable context block.

        Args:
            chunks: List of vector store chunk dicts

        Returns:
            Formatted string for prompt injection
        """
        if not chunks:
            return "No similar code found in the codebase."
        lines = []
        for c in chunks:
            lines.append(
                f"### {c.get('file_path', 'unknown')} "
                f"(similarity {c.get('similarity_score', 0):.0%})\n"
                f"```\n{c.get('content', '').strip()}\n```"
            )
        return "\n\n".join(lines)

    def _format_previous_fixes(self, fixes: List[Dict[str, Any]]) -> str:
        """Format long-term memory fixes for prompt injection.

        Args:
            fixes: List of long-term memory fix dicts

        Returns:
            Formatted string for prompt injection
        """
        if not fixes:
            return "No previous fixes found."
        lines = []
        for f in fixes:
            lines.append(
                f"- [{f.get('agent', 'unknown')} / task {f.get('task_id', '?')} "
                f"/ similarity {f.get('similarity_score', 0):.0%}]\n"
                f"  Error: (see context)\n"
                f"  Fix: {f.get('fix', 'no fix recorded')}"
            )
        return "\n".join(lines)
