"""Planner node — decomposes user prompt into a validated task DAG.

Calls Groq (Llama 70B) with JSON-enforced prompts, validates the returned
tasks against the DAG schema (required fields + no cycles), and returns the
plan into state.
"""

import json
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from config import create_json_mode_llm
from orchestrator.state import PlatformState, ProjectStatus
from task_system.task_graph import InvalidDAGError, TaskGraph

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# LLM setup
# ---------------------------------------------------------------------------

# Fields every task dict must carry
_REQUIRED_TASK_FIELDS = {"id", "title", "description", "skill_required", "acceptance_criteria"}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a senior software architect planning a multi-agent development project.
Analyse the user requirement and decompose it into a list of concrete development tasks.

**CRITICAL:** You MUST respond with ONLY valid JSON. No markdown code fences (```json),
no explanations before or after the JSON, no comments inside the JSON. Start your response
with { and end with }. This is mandatory.

RESPONSE FORMAT — JSON only:
{
  "project_summary": "<1–3 sentence description of what will be built>",
  "tasks": [
    {
      "id": "<unique snake_case identifier, e.g. task_001>",
      "title": "<short imperative title>",
      "description": "<detailed description of what must be implemented>",
      "skill_required": "<backend | frontend | database | qa>",
      "acceptance_criteria": ["<criterion 1>", "<criterion 2>"],
      "depends_on": ["<task_id>"]
    }
  ]
}

Rules:
- All task IDs must be unique strings (no duplicates).
- depends_on must reference only IDs defined in the same response.
- No circular dependencies of any kind.
- Include at least one backend task and one qa task.
- Maximum 20 tasks total.
- acceptance_criteria must be a non-empty list of strings.
- depends_on must be an array (use [] if no dependencies).

Remember: Output ONLY the JSON object. Nothing else.
"""

_USER_TEMPLATE = """\
User Requirement:
{original_prompt}

{feedback_section}\
Produce the full task plan now.\
"""

_FEEDBACK_SECTION = """\
Previous Plan Feedback (re-plan — address ALL points below):
{plan_feedback}

"""


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


async def planner_node(state: PlatformState) -> dict[str, Any]:
    """Decompose the user prompt into a validated task DAG.

    Reads ``original_prompt`` and optionally ``plan_feedback`` from state.
    Calls Groq (Llama 70B) with JSON-enforced prompts, validates the response
    against the task schema and DAG constraints, then returns the plan fields.

    Args:
        state: Current PlatformState.

    Returns:
        Partial state dict with ``task_dag``, ``project_summary``,
        ``project_status``, and ``plan_approved``.  On validation failure
        returns ``error_message`` and ``project_status = FAILED``.
    """
    project_id = state.get("project_id", "")
    original_prompt = state.get("original_prompt", "")
    plan_feedback = state.get("plan_feedback", "")

    is_replan = bool(plan_feedback)

    logger.info(
        "planner_node_start",
        project_id=project_id,
        is_replan=is_replan,
    )

    # ── Build prompt ────────────────────────────────────────────────────────
    feedback_section = (
        _FEEDBACK_SECTION.format(plan_feedback=plan_feedback) if is_replan else ""
    )
    user_content = _USER_TEMPLATE.format(
        original_prompt=original_prompt,
        feedback_section=feedback_section,
    )

    # ── Call LLM (Groq with JSON enforcement via prompt) ────────────────────
    try:
        llm = create_json_mode_llm()
        response = await llm.ainvoke(
            [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user_content)]
        )
        raw_content: str = response.content  # type: ignore[assignment]
    except Exception as exc:
        logger.error("planner_llm_failed", project_id=project_id, error=str(exc))
        return {
            "error_message": f"Planner LLM call failed: {exc}",
            "project_status": ProjectStatus.FAILED,
        }

    # ── Parse JSON ──────────────────────────────────────────────────────────
    try:
        data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        logger.error(
            "planner_json_parse_failed",
            project_id=project_id,
            error=str(exc),
            raw_preview=raw_content[:300],
        )
        return {
            "error_message": f"Planner returned invalid JSON: {exc}",
            "project_status": ProjectStatus.FAILED,
        }

    project_summary: str = data.get("project_summary", "")
    tasks: list[dict] = data.get("tasks", [])

    # ── Validate task schema ─────────────────────────────────────────────────
    schema_errors = _validate_task_schema(tasks)
    if schema_errors:
        error_msg = "Task schema validation failed: " + "; ".join(schema_errors)
        logger.error(
            "planner_schema_invalid",
            project_id=project_id,
            errors=schema_errors,
        )
        return {
            "error_message": error_msg,
            "project_status": ProjectStatus.FAILED,
        }

    # ── Validate DAG (no cycles, no unknown deps) ────────────────────────────
    try:
        TaskGraph().build_from_dag(tasks)
    except InvalidDAGError as exc:
        logger.error(
            "planner_dag_invalid",
            project_id=project_id,
            reason=exc.reason,
            details=exc.details,
        )
        return {
            "error_message": f"Task DAG invalid: {exc}",
            "project_status": ProjectStatus.FAILED,
        }

    logger.info(
        "planner_node_complete",
        project_id=project_id,
        task_count=len(tasks),
        is_replan=is_replan,
    )

    return {
        "task_dag": tasks,
        "project_summary": project_summary,
        "project_status": ProjectStatus.PLANNING,
        "plan_approved": False,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_task_schema(tasks: list[dict]) -> list[str]:
    """Check each task for required fields and correct types.

    Args:
        tasks: Raw task list from the LLM response.

    Returns:
        List of human-readable error strings (empty if all valid).
    """
    if not tasks:
        return ["tasks list is empty"]

    errors: list[str] = []

    for idx, task in enumerate(tasks):
        if not isinstance(task, dict):
            errors.append(f"task[{idx}] is not a dict")
            continue

        tid = task.get("id", f"(index {idx})")

        for field in _REQUIRED_TASK_FIELDS:
            if field not in task:
                errors.append(f"task '{tid}' missing required field '{field}'")

        # acceptance_criteria must be a non-empty list
        ac = task.get("acceptance_criteria")
        if ac is not None and (not isinstance(ac, list) or len(ac) == 0):
            errors.append(f"task '{tid}' acceptance_criteria must be a non-empty list")

        # depends_on must be a list
        deps = task.get("depends_on")
        if deps is not None and not isinstance(deps, list):
            errors.append(f"task '{tid}' depends_on must be a list")

    return errors
