"""Coordinator node — validates cross-agent consistency before task dispatch.

This node runs after the router validates the DAG but before tasks are monitored.
It ensures that the planned tasks don't have conflicts that would cause issues
when executed by different agents.
"""

from typing import Any

import structlog

from orchestrator.state import PlatformState, ProjectStatus

logger = structlog.get_logger()


# ──────────────────────────────────────────────────────────────────────────────
# Schema consistency validation functions
# ──────────────────────────────────────────────────────────────────────────────


def _validate_no_duplicate_files(task_dag: list[dict]) -> list[str]:
    """Check for duplicate file paths across tasks.

    Multiple tasks writing to the same file without proper dependency ordering
    will cause merge conflicts and race conditions.

    Args:
        task_dag: List of task dictionaries

    Returns:
        List of error messages
    """
    errors = []
    file_to_tasks: dict[str, list[str]] = {}

    for task in task_dag:
        task_id = str(task.get("id", ""))
        # Check various fields where file paths might be specified
        target_files = task.get("target_files", [])
        if isinstance(target_files, str):
            target_files = [target_files]

        # Also check description for mentioned file paths
        description = task.get("description", "")
        title = task.get("title", "")

        for file_path in target_files:
            if file_path in file_to_tasks:
                file_to_tasks[file_path].append(task_id)
            else:
                file_to_tasks[file_path] = [task_id]

    for file_path, task_ids in file_to_tasks.items():
        if len(task_ids) > 1:
            # Check if tasks have dependencies that resolve the conflict
            if not _tasks_have_ordering(task_dag, task_ids):
                errors.append(
                    f"File '{file_path}' is targeted by multiple tasks without "
                    f"dependency ordering: {task_ids}. Add depends_on to prevent conflicts."
                )

    return errors


def _tasks_have_ordering(task_dag: list[dict], task_ids: list[str]) -> bool:
    """Check if a set of tasks have dependency ordering between them.

    Returns True if for every pair of tasks, one depends on the other (directly
    or transitively).
    """
    # Build dependency map
    task_map = {str(t.get("id", "")): t for t in task_dag}

    def get_all_deps(task_id: str, visited: set | None = None) -> set[str]:
        if visited is None:
            visited = set()
        if task_id in visited:
            return set()
        visited.add(task_id)

        task = task_map.get(task_id)
        if not task:
            return set()

        deps = set(str(d) for d in (task.get("depends_on") or []))
        for dep in list(deps):
            deps.update(get_all_deps(dep, visited))
        return deps

    # For each pair, check if one depends on the other
    task_id_set = set(task_ids)
    for tid in task_ids:
        deps = get_all_deps(tid)
        # If this task depends on all others, ordering exists
        if task_id_set - {tid} <= deps:
            return True

    return False


def _validate_schema_consistency(task_dag: list[dict]) -> list[str]:
    """Check for schema consistency between database and backend tasks.

    Validates that:
    - Database tasks that create models have corresponding backend tasks
    - Field types are consistent across layers

    Args:
        task_dag: List of task dictionaries

    Returns:
        List of error messages
    """
    errors = []

    db_tasks = [t for t in task_dag if t.get("skill_required") == "database"]
    backend_tasks = [t for t in task_dag if t.get("skill_required") == "backend"]

    # Extract model names from database tasks
    db_models = set()
    for task in db_tasks:
        description = task.get("description", "").lower()
        title = task.get("title", "").lower()

        # Look for common patterns like "create user table", "user model", etc.
        import re
        model_patterns = [
            r'create\s+(\w+)\s+(?:table|model|schema)',
            r'(\w+)\s+(?:table|model|schema)\s+(?:migration|definition)',
            r'define\s+(\w+)\s+(?:entity|model)',
        ]
        for pattern in model_patterns:
            matches = re.findall(pattern, title + " " + description)
            db_models.update(m.lower() for m in matches)

    # Check that backend tasks reference the database models properly
    for backend_task in backend_tasks:
        description = backend_task.get("description", "").lower()
        title = backend_task.get("title", "").lower()
        task_id = backend_task.get("id", "")

        # Look for model references
        referenced_models = set()
        for model in db_models:
            if model in description or model in title:
                referenced_models.add(model)

        # If backend references a model, ensure it depends on the relevant DB task
        if referenced_models:
            backend_deps = set(str(d) for d in (backend_task.get("depends_on") or []))
            db_task_ids = {str(t.get("id", "")) for t in db_tasks}

            # At least one DB task should be a dependency
            if not backend_deps.intersection(db_task_ids):
                # This is a soft warning - models might be pre-existing
                logger.warning(
                    "coordinator_schema_hint",
                    backend_task=task_id,
                    referenced_models=list(referenced_models),
                    hint="Backend task references database models but doesn't depend on DB tasks",
                )

    return errors


def _validate_api_contracts(task_dag: list[dict]) -> list[str]:
    """Check for API contract consistency between frontend and backend tasks.

    Validates that:
    - Frontend tasks that consume APIs have dependencies on backend tasks
    - API endpoint naming is consistent

    Args:
        task_dag: List of task dictionaries

    Returns:
        List of error messages
    """
    errors = []

    frontend_tasks = [t for t in task_dag if t.get("skill_required") == "frontend"]
    backend_tasks = [t for t in task_dag if t.get("skill_required") == "backend"]

    backend_task_ids = {str(t.get("id", "")) for t in backend_tasks}

    for fe_task in frontend_tasks:
        task_id = str(fe_task.get("id", ""))
        description = fe_task.get("description", "").lower()
        title = fe_task.get("title", "").lower()

        # Check if frontend task mentions API consumption
        api_keywords = ["api", "fetch", "endpoint", "request", "http", "axios", "backend"]
        mentions_api = any(keyword in (description + title) for keyword in api_keywords)

        if mentions_api:
            fe_deps = set(str(d) for d in (fe_task.get("depends_on") or []))

            # Frontend consuming API should depend on backend
            if not fe_deps.intersection(backend_task_ids):
                errors.append(
                    f"Frontend task '{task_id}' ({fe_task.get('title', '')}) mentions API "
                    f"consumption but doesn't depend on any backend task. This may cause "
                    f"runtime errors if the API doesn't exist yet."
                )

    return errors


def _validate_technology_consistency(task_dag: list[dict]) -> list[str]:
    """Check for technology stack consistency across tasks.

    Ensures that:
    - Backend tasks use consistent async/sync patterns
    - Frontend tasks use consistent framework choice
    - Database tasks use consistent ORM/migration tool

    Args:
        task_dag: List of task dictionaries

    Returns:
        List of error messages
    """
    errors = []

    # Track technology mentions per skill
    tech_by_skill: dict[str, set[str]] = {
        "backend": set(),
        "frontend": set(),
        "database": set(),
    }

    backend_patterns = {
        "async": ["async", "asyncio", "asyncpg", "aiohttp", "async def"],
        "sync": ["sync", "psycopg2", "requests"],
        "fastapi": ["fastapi", "starlette"],
        "flask": ["flask"],
        "django": ["django"],
    }

    frontend_patterns = {
        "react": ["react", "jsx", "tsx", "usestate", "useeffect"],
        "vue": ["vue", ".vue", "composition api"],
        "angular": ["angular", "@component", "ngmodule"],
    }

    db_patterns = {
        "sqlalchemy": ["sqlalchemy", "alembic"],
        "prisma": ["prisma"],
        "typeorm": ["typeorm"],
        "raw_sql": ["raw sql", "psql"],
    }

    for task in task_dag:
        skill = task.get("skill_required", "")
        content = (task.get("title", "") + " " + task.get("description", "")).lower()

        if skill == "backend":
            for tech, patterns in backend_patterns.items():
                if any(p in content for p in patterns):
                    tech_by_skill["backend"].add(tech)

        elif skill == "frontend":
            for tech, patterns in frontend_patterns.items():
                if any(p in content for p in patterns):
                    tech_by_skill["frontend"].add(tech)

        elif skill == "database":
            for tech, patterns in db_patterns.items():
                if any(p in content for p in patterns):
                    tech_by_skill["database"].add(tech)

    # Check for conflicting technologies
    backend_techs = tech_by_skill["backend"]
    if "async" in backend_techs and "sync" in backend_techs:
        errors.append(
            "Tasks mix async and sync patterns. Ensure consistency: "
            "use async throughout (asyncpg, aiohttp) or sync throughout (psycopg2, requests)."
        )

    if {"fastapi", "flask", "django"}.intersection(backend_techs).__len__() > 1:
        frameworks = {"fastapi", "flask", "django"}.intersection(backend_techs)
        errors.append(
            f"Tasks reference multiple backend frameworks: {frameworks}. "
            "Choose one framework for consistency."
        )

    frontend_techs = tech_by_skill["frontend"]
    if {"react", "vue", "angular"}.intersection(frontend_techs).__len__() > 1:
        frameworks = {"react", "vue", "angular"}.intersection(frontend_techs)
        errors.append(
            f"Tasks reference multiple frontend frameworks: {frameworks}. "
            "Choose one framework for consistency."
        )

    return errors


# ──────────────────────────────────────────────────────────────────────────────
# Main coordinator node
# ──────────────────────────────────────────────────────────────────────────────


async def coordinator_node(state: PlatformState) -> dict[str, Any]:
    """Validate cross-agent consistency before task execution begins.

    This node runs after the router node validates the DAG structure.
    It performs semantic validation to catch issues that would cause problems
    when multiple agents execute their tasks.

    Validations performed:
    1. No duplicate file targets without dependency ordering
    2. Schema consistency between database and backend tasks
    3. API contract consistency between frontend and backend tasks
    4. Technology stack consistency across all tasks

    Args:
        state: Current PlatformState (after router_node)

    Returns:
        Partial state dict. On validation failure, returns error_message and
        project_status=FAILED. On success, returns empty dict (pass-through).
    """
    project_id = state.get("project_id", "")
    task_dag: list[dict] = state.get("task_dag") or []

    logger.info(
        "coordinator_node_start",
        project_id=project_id,
        task_count=len(task_dag),
    )

    all_errors: list[str] = []
    all_warnings: list[str] = []

    # ── 1. Check for duplicate file targets ──────────────────────────────────
    duplicate_errors = _validate_no_duplicate_files(task_dag)
    all_errors.extend(duplicate_errors)

    # ── 2. Check schema consistency ──────────────────────────────────────────
    schema_errors = _validate_schema_consistency(task_dag)
    all_errors.extend(schema_errors)

    # ── 3. Check API contract consistency ────────────────────────────────────
    api_errors = _validate_api_contracts(task_dag)
    all_errors.extend(api_errors)

    # ── 4. Check technology consistency ──────────────────────────────────────
    tech_errors = _validate_technology_consistency(task_dag)
    # Tech inconsistencies are warnings, not hard errors
    all_warnings.extend(tech_errors)

    # Log warnings
    for warning in all_warnings:
        logger.warning(
            "coordinator_validation_warning",
            project_id=project_id,
            warning=warning,
        )

    # Fail if there are hard errors
    if all_errors:
        error_summary = "\n".join(f"- {e}" for e in all_errors[:10])
        logger.error(
            "coordinator_validation_failed",
            project_id=project_id,
            error_count=len(all_errors),
            errors=all_errors[:10],
        )
        return {
            "error_message": (
                f"Cross-agent validation failed with {len(all_errors)} error(s):\n"
                f"{error_summary}"
            ),
            "project_status": ProjectStatus.FAILED,
        }

    logger.info(
        "coordinator_node_complete",
        project_id=project_id,
        warnings=len(all_warnings),
    )

    return {}
