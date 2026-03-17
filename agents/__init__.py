"""Agent factory and public API for the multi-agent system."""

from agents.backend_agent.agent import BackendAgent
from agents.base_agent import BaseAgent, LLMCallError
from agents.database_agent.agent import DatabaseAgent, MigrationValidationError
from agents.frontend_agent.agent import FrontendAgent
from agents.orchestrator import AgentPromptOrchestrator, get_prompt_orchestrator
from agents.qa_agent.agent import QAAgent
from agents.reviewer_agent.agent import ReviewerAgent


def create_agent(
    agent_type: str,
    agent_name: str,
    llm,
    vector_store,
    long_term_memory,
    message_bus,
    workspace_manager,
) -> BaseAgent:
    """Factory: instantiate the correct agent subclass.

    Args:
        agent_type: One of backend | frontend | database | qa | reviewer
        agent_name: Unique instance name (e.g. "backend-agent-1")
        llm: ChatOpenAI instance
        vector_store: VectorStore instance
        long_term_memory: LongTermMemory instance
        message_bus: MessageBus instance
        workspace_manager: WorkspaceManager instance

    Returns:
        Concrete BaseAgent subclass instance.

    Raises:
        ValueError: If agent_type is unrecognised.
    """
    classes = {
        "backend": BackendAgent,
        "frontend": FrontendAgent,
        "database": DatabaseAgent,
        "qa": QAAgent,
        "reviewer": ReviewerAgent,
    }

    cls = classes.get(agent_type)
    if cls is None:
        raise ValueError(
            f"Unknown agent_type '{agent_type}'. "
            f"Valid types: {list(classes)}"
        )

    return cls(
        agent_name=agent_name,
        llm=llm,
        vector_store=vector_store,
        long_term_memory=long_term_memory,
        message_bus=message_bus,
        workspace_manager=workspace_manager,
    )


__all__ = [
    # Base
    "BaseAgent",
    "LLMCallError",
    # Specialized agents
    "BackendAgent",
    "FrontendAgent",
    "DatabaseAgent",
    "QAAgent",
    "ReviewerAgent",
    # Factory
    "create_agent",
    # Prompt orchestrator
    "AgentPromptOrchestrator",
    "get_prompt_orchestrator",
    # Exceptions
    "MigrationValidationError",
]
