"""Multi-agent orchestration and prompt management."""

import json
from datetime import datetime, timezone
from typing import Dict, Optional

import structlog

from agents.base_agent import _parse_json_safe
from agents.backend_agent.prompts import SYSTEM_PROMPT as BACKEND_SYSTEM
from agents.backend_agent.prompts import format_backend_task_prompt
from agents.database_agent.prompts import SYSTEM_PROMPT as DATABASE_SYSTEM
from agents.database_agent.prompts import format_database_task_prompt
from agents.frontend_agent.prompts import SYSTEM_PROMPT as FRONTEND_SYSTEM
from agents.frontend_agent.prompts import format_frontend_task_prompt
from agents.qa_agent.prompts import SYSTEM_PROMPT as QA_SYSTEM
from agents.qa_agent.prompts import format_qa_task_prompt
from agents.reviewer_agent.prompts import SYSTEM_PROMPT as REVIEWER_SYSTEM
from agents.reviewer_agent.prompts import format_reviewer_task_prompt

logger = structlog.get_logger()


class AgentPromptOrchestrator:
    """Manages prompt templates and execution for all agents."""

    AGENT_TYPES = {
        "backend": {
            "system_prompt": BACKEND_SYSTEM,
            "format_func": format_backend_task_prompt,
            "skill": "backend/api_development",
        },
        "frontend": {
            "system_prompt": FRONTEND_SYSTEM,
            "format_func": format_frontend_task_prompt,
            "skill": "frontend/ui_development",
        },
        "database": {
            "system_prompt": DATABASE_SYSTEM,
            "format_func": format_database_task_prompt,
            "skill": "database/migrations",
        },
        "qa": {
            "system_prompt": QA_SYSTEM,
            "format_func": format_qa_task_prompt,
            "skill": "qa/testing_security",
        },
        "reviewer": {
            "system_prompt": REVIEWER_SYSTEM,
            "format_func": format_reviewer_task_prompt,
            "skill": "review/approval",
        },
    }

    @staticmethod
    def get_system_prompt(agent_type: str) -> str:
        """Get system prompt for agent type.

        Args:
            agent_type: Type of agent (backend, frontend, database, qa, reviewer)

        Returns:
            System prompt string.

        Raises:
            ValueError: If agent type unknown.
        """
        if agent_type not in AgentPromptOrchestrator.AGENT_TYPES:
            raise ValueError(f"Unknown agent type: {agent_type}")

        return AgentPromptOrchestrator.AGENT_TYPES[agent_type]["system_prompt"]

    @staticmethod
    def format_task_prompt(
        agent_type: str,
        task_id: str,
        **kwargs,
    ) -> str:
        """Format task prompt for specific agent type.

        Args:
            agent_type: Type of agent
            task_id: Task identifier for logging
            **kwargs: Variables for prompt template

        Returns:
            Formatted task prompt.

        Raises:
            ValueError: If agent type unknown or missing required kwargs.
        """
        if agent_type not in AgentPromptOrchestrator.AGENT_TYPES:
            raise ValueError(f"Unknown agent type: {agent_type}")

        try:
            format_func = AgentPromptOrchestrator.AGENT_TYPES[agent_type]["format_func"]
            prompt = format_func(**kwargs)
            logger.info(
                "task_prompt_formatted",
                agent_type=agent_type,
                task_id=task_id,
            )
            return prompt
        except TypeError as e:
            logger.error(
                "Missing prompt template variables",
                agent_type=agent_type,
                error=str(e),
            )
            raise ValueError(f"Missing required prompt variables: {str(e)}") from e

    @staticmethod
    def get_agent_skill(agent_type: str) -> str:
        """Get skill classification for agent type.

        Args:
            agent_type: Type of agent

        Returns:
            Skill identifier string.
        """
        if agent_type in AgentPromptOrchestrator.AGENT_TYPES:
            return AgentPromptOrchestrator.AGENT_TYPES[agent_type]["skill"]
        return "unknown"

    @staticmethod
    def validate_agent_response(
        agent_type: str,
        response: str,
    ) -> Dict[str, any]:
        """Validate and parse agent response JSON.

        Args:
            agent_type: Type of agent
            response: Agent response string

        Returns:
            Parsed response dictionary.

        Raises:
            json.JSONDecodeError: If response not valid JSON.
            ValueError: If response missing required fields for agent type.
        """
        try:
            parsed = _parse_json_safe(response)
        except json.JSONDecodeError as e:
            logger.error(
                "Agent response not valid JSON",
                agent_type=agent_type,
                error=str(e),
            )
            raise

        # Validate structure by agent type
        if agent_type == "reviewer":
            required_fields = {"approved", "issues", "summary"}
            if not required_fields.issubset(parsed.keys()):
                raise ValueError(f"Missing required fields: {required_fields - parsed.keys()}")
        else:
            # All other agents require files field
            if "files" not in parsed:
                raise ValueError("Missing required 'files' field in response")

        logger.debug(
            "Agent response validated",
            agent_type=agent_type,
        )
        return parsed


# Global orchestrator instance
_orchestrator_instance: Optional[AgentPromptOrchestrator] = None


def get_prompt_orchestrator() -> AgentPromptOrchestrator:
    """Get global prompt orchestrator instance.

    Returns:
        AgentPromptOrchestrator singleton.
    """
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = AgentPromptOrchestrator()
    return _orchestrator_instance
