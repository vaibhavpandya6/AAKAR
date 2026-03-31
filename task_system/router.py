"""Task routing — maps tasks to the correct agent skill via registry + keyword scoring."""

import re
from typing import Optional

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Skill → keyword registry
# ---------------------------------------------------------------------------
# Each key is the canonical skill name returned to callers.
# Each list is scored against the lowercased task title + description.
# ---------------------------------------------------------------------------

SKILL_REGISTRY: dict[str, list[str]] = {
    "bootstrap": [
        "setup",
        "configuration",
        "config",
        "requirements.txt",
        "package.json",
        "dockerfile",
        "docker-compose",
        "environment",
        "env",
        ".env",
        "initialize",
        "scaffold",
        "boilerplate",
        "project setup",
        "dependencies",
        "devcontainer",
        "makefile",
        "gitignore",
        "tsconfig",
        "pyproject",
        "poetry",
        "pipfile",
        "yarn",
        "npm init",
        "vite",
        "webpack",
        "eslint",
        "prettier",
        "linter",
        "formatter",
    ],
    "backend": [
        "api",
        "endpoint",
        "server",
        "authentication",
        "jwt",
        "middleware",
        "node.js",
        "python",
        "fastapi",
        "express",
        "rest",
        "graphql",
        "websocket",
        "route",
        "handler",
        "controller",
        "service",
        "worker",
        "cron",
        "task",
        "celery",
        "redis",
        "cache",
        "queue",
        "background",
        "async",
        "auth",
        "login",
        "register",
        "password",
        "token",
        "session",
        "oauth",
        "permission",
        "role",
    ],
    "frontend": [
        "react",
        "component",
        "ui",
        "interface",
        "css",
        "typescript",
        "page",
        "form",
        "button",
        "state",
        "hook",
        "tailwind",
        "design",
        "view",
        "screen",
        "modal",
        "dialog",
        "layout",
        "navigation",
        "menu",
        "sidebar",
        "navbar",
        "header",
        "footer",
        "table",
        "list",
        "grid",
        "chart",
        "dashboard",
        "animation",
        "style",
        "responsive",
        "mobile",
        "accessibility",
        "jsx",
        "tsx",
        "html",
        "dom",
        "event",
        "click",
        "input",
        "select",
        "dropdown",
    ],
    "database": [
        "schema",
        "migration",
        "model",
        "table",
        "index",
        "query",
        "mongodb",
        "postgresql",
        "sql",
        "orm",
        "relationship",
        "seed",
        "column",
        "row",
        "record",
        "field",
        "foreign key",
        "primary key",
        "constraint",
        "transaction",
        "join",
        "aggregate",
        "view",
        "trigger",
        "procedure",
        "function",
        "alembic",
        "sequelize",
        "prisma",
        "typeorm",
        "sqlalchemy",
        "nosql",
        "document",
        "collection",
        "database",
        "db",
        "store",
        "persist",
        "data model",
        "entity",
    ],
    "qa": [
        "test",
        "testing",
        "spec",
        "assert",
        "coverage",
        "bug",
        "e2e",
        "unit",
        "integration",
        "mock",
        "fixture",
        "security",
        "audit",
        "verify",
        "validate",
        "check",
        "quality",
        "regression",
        "smoke",
        "load",
        "performance",
        "stress",
        "pytest",
        "jest",
        "cypress",
        "selenium",
        "playwright",
        "supertest",
        "httpx",
        "scan",
        "vulnerability",
        "injection",
        "xss",
        "csrf",
        "penetration",
    ],
}

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class AgentRouter:
    """Routes tasks to the appropriate agent skill.

    Decision order:
      1. Use ``task["skill_required"]`` if it matches a known skill (exact or prefix).
      2. Score each skill by keyword matches in title + description.
      3. Tie-break: backend wins over other categories (it is the most common).
    """

    DEFAULT_SKILL = "backend"

    def route_task(self, task: dict) -> str:
        """Determine the skill required to execute a task.

        Args:
            task: Task dict. Expected keys: id, title, description,
                  skill_required (optional).

        Returns:
            Canonical skill name: one of bootstrap | backend | frontend | database | qa.
        """
        task_id = task.get("id", "unknown")

        # ── Fast path: explicit skill_required ─────────────────────────────
        declared = task.get("skill_required", "")
        if declared:
            declared_lower = declared.lower().strip()
            # Exact match or prefix match (e.g. "backend", "backend/api")
            for skill in SKILL_REGISTRY:
                if declared_lower == skill or declared_lower.startswith(skill):
                    logger.info(
                        "task_routed_by_declaration",
                        task_id=task_id,
                        skill=skill,
                        declared=declared,
                    )
                    return skill

        # ── Keyword scoring ────────────────────────────────────────────────
        title = task.get("title", "")
        description = task.get("description", "")
        corpus = _tokenise(f"{title} {description}")

        scores: dict[str, int] = {}
        matched_keywords: dict[str, list[str]] = {}

        for skill, keywords in SKILL_REGISTRY.items():
            hits = [kw for kw in keywords if kw in corpus]
            scores[skill] = len(hits)
            matched_keywords[skill] = hits

        best_skill = max(scores, key=lambda s: scores[s])
        best_score = scores[best_skill]

        if best_score == 0:
            # Nothing matched — fall back to default
            logger.warning(
                "task_route_no_match_fallback",
                task_id=task_id,
                title=title[:80],
                fallback=self.DEFAULT_SKILL,
            )
            return self.DEFAULT_SKILL

        logger.info(
            "task_routed_by_keywords",
            task_id=task_id,
            skill=best_skill,
            score=best_score,
            matched=matched_keywords[best_skill][:8],  # log up to 8 matched words
            runner_up={
                s: scores[s]
                for s in scores
                if s != best_skill and scores[s] > 0
            },
        )
        return best_skill

    @staticmethod
    def get_agent_stream(skill: str) -> str:
        """Return the Redis stream name for a given skill's agent.

        Args:
            skill: Canonical skill name.

        Returns:
            Stream key, e.g. ``stream:backend_agent``.
        """
        return f"stream:{skill}_agent"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tokenise(text: str) -> str:
    """Lowercase and normalise text for keyword matching.

    Multi-word keywords (e.g. "foreign key") must survive as substrings, so
    we only strip punctuation and collapse whitespace rather than splitting.
    """
    # lower
    text = text.lower()
    # remove punctuation except spaces and hyphens (keep "node.js" etc)
    text = re.sub(r"[^\w\s.\-]", " ", text)
    # collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text
