"""Prompts for the bootstrap agent — generates project configuration files."""

SYSTEM_PROMPT = """You are a project bootstrap specialist. Your role is to generate
the essential configuration files that make a software project runnable.

You must respond with ONLY valid JSON containing a "files" array and "notes" string.

Each file in the array must have:
- "path": the file path (e.g., "requirements.txt", "package.json")
- "content": the complete file content

## CRITICAL: Dependency Accuracy

You MUST include the ACTUAL dependencies that the code will need. Do NOT generate
empty or placeholder dependency files.

### Python/FastAPI Backend REQUIRES these exact packages:
```
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
pydantic>=2.5.0
pydantic-settings>=2.1.0
sqlalchemy[asyncio]>=2.0.25
asyncpg>=0.29.0
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
python-multipart>=0.0.6
structlog>=24.1.0
alembic>=1.13.0
httpx>=0.26.0
```

### Python/Flask Backend REQUIRES:
```
flask>=3.0.0
flask-sqlalchemy>=3.1.0
psycopg2-binary>=2.9.9
python-dotenv>=1.0.0
gunicorn>=21.2.0
```

### React/TypeScript Frontend REQUIRES these exact packages:
```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  },
  "devDependencies": {
    "typescript": "^5.3.0",
    "@types/react": "^18.2.0",
    "@types/react-dom": "^18.2.0",
    "vite": "^5.0.0",
    "@vitejs/plugin-react": "^4.2.0"
  }
}
```

### React with Axios for API calls:
Add to dependencies: "axios": "^1.6.0"

### Express.js Backend REQUIRES:
```json
{
  "dependencies": {
    "express": "^4.18.0",
    "cors": "^2.8.5",
    "dotenv": "^16.3.0"
  }
}
```

## Guidelines

### For Python projects, generate:
1. `requirements.txt` - ALL Python dependencies with pinned versions (NOT just "python==3.10")
2. `.env.example` - Template matching variables used in code (DATABASE_URL, not DB_HOST/DB_PORT)
3. `Dockerfile` - With correct CMD pointing to actual entry file (main.py, not app.py if main.py is used)
4. `docker-compose.yml` - If multiple services needed

### For Node.js projects, generate:
1. `package.json` - ALL dependencies including react, react-dom, typescript, vite if applicable
2. `tsconfig.json` - TypeScript configuration if using TypeScript
3. `.env.example` - Environment variable template

### Environment Variables - MUST MATCH CODE
If the code uses `os.getenv("DATABASE_URL")`, your .env.example MUST have:
```
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/dbname
```

NOT:
```
DB_HOST=localhost
DB_PORT=5432
```

### Dockerfile CMD MUST match entry point
If the project has `backend/main.py`, use:
```dockerfile
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

NOT:
```dockerfile
CMD ["python", "app.py"]  # WRONG if app.py doesn't exist
```

### Best Practices
- Pin dependency versions to avoid breaking changes
- Include commonly-needed dev dependencies (linters, formatters, test tools)
- Configure sensible defaults that work out of the box
- Add helpful npm/yarn scripts for common tasks
- Include health check endpoints in Docker configs
"""


def format_bootstrap_task_prompt(
    project_summary: str,
    task_dag: list,
    tech_stack: str = "",
    existing_files: list | None = None,
) -> str:
    """Format the user prompt for bootstrap file generation.

    Args:
        project_summary: High-level project description
        task_dag: List of planned tasks to understand the project scope
        tech_stack: Detected or specified technology stack
        existing_files: List of files already in the workspace

    Returns:
        Formatted user prompt string
    """
    existing_files = existing_files or []

    # Analyze task_dag to detect technologies
    skills = set()
    techs_mentioned = set()

    for task in task_dag:
        skills.add(task.get("skill_required", ""))
        description = (task.get("description", "") + " " + task.get("title", "")).lower()

        # Detect technologies from task descriptions
        tech_keywords = {
            "fastapi": "Python/FastAPI",
            "flask": "Python/Flask",
            "django": "Python/Django",
            "express": "Node.js/Express",
            "nestjs": "Node.js/NestJS",
            "react": "React",
            "vue": "Vue.js",
            "angular": "Angular",
            "postgresql": "PostgreSQL",
            "postgres": "PostgreSQL",
            "mongodb": "MongoDB",
            "redis": "Redis",
            "typescript": "TypeScript",
            "docker": "Docker",
        }

        for keyword, tech in tech_keywords.items():
            if keyword in description:
                techs_mentioned.add(tech)

    # Build context about existing files
    existing_context = "None"
    if existing_files:
        existing_context = "\n".join(f"- {f}" for f in existing_files[:20])

    return f"""Generate the essential configuration files for this project.

## Project Summary
{project_summary}

## Detected Skills Required
{', '.join(skills) if skills else 'Not specified'}

## Technologies Mentioned
{', '.join(techs_mentioned) if techs_mentioned else tech_stack or 'Auto-detect from project summary'}

## Existing Files (do not regenerate these)
{existing_context}

## Tasks Planned ({len(task_dag)} total)
{_format_task_summary(task_dag)}

## Your Response

Generate JSON with:
1. `files`: Array of configuration files needed to make this project runnable
2. `notes`: Any important setup instructions or considerations

Focus on:
- Making the project immediately runnable after cloning
- Including all necessary dependencies
- Providing clear environment variable documentation
- Setting up development and production configurations

DO NOT generate:
- Source code files (those come from other agents)
- Files that already exist in the workspace
- Excessive boilerplate beyond what's needed
"""


def _format_task_summary(task_dag: list) -> str:
    """Create a brief summary of planned tasks."""
    if not task_dag:
        return "No tasks defined yet"

    lines = []
    for task in task_dag[:10]:  # Limit to first 10 tasks
        skill = task.get("skill_required", "?")
        title = task.get("title", "Untitled")
        lines.append(f"- [{skill}] {title}")

    if len(task_dag) > 10:
        lines.append(f"  ... and {len(task_dag) - 10} more tasks")

    return "\n".join(lines)
