"""Centralized LLM factory for Groq models.

Provides a single place to configure all LLM instances across agents and nodes.
Uses Groq's API with Llama 3.1 70B model by default.
"""

from typing import Any, Dict, Optional

from langchain_groq import ChatGroq

from config.settings import settings


def create_llm(
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    streaming: bool = False,
    **kwargs: Any,
) -> ChatGroq:
    """Create a ChatGroq LLM instance with consistent configuration.

    Args:
        model: Groq model name (default: from settings.llm_model).
               Available models: llama-3.3-70b-versatile, llama-3.1-8b-instant,
               mixtral-8x7b-32768, gemma2-9b-it
        temperature: Sampling temperature 0.0-1.0 (default: from settings.llm_temperature).
        streaming: Enable streaming responses (default: False).
        **kwargs: Additional arguments passed to ChatGroq constructor.

    Returns:
        Configured ChatGroq instance ready for use with LangChain.

    Example:
        >>> llm = create_llm()  # Uses defaults from settings
        >>> llm = create_llm(model="llama-3.1-8b-instant", temperature=0.7)
    """
    effective_model = model or settings.llm_model
    effective_temp = temperature if temperature is not None else settings.llm_temperature

    return ChatGroq(
        model=effective_model,
        temperature=effective_temp,
        groq_api_key=settings.groq_api_key,
        streaming=streaming,
        **kwargs,
    )


def create_json_mode_llm(
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    **kwargs: Any,
) -> ChatGroq:
    """Create a ChatGroq LLM configured for JSON output.

    **Important:** Groq does not support OpenAI's native JSON mode
    (`response_format={"type": "json_object"}`). Instead, enforce JSON output
    by including explicit instructions in your system prompt:

        "You MUST respond with valid JSON only. No markdown, no explanations."

    This factory method exists for API parity with OpenAI-based code but does
    not apply any special JSON configuration. The caller must handle JSON
    enforcement via prompts and parsing.

    Args:
        model: Groq model name (default: from settings).
        temperature: Sampling temperature (default: from settings).
        **kwargs: Additional ChatGroq constructor arguments.

    Returns:
        ChatGroq instance (JSON enforcement is prompt-based, not config-based).

    Example:
        >>> llm = create_json_mode_llm()
        >>> system_msg = "You are a planner. Respond with valid JSON only..."
        >>> response = await llm.ainvoke([system_msg, user_msg])
    """
    return create_llm(model=model, temperature=temperature, **kwargs)
