"""BRD to WBS node — multi-stage pipeline to generate WBS tasks from BRD.

Implements the FULL pipeline from the BRD_to_WBS notebook:
1. Requirements extraction (Agent 2)
2. Scope definition (Agent 3)
3. Proposal generation (Agent 3.5)
4. Architecture design (Agent 4)
5. SOW generation (Agent 5)
6. WBS generation per module (Agent 6)
7. Test case generation per agent
8. Task parsing and transformation to internal format

Uses NVIDIA Nemotron-3-Super-120B model for generation.
"""

import json
import re
import time
import asyncio
from typing import Any

import structlog
from openai import AsyncOpenAI

from config.settings import settings
from orchestrator.state import PlatformState, ProjectStatus
from task_system.task_graph import InvalidDAGError, TaskGraph

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Rate Limiting for 39 RPM
# ---------------------------------------------------------------------------

_last_api_call_time = 0.0
_api_call_lock = asyncio.Lock()
RPM_LIMIT = 39  # Requests per minute
MIN_INTERVAL = 60.0 / RPM_LIMIT  # ~1.54 seconds between calls


async def _throttle_api_call():
    """Ensure we don't exceed 39 RPM by adding delays between API calls."""
    global _last_api_call_time

    async with _api_call_lock:
        current_time = time.time()
        time_since_last_call = current_time - _last_api_call_time

        if time_since_last_call < MIN_INTERVAL:
            sleep_time = MIN_INTERVAL - time_since_last_call
            logger.debug("rpm_throttle", sleep_seconds=round(sleep_time, 2))
            await asyncio.sleep(sleep_time)

        _last_api_call_time = time.time()

# ---------------------------------------------------------------------------
# NVIDIA API Configuration
# ---------------------------------------------------------------------------

NVIDIA_MODEL = "nvidia/nemotron-3-super-120b-a12b"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Fallback model if primary fails (optimized for 39 RPM - only 1 fallback)
FALLBACK_MODELS = [
    "qwen/qwen3.5-122b-a10b",  # 256K context
]

# Use Nemotron for all stages (SOW and Test generation)
def _get_sow_model() -> str:
    """Get SOW model - using Nemotron for all stages."""
    return settings.nvidia_model or NVIDIA_MODEL

def _get_test_model() -> str:
    """Get test model - using Nemotron for all stages."""
    return settings.nvidia_model or NVIDIA_MODEL

# ---------------------------------------------------------------------------
# Required fields for internal task format
# ---------------------------------------------------------------------------

_REQUIRED_TASK_FIELDS = {"id", "title", "description", "skill_required", "acceptance_criteria"}

# ---------------------------------------------------------------------------
# Stage 1: Requirements Extraction (Agent 2)
# ---------------------------------------------------------------------------

_SYS_REQ = """You are a principal business analyst with 15+ years of experience.

ABSOLUTE RULES:
1. Extract ONLY what is explicitly stated or clearly implied in the BRD — never invent
2. Every requirement must cite its source section
3. Use exact BRD language — do not paraphrase to change meaning
4. Assign unique IDs referenced throughout the entire pipeline
5. Flag ambiguities as gaps — never assume or fill blanks
6. One row = one requirement — never combine two into one

**CRITICAL:** Respond with ONLY valid JSON. No markdown. Start with { end with }.

RESPONSE FORMAT:
{
  "project_meta": {
    "name": "<project name>",
    "domain": "<domain>",
    "timeline": "<timeline from BRD>",
    "team_size": "<team size if mentioned>",
    "tech_constraints": ["<constraint 1>", "<constraint 2>"],
    "budget": "<budget if mentioned>"
  },
  "functional_requirements": [
    {"id": "FR-001", "module": "<module>", "requirement": "<description>", "priority": "High|Medium|Low", "source_section": "<BRD section>"}
  ],
  "non_functional_requirements": [
    {"id": "NFR-001", "category": "<Performance|Security|Scalability|Usability|etc>", "requirement": "<description>", "metric": "<measurable metric>", "source_section": "<BRD section>"}
  ],
  "integrations": [
    {"id": "INT-001", "system": "<external system>", "purpose": "<purpose>", "priority": "High|Medium|Low", "api_type": "<REST|GraphQL|WebSocket|etc>"}
  ],
  "modules": ["<module1>", "<module2>"],
  "gaps": [
    {"id": "GAP-001", "type": "Ambiguity|Missing|Contradiction|Technical Risk|Business Risk", "description": "<what is unclear>", "impact": "<potential impact>", "suggested_clarification": "<question to ask>"}
  ]
}"""

# ---------------------------------------------------------------------------
# Stage 2: Scope Definition (Agent 3)
# ---------------------------------------------------------------------------

_SYS_SCOPE = """You are a senior project manager and scope management specialist.

ABSOLUTE RULES:
1. Base ALL decisions strictly on the BRD and requirements — never assume
2. Every in-scope item must reference a requirement ID
3. Every out-of-scope decision needs explicit BRD justification
4. This scope matrix is the binding contract for all downstream work
5. Phase assignments must respect dependencies

**CRITICAL:** Respond with ONLY valid JSON. No markdown. Start with { end with }.

RESPONSE FORMAT:
{
  "in_scope": [
    {"req_id": "FR-001", "feature": "<feature name>", "description": "<what will be delivered>", "acceptance_criteria": "<how we know it's done>", "phase": 1, "priority": "Must Have|Should Have|Could Have"}
  ],
  "out_of_scope": [
    {"feature": "<feature>", "reason": "<why excluded>", "future_phase": "<when might be added>"}
  ],
  "assumptions": [
    {"id": "ASM-001", "assumption": "<what we assume>", "impact_if_wrong": "<risk if assumption fails>", "owner": "<who validates>"}
  ],
  "constraints": [
    {"id": "CON-001", "constraint": "<limitation>", "source": "<BRD|Technical|Business>", "impact": "<how it affects delivery>"}
  ],
  "dependencies": [
    {"id": "DEP-001", "from_feature": "<feature>", "to_feature": "<feature>", "type": "blocks|enables", "critical_path": true}
  ],
  "client_signoff_questions": [
    {"question": "<clarifying question>", "why_important": "<impact on delivery>"}
  ]
}"""

# ---------------------------------------------------------------------------
# Stage 3: Proposal Generation (Agent 3.5)
# ---------------------------------------------------------------------------

_SYS_PROPOSAL = """You are a senior presales consultant writing a client proposal.

ABSOLUTE RULES:
1. Base everything strictly on BRD, requirements, and scope — no fabrication
2. Executive summary must be understandable by non-technical stakeholders
3. Every deliverable must trace back to an in-scope item
4. Timeline must respect dependencies and team constraints
5. Risks must be real, specific to THIS project

**CRITICAL:** Respond with ONLY valid JSON. No markdown. Start with { end with }.

RESPONSE FORMAT:
{
  "executive_summary": "<2-3 paragraph summary for executives>",
  "objectives": [
    {"id": "OBJ-001", "objective": "<business objective>", "success_metric": "<measurable outcome>"}
  ],
  "deliverables": [
    {"id": "DLV-001", "deliverable": "<what will be delivered>", "req_ids": ["FR-001", "FR-002"], "phase": 1, "acceptance_criteria": "<definition of done>"}
  ],
  "timeline": {
    "phases": [
      {"phase": 1, "name": "<phase name>", "duration_weeks": 4, "deliverables": ["DLV-001"], "milestones": ["<milestone>"]}
    ],
    "total_duration_weeks": 12
  },
  "team_structure": [
    {"role": "<role>", "responsibilities": ["<responsibility>"], "allocation_percent": 100}
  ],
  "risks": [
    {"id": "RSK-001", "risk": "<specific risk>", "probability": "High|Medium|Low", "impact": "High|Medium|Low", "mitigation": "<how to mitigate>"}
  ],
  "next_steps": ["<action item 1>", "<action item 2>"]
}"""

# ---------------------------------------------------------------------------
# Stage 4: Architecture Design (Agent 4)
# ---------------------------------------------------------------------------

_SYS_ARCH = """You are a principal solutions architect with 15+ years designing production systems.

ABSOLUTE RULES:
1. Design ONLY for features IN SCOPE — never add components for out-of-scope items
2. Every component must map to at least one requirement ID
3. Respect ALL BRD constraints — technology preferences, team size, timeline
4. Choose appropriate technology — avoid over-engineering for small projects
5. Every API endpoint must map to a functional requirement

**CRITICAL:** Respond with ONLY valid JSON. No markdown. Start with { end with }.

RESPONSE FORMAT:
{
  "architecture_pattern": "<Monolith|Modular Monolith|Microservices>",
  "justification": "<why this pattern fits the project constraints>",
  "components": [
    {"name": "<component>", "responsibility": "<what it does>", "technology": "<tech stack>", "req_ids": ["FR-001"], "dependencies": ["<other component>"]}
  ],
  "tech_stack": {
    "backend": {"language": "<lang>", "framework": "<framework>", "justification": "<why>"},
    "frontend": {"language": "<lang>", "framework": "<framework>", "justification": "<why>"},
    "database": {"type": "<SQL|NoSQL>", "product": "<product>", "justification": "<why>"},
    "infrastructure": {"platform": "<AWS|GCP|Azure|etc>", "key_services": ["<service>"], "justification": "<why>"}
  },
  "apis": [
    {"method": "POST|GET|PUT|DELETE", "endpoint": "/api/v1/...", "description": "<what it does>", "req_id": "FR-001", "auth_required": true, "rate_limit": "<limit>"}
  ],
  "data_model": [
    {"entity": "<entity>", "attributes": ["<attr>"], "relationships": ["<relation>"]}
  ],
  "security": {
    "authentication": "<method>",
    "authorization": "<method>",
    "data_encryption": "<approach>",
    "nfr_compliance": ["NFR-001"]
  },
  "needs_further_design": [
    {"area": "<area>", "reason": "<why needs more detail>", "owner": "<who>"}
  ]
}"""

# ---------------------------------------------------------------------------
# Stage 5: SOW Generation (Agent 5)
# ---------------------------------------------------------------------------

_SYS_SOW = """You are a senior project manager writing a formal Statement of Work for client signature.

ABSOLUTE RULES:
1. Every deliverable must come from the approved scope matrix — never add or remove
2. Milestones must align with proposal timeline — no invented phases
3. Acceptance criteria must be testable and specific
4. Payment terms tied to milestones
5. Legal sections must be complete and professional

**CRITICAL:** Respond with ONLY valid JSON. No markdown. Start with { end with }.

RESPONSE FORMAT:
{
  "document_meta": {
    "document_id": "SOW-001",
    "version": "1.0",
    "date": "<today's date>",
    "client": "<client name>",
    "vendor": "<vendor name>"
  },
  "project_overview": "<comprehensive project description>",
  "objectives": [
    {"id": "OBJ-001", "objective": "<objective>", "success_criteria": "<measurable criteria>"}
  ],
  "scope_of_work": {
    "in_scope": ["<deliverable 1>", "<deliverable 2>"],
    "out_of_scope": ["<excluded item>"],
    "assumptions": ["<assumption>"]
  },
  "deliverables": [
    {"id": "D-001", "deliverable": "<deliverable>", "description": "<detail>", "acceptance_criteria": "<specific criteria>", "phase": 1}
  ],
  "milestones": [
    {"id": "M-001", "milestone": "<milestone name>", "deliverables": ["D-001"], "due_date": "<relative date>", "payment_percent": 25, "acceptance_criteria": "<sign-off criteria>"}
  ],
  "timeline": {
    "start_date": "<TBD after sign-off>",
    "end_date": "<calculated>",
    "total_weeks": 12,
    "phases": [
      {"phase": 1, "name": "<name>", "start_week": 1, "end_week": 4, "milestones": ["M-001"]}
    ]
  },
  "team_and_resources": [
    {"role": "<role>", "responsibilities": ["<resp>"], "hours_per_week": 40}
  ],
  "communication": {
    "status_reports": "<frequency>",
    "meetings": "<schedule>",
    "escalation_path": ["<level 1>", "<level 2>"]
  },
  "risks_and_mitigations": [
    {"risk": "<risk>", "mitigation": "<mitigation>", "owner": "<owner>"}
  ],
  "change_management": "<process for handling changes>",
  "acceptance_process": "<how deliverables are accepted>",
  "terms_and_conditions": {
    "intellectual_property": "<IP terms>",
    "confidentiality": "<confidentiality clause>",
    "warranty": "<warranty terms>",
    "dispute_resolution": "<process>"
  }
}"""

# ---------------------------------------------------------------------------
# Stage 6: WBS Generation per Module (Agent 6)
# ---------------------------------------------------------------------------

_SYS_WBS = """You are a senior project manager creating a Work Breakdown Structure.

ABSOLUTE RULES:
1. Generate tasks ONLY for modules IN SCOPE from the scope matrix — no exceptions
2. If a module is OUT OF SCOPE, return only: {"tasks": [], "summary": "OUT OF SCOPE — no tasks generated"}
3. Every task must cite at least one requirement ID (FR-xxx, NFR-xxx, INT-xxx)
4. Every task must have one acceptance criterion — one line defining done
5. Effort must be realistic for team size and timeline in the BRD
6. Dependencies must use task IDs from the same WBS — no free-text
7. Role determines which agent handles the task:
   - Design → frontend agent (UI/UX work)
   - Backend → backend agent
   - Frontend → frontend agent
   - Mobile → frontend agent
   - Database → database agent
   - DevOps → backend agent
   - QA → qa agent
   - Docs → backend agent

**CRITICAL:** Respond with ONLY valid JSON. No markdown. Start with { end with }.

RESPONSE FORMAT:
{
  "module": "<module name>",
  "tasks": [
    {
      "wbs_id": "WBS-{MODULE_CODE}-001",
      "task": "<short task name max 50 chars>",
      "description": "<detailed description of what to implement>",
      "req_ids": ["FR-001", "NFR-001"],
      "type": "Design|Backend|Frontend|Mobile|Database|DevOps|QA|Docs",
      "effort_days": 2,
      "story_points": 3,
      "priority": "Must Have|Should Have|Could Have",
      "role": "<Developer|Designer|QA Engineer|DevOps Engineer>",
      "dependencies": ["WBS-XXX-001"],
      "acceptance_criteria": "<one line defining done — specific and testable>",
      "technical_notes": "<implementation hints for the agent>"
    }
  ],
  "summary": {
    "total_tasks": 10,
    "total_effort_days": 25,
    "total_story_points": 40,
    "primary_role": "<most common role>"
  }
}

TASK ID FORMAT: WBS-{MODULE_CODE 3-4 chars}-{NNN}
Module codes examples: AUTH, USER, TASK, DASH, API, DB, etc.
Story points: 1=trivial, 2=small, 3=medium, 5=large, 8=complex, 13=epic
Maximum 12 tasks per module. Be specific, not generic."""

# ---------------------------------------------------------------------------
# Stage 7: Test Case Generation per Agent
# ---------------------------------------------------------------------------

_SYS_TEST = """You are a senior QA engineer and test architect.

ABSOLUTE RULES:
1. Generate test cases ONLY for the tasks provided — no extras
2. Every test case must reference a specific task ID
3. Happy path + edge case + failure scenario per task (min 3 per task)
4. Steps must be specific: name exact endpoints, functions, or UI elements
5. Never write 'verify it works' — write the exact assertion or HTTP status
6. Pass criteria must come from the task acceptance criteria
7. Format output as valid JSON

**CRITICAL:** Respond with ONLY valid JSON. No markdown. Start with { end with }.

RESPONSE FORMAT:
{
  "agent": "<agent name>",
  "test_cases": [
    {
      "test_id": "TC-{PREFIX}-001",
      "task_id": "WBS-XXX-001",
      "test_name": "<descriptive test name>",
      "type": "Unit|Integration|E2E|Contract|Performance",
      "preconditions": ["<required state before test>"],
      "steps": ["<step 1>", "<step 2>"],
      "expected_result": "<specific expected outcome>",
      "pass_criteria": "<exact assertion or status code>",
      "priority": "Critical|High|Medium|Low"
    }
  ],
  "coverage_summary": {
    "total_tests": 15,
    "by_type": {"Unit": 5, "Integration": 5, "E2E": 5},
    "tasks_covered": ["WBS-XXX-001"]
  }
}"""

# ---------------------------------------------------------------------------
# Analysis Templates
# ---------------------------------------------------------------------------

_SYS_ANALYSIS = """You are a senior project analyst creating project metrics.

Based on the WBS tasks provided, generate comprehensive analysis.

**CRITICAL:** Respond with ONLY valid JSON. No markdown. Start with { end with }.

RESPONSE FORMAT:
{
  "effort_summary": [
    {"module": "<module>", "tasks": 10, "effort_days": 25, "story_points": 40, "primary_role": "<role>"}
  ],
  "overall_totals": {
    "total_tasks": 50,
    "total_effort_days": 120,
    "total_story_points": 200,
    "duration_weeks": 12
  },
  "critical_path": [
    {"task_id": "WBS-XXX-001", "task": "<task name>", "duration_days": 5}
  ],
  "critical_path_total_weeks": 8,
  "milestone_mapping": [
    {"milestone_id": "M-001", "milestone": "<milestone name>", "wbs_tasks": ["WBS-XXX-001"]}
  ],
  "top_risks": [
    {"risk": "<risk>", "module": "<module>", "likelihood": "High|Medium|Low", "impact": "High|Medium|Low", "mitigation": "<mitigation>"}
  ],
  "team_allocation": [
    {"role": "<role>", "headcount": 2, "modules": ["<module1>", "<module2>"]}
  ],
  "agent_distribution": {
    "backend": {"task_count": 20, "story_points": 80},
    "frontend": {"task_count": 15, "story_points": 60},
    "database": {"task_count": 5, "story_points": 20},
    "qa": {"task_count": 10, "story_points": 40}
  }
}"""

# ---------------------------------------------------------------------------
# LLM Call Helper (NVIDIA Nemotron)
# ---------------------------------------------------------------------------


def _get_nvidia_client() -> AsyncOpenAI:
    """Create NVIDIA API client using OpenAI-compatible interface."""
    api_key = settings.nvidia_api_key
    if not api_key:
        raise ValueError("NVIDIA_API_KEY not configured in environment")

    return AsyncOpenAI(
        base_url=settings.nvidia_base_url or NVIDIA_BASE_URL,
        api_key=api_key,
    )


async def _call_llm(
    system_prompt: str,
    user_content: str,
    max_tokens: int = 4000,
    model_override: str | None = None,
) -> dict | None:
    """Call NVIDIA Nemotron LLM and parse JSON response.

    Uses NVIDIA's OpenAI-compatible API with the Nemotron-3-Super-120B model.
    Falls back to Qwen if primary fails.
    Rate limited to 39 RPM.

    Args:
        system_prompt: System message for LLM.
        user_content: User message content.
        max_tokens: Maximum tokens in response.
        model_override: Use specific model instead of default.

    Returns:
        Parsed JSON dict or None on failure.
    """
    try:
        client = _get_nvidia_client()
    except ValueError as exc:
        logger.error("nvidia_client_init_failed", error=str(exc))
        return None

    # Determine model(s) to try
    if model_override:
        models_to_try = [model_override] + FALLBACK_MODELS
    else:
        model = settings.nvidia_model or NVIDIA_MODEL
        models_to_try = [model] + FALLBACK_MODELS

    raw_content = None
    for model_name in models_to_try:
        try:
            # Throttle to respect 39 RPM limit
            await _throttle_api_call()

            logger.info("nvidia_llm_call", model=model_name)

            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
                max_tokens=max_tokens,
            )

            raw_content = response.choices[0].message.content
            if not raw_content:
                logger.warning("nvidia_empty_response", model=model_name)
                continue

            # Try to extract JSON if wrapped in markdown
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_content)
            if json_match:
                raw_content = json_match.group(1)

            # Clean up common JSON issues
            raw_content = raw_content.strip()
            if raw_content.startswith("```"):
                raw_content = raw_content[3:]
            if raw_content.endswith("```"):
                raw_content = raw_content[:-3]

            return json.loads(raw_content)

        except json.JSONDecodeError as exc:
            logger.warning(
                "nvidia_json_parse_failed",
                model=model_name,
                error=str(exc),
                preview=raw_content[:500] if raw_content else "empty",
            )
            continue
        except Exception as exc:
            logger.warning(
                "nvidia_llm_call_failed",
                model=model_name,
                error=str(exc),
            )
            continue

    logger.error("nvidia_all_models_failed")
    return None


# ---------------------------------------------------------------------------
# WBS Task Parser (from Markdown/JSON)
# ---------------------------------------------------------------------------


def parse_wbs_tasks(wbs_data: dict | list, module: str = "") -> list[dict]:
    """Parse WBS tasks from LLM response.

    Args:
        wbs_data: Either a dict with 'tasks' key or a list of tasks.
        module: Module name for context.

    Returns:
        List of task dicts with normalized fields.
    """
    if isinstance(wbs_data, list):
        tasks = wbs_data
    elif isinstance(wbs_data, dict):
        tasks = wbs_data.get("tasks", [])
        # Log if we got a dict but no tasks
        if not tasks:
            logger.debug(
                "parse_wbs_no_tasks_in_dict",
                module=module,
                keys=list(wbs_data.keys()),
                summary=wbs_data.get("summary", ""),
            )
    else:
        logger.warning("parse_wbs_invalid_type", module=module, type=type(wbs_data).__name__)
        return []

    parsed = []
    for idx, task in enumerate(tasks):
        if not isinstance(task, dict):
            logger.debug("parse_wbs_task_not_dict", module=module, index=idx, type=type(task).__name__)
            continue

        # Normalize fields
        parsed_task = {
            "wbs_id": task.get("wbs_id", task.get("id", "")),
            "task": task.get("task", task.get("title", "")),
            "description": task.get("description", ""),
            "req_ids": task.get("req_ids", [task.get("req_id", "")]),
            "type": task.get("type", "Backend"),
            "effort_days": task.get("effort_days", task.get("effort", 1)),
            "story_points": task.get("story_points", 3),
            "priority": task.get("priority", "Should Have"),
            "role": task.get("role", "Developer"),
            "dependencies": task.get("dependencies", ""),
            "acceptance_criteria": task.get("acceptance_criteria", task.get("accept", "")),
            "technical_notes": task.get("technical_notes", ""),
            "module": module,
        }

        # Ensure req_ids is a list
        if isinstance(parsed_task["req_ids"], str):
            parsed_task["req_ids"] = [parsed_task["req_ids"]] if parsed_task["req_ids"] else []

        # Ensure dependencies is a list
        if isinstance(parsed_task["dependencies"], str):
            deps = parsed_task["dependencies"]
            if deps:
                parsed_task["dependencies"] = [d.strip() for d in deps.split(",") if d.strip()]
            else:
                parsed_task["dependencies"] = []

        # Only add if we have a valid ID - but generate one if missing
        if not parsed_task["wbs_id"]:
            # Generate a fallback ID
            module_code = re.sub(r'[^A-Z]', '', module.upper())[:4] or "CORE"
            parsed_task["wbs_id"] = f"WBS-{module_code}-{idx+1:03d}"
            logger.warning(
                "parse_wbs_missing_id_generated",
                module=module,
                generated_id=parsed_task["wbs_id"],
                task_title=parsed_task["task"][:50],
            )

        parsed.append(parsed_task)

    return parsed


# ---------------------------------------------------------------------------
# WBS to Internal Task Format Transformer
# ---------------------------------------------------------------------------


def transform_wbs_to_internal(wbs_tasks: list[dict], test_cases: dict | None = None) -> list[dict]:
    """Transform WBS format tasks to internal pipeline format.

    Args:
        wbs_tasks: List of WBS task dicts.
        test_cases: Optional dict of test cases by agent.

    Returns:
        List of internal task dicts ready for the pipeline.
    """
    internal_tasks = []

    type_to_skill = {
        "backend": "backend",
        "frontend": "frontend",
        "database": "database",
        "qa": "qa",
        "devops": "backend",
        "docs": "backend",
        "design": "frontend",
        "mobile": "frontend",
    }

    # Build test case lookup
    task_tests = {}
    if test_cases:
        for agent, agent_data in test_cases.items():
            if isinstance(agent_data, dict):
                for tc in agent_data.get("test_cases", []):
                    task_id = tc.get("task_id", "")
                    if task_id:
                        if task_id not in task_tests:
                            task_tests[task_id] = []
                        task_tests[task_id].append(tc)

    for wbs_task in wbs_tasks:
        wbs_id = str(wbs_task.get("wbs_id", wbs_task.get("id", ""))).strip()
        if not wbs_id:
            continue

        # Normalize ID to snake_case
        internal_id = wbs_id.lower().replace("-", "_")

        # Map type to skill
        task_type = str(wbs_task.get("type", "backend")).lower()
        skill = type_to_skill.get(task_type, "backend")

        # Parse dependencies
        deps = wbs_task.get("dependencies", [])
        if isinstance(deps, str):
            deps = [d.strip() for d in deps.split(",") if d.strip()]
        depends_on = [d.lower().replace("-", "_") for d in deps if d]

        # Ensure acceptance_criteria is a list
        ac = wbs_task.get("acceptance_criteria", "")
        if isinstance(ac, str):
            acceptance_criteria = [ac] if ac else ["Task completed successfully"]
        elif isinstance(ac, list):
            acceptance_criteria = ac if ac else ["Task completed successfully"]
        else:
            acceptance_criteria = ["Task completed successfully"]

        # Get test cases for this task
        tests = task_tests.get(wbs_id, [])
        test_summary = []
        for tc in tests[:5]:  # Max 5 test cases per task
            test_summary.append({
                "test_id": tc.get("test_id", ""),
                "test_name": tc.get("test_name", ""),
                "type": tc.get("type", "Unit"),
                "priority": tc.get("priority", "Medium"),
            })

        # Build req_ids list
        req_ids = wbs_task.get("req_ids", [])
        if isinstance(req_ids, str):
            req_ids = [req_ids] if req_ids else []
        req_id_str = wbs_task.get("req_id", "")
        if req_id_str and req_id_str not in req_ids:
            req_ids.append(req_id_str)

        internal_task = {
            "id": internal_id,
            "title": str(wbs_task.get("task", wbs_task.get("title", "Untitled"))),
            "description": str(wbs_task.get("description", "")),
            "skill_required": skill,
            "acceptance_criteria": acceptance_criteria,
            "depends_on": depends_on,
            # Preserved metadata
            "wbs_id": wbs_id,
            "priority": str(wbs_task.get("priority", "Should Have")),
            "story_points": wbs_task.get("story_points", 3),
            "effort_days": wbs_task.get("effort_days", 1),
            "req_ids": req_ids,
            "module": wbs_task.get("module", ""),
            "role": wbs_task.get("role", "Developer"),
            "technical_notes": wbs_task.get("technical_notes", ""),
            "test_cases": test_summary,
        }

        internal_tasks.append(internal_task)

    return internal_tasks


# ---------------------------------------------------------------------------
# Main Node
# ---------------------------------------------------------------------------


async def brd_to_wbs_node(state: PlatformState) -> dict[str, Any]:
    """Multi-stage BRD to WBS pipeline matching notebook quality.

    Runs through: Requirements → Scope → Proposal → Architecture → SOW → WBS → Tests → Transform

    Args:
        state: Current PlatformState with BRD in original_prompt.

    Returns:
        Partial state dict with task_dag, project_summary, etc.
    """
    project_id = state.get("project_id", "")
    brd_text = state.get("original_prompt", "")
    plan_feedback = state.get("plan_feedback", "")

    is_replan = bool(plan_feedback)
    feedback_note = f"\n\nPREVIOUS FEEDBACK TO ADDRESS:\n{plan_feedback}" if is_replan else ""

    logger.info(
        "brd_to_wbs_start",
        project_id=project_id,
        is_replan=is_replan,
        brd_length=len(brd_text),
    )

    # ══════════════════════════════════════════════════════════════════════
    # Stage 1: Requirements Extraction (Agent 2)
    # ══════════════════════════════════════════════════════════════════════
    logger.info("brd_to_wbs_stage", project_id=project_id, stage="requirements")

    req_user = f"Extract requirements from this BRD:{feedback_note}\n\n=== BRD ===\n{brd_text}\n=== END BRD ==="
    requirements = await _call_llm(_SYS_REQ, req_user, max_tokens=4000)

    if not requirements:
        return {
            "error_message": "Failed to extract requirements from BRD",
            "project_status": ProjectStatus.FAILED,
        }

    modules = requirements.get("modules", [])
    if not modules:
        # Try to extract modules from functional requirements
        modules = list(set(
            req.get("module", "core")
            for req in requirements.get("functional_requirements", [])
        ))
        if not modules:
            modules = ["core"]

    logger.info("brd_to_wbs_requirements_done", project_id=project_id, modules=modules)

    # ══════════════════════════════════════════════════════════════════════
    # Stage 2: Scope Definition (Agent 3)
    # ══════════════════════════════════════════════════════════════════════
    logger.info("brd_to_wbs_stage", project_id=project_id, stage="scope")

    scope_user = (
        f"Define scope based on BRD and requirements:{feedback_note}\n\n"
        f"=== BRD ===\n{brd_text}\n=== END BRD ===\n\n"
        f"=== REQUIREMENTS ===\n{json.dumps(requirements, indent=2)}\n=== END REQUIREMENTS ==="
    )
    scope = await _call_llm(_SYS_SCOPE, scope_user, max_tokens=4000)  # Increased from 3000

    if not scope:
        logger.warning("brd_to_wbs_scope_failed", project_id=project_id)
        # Fallback: assume everything in requirements is in scope
        # Better to generate tasks than to mark everything out of scope
        scope = {
            "in_scope": [
                {
                    "req_id": req.get("id"),
                    "feature": req.get("requirement", "Feature"),
                    "description": req.get("requirement", ""),
                    "acceptance_criteria": "To be defined",
                    "phase": 1,
                    "priority": req.get("priority", "Should Have")
                }
                for req in requirements.get("functional_requirements", [])
            ],
            "out_of_scope": [],
            "assumptions": [],
        }
        logger.info(
            "brd_to_wbs_scope_defaulted",
            project_id=project_id,
            in_scope_count=len(scope["in_scope"]),
        )

    logger.info("brd_to_wbs_scope_done", project_id=project_id, in_scope_count=len(scope.get("in_scope", [])))

    # ══════════════════════════════════════════════════════════════════════
    # Stage 3: Proposal Generation (Agent 3.5)
    # ══════════════════════════════════════════════════════════════════════
    logger.info("brd_to_wbs_stage", project_id=project_id, stage="proposal")

    proposal_user = (
        f"Create client proposal based on BRD, requirements, and scope:{feedback_note}\n\n"
        f"=== BRD ===\n{brd_text}\n=== END BRD ===\n\n"
        f"=== REQUIREMENTS ===\n{json.dumps(requirements, indent=2)}\n=== END REQUIREMENTS ===\n\n"
        f"=== SCOPE ===\n{json.dumps(scope, indent=2)}\n=== END SCOPE ==="
    )
    proposal = await _call_llm(_SYS_PROPOSAL, proposal_user, max_tokens=3000)

    if not proposal:
        logger.warning("brd_to_wbs_proposal_failed", project_id=project_id)
        proposal = {"executive_summary": "", "deliverables": [], "timeline": {}}

    logger.info("brd_to_wbs_proposal_done", project_id=project_id)

    # ══════════════════════════════════════════════════════════════════════
    # Stage 4: Architecture Design (Agent 4)
    # ══════════════════════════════════════════════════════════════════════
    logger.info("brd_to_wbs_stage", project_id=project_id, stage="architecture")

    arch_user = (
        f"Design architecture for in-scope features:{feedback_note}\n\n"
        f"=== BRD ===\n{brd_text}\n=== END BRD ===\n\n"
        f"=== REQUIREMENTS ===\n{json.dumps(requirements, indent=2)}\n=== END REQUIREMENTS ===\n\n"
        f"=== SCOPE ===\n{json.dumps(scope, indent=2)}\n=== END SCOPE ===\n\n"
        f"=== PROPOSAL ===\n{json.dumps(proposal, indent=2)}\n=== END PROPOSAL ==="
    )
    architecture = await _call_llm(_SYS_ARCH, arch_user, max_tokens=4000)

    if not architecture:
        logger.warning("brd_to_wbs_arch_failed", project_id=project_id)
        architecture = {"components": [], "tech_stack": {}, "apis": []}

    logger.info("brd_to_wbs_arch_done", project_id=project_id, components=len(architecture.get("components", [])))

    # ══════════════════════════════════════════════════════════════════════
    # Stage 5: SOW Generation (Agent 5)
    # ══════════════════════════════════════════════════════════════════════
    logger.info("brd_to_wbs_stage", project_id=project_id, stage="sow")

    sow_user = (
        f"Generate Statement of Work:{feedback_note}\n\n"
        f"=== BRD ===\n{brd_text}\n=== END BRD ===\n\n"
        f"=== REQUIREMENTS ===\n{json.dumps(requirements, indent=2)}\n=== END REQUIREMENTS ===\n\n"
        f"=== SCOPE ===\n{json.dumps(scope, indent=2)}\n=== END SCOPE ===\n\n"
        f"=== PROPOSAL ===\n{json.dumps(proposal, indent=2)}\n=== END PROPOSAL ===\n\n"
        f"=== ARCHITECTURE ===\n{json.dumps(architecture, indent=2)}\n=== END ARCHITECTURE ==="
    )
    sow = await _call_llm(_SYS_SOW, sow_user, max_tokens=4000, model_override=_get_sow_model())

    if not sow:
        logger.warning("brd_to_wbs_sow_failed", project_id=project_id)
        sow = {"deliverables": [], "milestones": [], "timeline": {}}

    logger.info("brd_to_wbs_sow_done", project_id=project_id, milestones=len(sow.get("milestones", [])))

    # ══════════════════════════════════════════════════════════════════════
    # Stage 6: WBS Generation per Module (Agent 6)
    # ══════════════════════════════════════════════════════════════════════
    logger.info("brd_to_wbs_stage", project_id=project_id, stage="wbs")

    all_wbs_tasks = []
    base_context = (
        f"Scope matrix is your ONLY source of truth for what tasks exist.{feedback_note}\n\n"
        f"=== SCOPE MATRIX ===\n{json.dumps(scope, indent=2)}\n=== END SCOPE ===\n\n"
        f"=== REQUIREMENTS REGISTER ===\n{json.dumps(requirements, indent=2)}\n=== END REQUIREMENTS ===\n\n"
        f"=== ARCHITECTURE DOCUMENT ===\n{json.dumps(architecture, indent=2)}\n=== END ARCHITECTURE ===\n\n"
        f"=== SOW ===\n{json.dumps(sow, indent=2)}\n=== END SOW ==="
    )

    for module in modules:
        logger.info("brd_to_wbs_module", project_id=project_id, module=module)

        # Generate module code (first 4 uppercase letters)
        module_code = re.sub(r'[^A-Z]', '', module.upper())[:4] or "CORE"

        wbs_user = (
            f"{base_context}\n\n"
            f"Generate WBS for module: **{module}**\n\n"
            f"Check the scope matrix: is {module} explicitly marked OUT OF SCOPE?\n"
            f"- If explicitly OUT OF SCOPE → return: {{'tasks': [], 'summary': 'OUT OF SCOPE — no tasks generated'}}\n"
            f"- Otherwise (including if scope is unclear) → generate tasks:\n\n"
            f"Task IDs: WBS-{module_code}-001 format\n"
            f"Type: Design/Backend/Frontend/Mobile/Database/DevOps/QA/Docs\n"
            f"Priority: Must Have/Should Have/Could Have\n"
            f"Story Points: 1/2/3/5/8/13\n"
            f"Max 12 tasks per module. Concise but specific descriptions.\n\n"
            f"IMPORTANT: Generate tasks even if scope matrix is minimal or incomplete.\n"
            f"Use requirements and architecture to infer what tasks are needed."
        )

        wbs_result = await _call_llm(_SYS_WBS, wbs_user, max_tokens=3000)

        if wbs_result:
            # Check if module is out of scope
            summary = wbs_result.get("summary", "") if isinstance(wbs_result, dict) else ""
            if "OUT OF SCOPE" in str(summary).upper():
                logger.info(
                    "brd_to_wbs_module_out_of_scope",
                    project_id=project_id,
                    module=module,
                )
                continue

            module_tasks = parse_wbs_tasks(wbs_result, module)

            # Debug log if no tasks parsed
            if not module_tasks:
                logger.warning(
                    "brd_to_wbs_module_no_tasks_parsed",
                    project_id=project_id,
                    module=module,
                    response_type=type(wbs_result).__name__,
                    has_tasks_key="tasks" in wbs_result if isinstance(wbs_result, dict) else False,
                    response_preview=str(wbs_result)[:500],
                )

            all_wbs_tasks.extend(module_tasks)
            logger.info(
                "brd_to_wbs_module_done",
                project_id=project_id,
                module=module,
                task_count=len(module_tasks),
            )

    if not all_wbs_tasks:
        return {
            "error_message": "WBS generation returned no tasks for any module",
            "project_status": ProjectStatus.FAILED,
        }

    logger.info("brd_to_wbs_wbs_done", project_id=project_id, total_tasks=len(all_wbs_tasks))

    # ══════════════════════════════════════════════════════════════════════
    # Stage 7: Test Case Generation per Agent
    # ══════════════════════════════════════════════════════════════════════
    logger.info("brd_to_wbs_stage", project_id=project_id, stage="test_cases")

    # Group tasks by agent/skill
    tasks_by_agent: dict[str, list[dict]] = {}
    type_to_agent = {
        "backend": "backend",
        "frontend": "frontend",
        "database": "database",
        "qa": "qa",
        "devops": "backend",
        "design": "frontend",
        "mobile": "frontend",
        "docs": "backend",
    }

    for task in all_wbs_tasks:
        task_type = task.get("type", "Backend").lower()
        agent = type_to_agent.get(task_type, "backend")
        if agent not in tasks_by_agent:
            tasks_by_agent[agent] = []
        tasks_by_agent[agent].append(task)

    test_cases_by_agent: dict[str, dict] = {}

    for agent_name, agent_tasks in tasks_by_agent.items():
        if not agent_tasks:
            continue

        logger.info("brd_to_wbs_test_gen", project_id=project_id, agent=agent_name, task_count=len(agent_tasks))

        prefix = agent_name[:2].upper()
        task_ref = "\n".join([
            f"- **{t['wbs_id']}** | {t['task']} | Type: {t['type']} | "
            f"Effort: {t['effort_days']}d | Priority: {t['priority']} | "
            f"Accept: {str(t['acceptance_criteria'])[:100]}"
            for t in agent_tasks[:10]  # Max 10 tasks per batch
        ])

        test_user = (
            f"Agent: **{agent_name.capitalize()} Agent**\n\n"
            f"=== ARCHITECTURE CONTEXT ===\n"
            f"{json.dumps(architecture, indent=2)[:2000]}\n=== END ===\n\n"
            f"Generate test cases for these {len(agent_tasks)} tasks.\n"
            f"For EACH task: minimum 3 test cases "
            f"(happy path, edge case, failure scenario).\n\n"
            f"Test ID format: TC-{prefix}-001, TC-{prefix}-002 ...\n"
            f"Type: Unit / Integration / E2E / Contract / Performance\n"
            f"Priority: Critical / High / Medium / Low\n\n"
            f"TASKS:\n{task_ref}"
        )

        test_result = await _call_llm(_SYS_TEST, test_user, max_tokens=3000, model_override=_get_test_model())

        if test_result:
            test_cases_by_agent[agent_name] = test_result
            logger.info(
                "brd_to_wbs_test_done",
                project_id=project_id,
                agent=agent_name,
                test_count=len(test_result.get("test_cases", [])),
            )

    # ══════════════════════════════════════════════════════════════════════
    # Stage 8: Project Analysis
    # ══════════════════════════════════════════════════════════════════════
    logger.info("brd_to_wbs_stage", project_id=project_id, stage="analysis")

    analysis_user = (
        f"Generate project analysis from WBS tasks:\n\n"
        f"=== WBS TASKS ===\n{json.dumps(all_wbs_tasks, indent=2)}\n=== END WBS ===\n\n"
        f"=== SOW MILESTONES ===\n{json.dumps(sow.get('milestones', []), indent=2)}\n=== END MILESTONES ==="
    )

    analysis = await _call_llm(_SYS_ANALYSIS, analysis_user, max_tokens=2000)

    if not analysis:
        # Generate basic analysis fallback
        analysis = {
            "overall_totals": {
                "total_tasks": len(all_wbs_tasks),
                "total_story_points": sum(t.get("story_points", 3) for t in all_wbs_tasks),
                "total_effort_days": sum(t.get("effort_days", 1) for t in all_wbs_tasks),
            },
            "agent_distribution": {
                agent: {"task_count": len(tasks)}
                for agent, tasks in tasks_by_agent.items()
            },
        }

    # ══════════════════════════════════════════════════════════════════════
    # Stage 9: Transform to Internal Format
    # ══════════════════════════════════════════════════════════════════════
    try:
        internal_tasks = transform_wbs_to_internal(all_wbs_tasks, test_cases_by_agent)
    except Exception as exc:
        logger.error("brd_to_wbs_transform_failed", project_id=project_id, error=str(exc))
        return {
            "error_message": f"WBS transformation failed: {exc}",
            "project_status": ProjectStatus.FAILED,
        }

    # Validate task schema
    schema_errors = _validate_task_schema(internal_tasks)
    if schema_errors:
        error_msg = "Task schema validation failed: " + "; ".join(schema_errors[:5])
        logger.error("brd_to_wbs_schema_invalid", project_id=project_id, errors=schema_errors)
        return {
            "error_message": error_msg,
            "project_status": ProjectStatus.FAILED,
        }

    # Validate DAG
    try:
        task_graph = TaskGraph()
        task_graph.build_from_dag(internal_tasks)
        logger.info(
            "task_dag_built",
            total_tasks=len(internal_tasks),
            total_dependencies=sum(len(t.get("depends_on", [])) for t in internal_tasks),
        )
    except InvalidDAGError as exc:
        logger.error("brd_to_wbs_dag_invalid", project_id=project_id, reason=exc.reason)
        return {
            "error_message": f"Task DAG invalid: {exc}",
            "project_status": ProjectStatus.FAILED,
        }

    # Build project summary
    project_summary = _build_project_summary(
        requirements=requirements,
        scope=scope,
        proposal=proposal,
        architecture=architecture,
        sow=sow,
        analysis=analysis,
        modules=modules,
        task_count=len(internal_tasks),
    )

    logger.info(
        "brd_to_wbs_complete",
        project_id=project_id,
        task_count=len(internal_tasks),
        modules=modules,
    )

    return {
        "task_dag": internal_tasks,
        "project_summary": project_summary,
        "project_status": ProjectStatus.PLANNING,
        "plan_approved": False,
        "error_message": None,
        # Extended metadata for HITL review
        "_metadata": {
            "requirements": requirements,
            "scope": scope,
            "proposal": proposal,
            "architecture": architecture,
            "sow": sow,
            "analysis": analysis,
            "test_cases": test_cases_by_agent,
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_project_summary(
    requirements: dict,
    scope: dict,
    proposal: dict,
    architecture: dict,
    sow: dict,
    analysis: dict,
    modules: list[str],
    task_count: int,
) -> str:
    """Build a comprehensive project summary from all pipeline outputs."""

    # Get executive summary from proposal
    exec_summary = proposal.get("executive_summary", "")

    # Get architecture pattern
    arch_pattern = architecture.get("architecture_pattern", "Not specified")
    tech_stack = architecture.get("tech_stack", {})

    # Get timeline from SOW or proposal
    timeline = sow.get("timeline", {}) or proposal.get("timeline", {})
    duration = timeline.get("total_weeks", timeline.get("total_duration_weeks", "TBD"))

    # Get totals from analysis
    totals = analysis.get("overall_totals", {})
    total_effort = totals.get("total_effort_days", "TBD")
    total_points = totals.get("total_story_points", "TBD")

    # Get risk count
    risks = proposal.get("risks", [])

    # Build summary
    summary_parts = []

    if exec_summary:
        summary_parts.append(exec_summary[:500])

    summary_parts.append(
        f"\n\n**Project Metrics:**\n"
        f"- Modules: {', '.join(modules)}\n"
        f"- Total Tasks: {task_count}\n"
        f"- Total Effort: {total_effort} days\n"
        f"- Story Points: {total_points}\n"
        f"- Timeline: {duration} weeks\n"
        f"- Architecture: {arch_pattern}\n"
        f"- Identified Risks: {len(risks)}"
    )

    if tech_stack:
        backend = tech_stack.get("backend", {})
        frontend = tech_stack.get("frontend", {})
        db = tech_stack.get("database", {})

        stack_str = []
        if isinstance(backend, dict):
            stack_str.append(f"Backend: {backend.get('framework', 'TBD')}")
        if isinstance(frontend, dict):
            stack_str.append(f"Frontend: {frontend.get('framework', 'TBD')}")
        if isinstance(db, dict):
            stack_str.append(f"Database: {db.get('product', 'TBD')}")

        if stack_str:
            summary_parts.append(f"\n**Tech Stack:** {' | '.join(stack_str)}")

    return "".join(summary_parts)


def _validate_task_schema(tasks: list[dict]) -> list[str]:
    """Check each task for required fields and correct types."""
    if not tasks:
        return ["tasks list is empty"]

    errors: list[str] = []
    seen_ids: set[str] = set()

    for idx, task in enumerate(tasks):
        if not isinstance(task, dict):
            errors.append(f"task[{idx}] is not a dict")
            continue

        tid = task.get("id", f"(index {idx})")

        if tid in seen_ids:
            errors.append(f"duplicate task ID '{tid}'")
        seen_ids.add(tid)

        for field in _REQUIRED_TASK_FIELDS:
            if field not in task:
                errors.append(f"task '{tid}' missing '{field}'")

        ac = task.get("acceptance_criteria")
        if ac is not None and (not isinstance(ac, list) or len(ac) == 0):
            errors.append(f"task '{tid}' acceptance_criteria must be a non-empty list")

        deps = task.get("depends_on")
        if deps is not None and not isinstance(deps, list):
            errors.append(f"task '{tid}' depends_on must be a list")

    return errors
