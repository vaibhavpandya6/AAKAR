# ai-dev-platform

A production-ready **multi-agent AI development platform** that orchestrates large language models to automatically plan, implement, test, review, and deliver full-stack software projects.

**ai-dev-platform** takes a natural-language product spec and builds complete working code end-to-end: a backend agent writes the service layer; a frontend agent builds the UI; a database agent schemas tables; a QA agent tests for bugs; and a dedicated reviewer agent provides one final sign-off before code ships to production. The entire workflow is interrupted at critical checkpoints to incorporate human feedback via email or web UI before proceeding.

## Quick Start (Windows)

```powershell
# 1. Clone and navigate to the repo
git clone https://github.com/your-org/ai-dev-platform.git
cd ai-dev-platform

# 2. Set up .env (edit with Notepad and fill in OPENAI_API_KEY, APP_SECRET_KEY)
copy .env.example .env

# 3. Start everything (Redis, PostgreSQL, and all services)
.\start.bat
# or: .\start.ps1

# 4. After "✅ All services started", the API is at http://localhost:8080
# See "How to use" section below for next steps.
```

For **detailed Windows setup**, see the [Windows section](#windows) below.

For **macOS/Linux**, see [Prerequisites](#prerequisites) and [Setup](#setup) sections.

## Architecture Overview

```
                              ┌─────────────────────────────────────────┐
                              │         FastAPI HTTP Server             │
                              │  POST /auth/register, /projects/create  │
                              │  GET  /projects/{id}/status             │
                              │  POST /projects/{id}/plan/approve       │
                              └────────────┬────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────┐
│                          LangGraph Orchestrator                         │
│                                                                         │
│  planner_node → router_node → task_monitor_node ↔ qa_node → reviewer  │
│              (JSON-mode LLM)  (task_graph DAG)   (test loop) (decision)│
│                                                                         │
│  Coordinates multi-agent system via Redis Streams message bus          │
└────────┬──────────────────────────┬──────────────────────┬─────────────┘
         │                          │                      │
         ▼                          ▼                      ▼
    ┌────────────┐            ┌─────────────┐       ┌──────────────┐
    │   Redis    │            │ PostgreSQL  │       │  ChromaDB    │
    │  Streams   │            │  Database   │       │ Vector Store │
    │ (messages) │            │  (state)    │       │ (memories)   │
    └────────────┘            └─────────────┘       └──────────────┘
         │
    ┌────┴────────────────────────────────────────────────────┐
    │                    Agent Workers                         │
    │                                                           │
    ├─────────────────┬──────────────────┬────────────────────┤
    │   BackendAgent  │  FrontendAgent   │  DatabaseAgent     │
    │  (Python/Node)  │   (React/Vue)    │  (Migrations)      │
    │                 │                  │                    │
    │ ↓ write_file    │  ↓ write_file    │  ↓ write_file      │
    │   run_command   │    compile_scss  │    run_psql        │
    │   run_tests     │    audit_perf    │    validate_sql    │
    └─────────────────┴──────────────────┴────────────────────┘
         │                                     │
         └──────────────┬──────────────────────┘
                        ▼
                ┌───────────────────┐
                │  Docker Sandbox   │
                │  (code execution) │
                │  - Copy project   │
                │  - Run tests      │
                │  - Capture logs   │
                │  - Return results │
                └───────────────────┘
```

## Prerequisites

**👉 Windows users: Jump to the [Windows section](#windows) below for native Windows setup with Docker Compose support.**

### macOS

```bash
# Homebrew (https://brew.sh if not installed)
brew install python@3.11 postgresql@15 redis git

# Start PostgreSQL and Redis as background services (persistent across reboots)
brew services start postgresql@15
brew services start redis

# Verify they're running
psql --version          # should be 15.x
redis-cli ping          # should respond with PONG

# Install Docker Desktop from https://www.docker.com/products/docker-desktop
```

### Ubuntu 22.04+ / Debian

```bash
# Update package list
sudo apt-get update

# Install dependencies
sudo apt-get install -y python3.11 python3.11-venv postgresql-15 redis-server git

# Start PostgreSQL and Redis
sudo systemctl start postgresql
sudo systemctl start redis-server

# Verify they're running
psql --version              # should be 15.x
redis-cli ping              # should respond with PONG

# Install Docker (https://docs.docker.com/engine/install/ubuntu/)
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER   # Add your user to docker group (requires logout/login)
```

### Windows

**Full Windows support** is now available! Redis and PostgreSQL run in Docker containers (via `docker-compose.dev.yml`), and the platform services run natively on Windows as background processes.

#### Prerequisites

1. **Python 3.11+** — Download from https://www.python.org/downloads/
   - ✅ Check "Add Python to PATH" during installation
   - Verify: `python --version` in Command Prompt

2. **Docker Desktop** — Download from https://www.docker.com/products/docker-desktop
   - Install and leave it running (optional: enable "Start Docker Desktop when you log in")
   - Verify: `docker info` in PowerShell or Command Prompt

3. **Git** — Download from https://git-scm.com/download/win
   - Verify: `git --version` in Command Prompt

#### Setup (Windows-Specific)

1. **Clone the repository**
   ```cmd
   git clone https://github.com/your-org/ai-dev-platform.git
   cd ai-dev-platform
   ```

2. **Configure .env for Windows**
   ```cmd
   copy .env.example .env
   ```

   Edit `.env` with Notepad and update:
   - `APP_SECRET_KEY` — generate a random string, e.g., `python -c "import secrets; print(secrets.token_hex(16))"`
   - `OPENAI_API_KEY` — your OpenAI API key (required)
   - `SERVICE_TOKEN_SECRET` — can be the same as `APP_SECRET_KEY`

   **For Windows, keep these as-is** (Docker Compose uses these defaults):
   ```ini
   POSTGRES_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/aidevplatform
   REDIS_URL=redis://localhost:6379
   ```

3. **Start the platform**

   **Option A: Double-click `start.bat`** (simplest, opens in Command Prompt)
   ```cmd
   start.bat
   ```

   **Option B: PowerShell (recommended for better logging)**
   ```powershell
   .\start.ps1
   ```

   **Option C: PowerShell with skip-infrastructure flag** (if Redis/PostgreSQL already running)
   ```powershell
   .\start.ps1 -SkipInfra
   ```

4. **Wait for startup**
   - The script will:
     - Start Redis + PostgreSQL in Docker
     - Create a Python virtual environment (`.venv`)
     - Install dependencies from `requirements.txt`
     - Run database migrations
     - Build the sandbox Docker image
     - Start 6 background services (API, orchestrator, 4 agents)

   - When you see **"✅ All services started. API at http://localhost:8080"**, you're ready!
   - Service logs are written to the `logs/` directory

5. **Verify the setup**
   ```powershell
   curl http://localhost:8080/health
   ```

#### Stopping (Windows)

**Option A: Double-click `stop.bat`**
```cmd
stop.bat
```

**Option B: PowerShell**
```powershell
.\stop.ps1          # Stop services, keep Redis/PostgreSQL running
.\stop.ps1 -StopInfra   # Also stop Redis/PostgreSQL
```

### WSL2 (Alternative for Windows)

If you prefer Linux inside Windows, use **Windows Subsystem for Linux 2**:

```bash
# Inside WSL2 Ubuntu terminal, follow the Ubuntu instructions above
./start.sh
```

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/your-org/ai-dev-platform.git
cd ai-dev-platform
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:
- `APP_SECRET_KEY` — a random 32-character secret (for session signing)
- `OPENAI_API_KEY` — your OpenAI API key (required for GPT-4 calls)
- `SERVICE_TOKEN_SECRET` — can be the same as `APP_SECRET_KEY`

**Operating system-specific:**

**macOS/Linux**: Keep default POSTGRES_URL and REDIS_URL
```bash
POSTGRES_URL=postgresql+asyncpg://user:password@localhost:5432/aidevplatform
REDIS_URL=redis://localhost:6379
```

**Windows**: See [Windows Setup section](#windows) below — use Docker Compose and adjust `.env` accordingly.

### 3. Create the PostgreSQL database

```bash
createdb aidevplatform
```

*(Windows: Skip this — Docker Compose creates it automatically)*

### 4. Start the platform

**macOS/Linux:**
```bash
chmod +x start.sh
./start.sh
```

**Windows:** See [Windows Setup section](#windows) and use `start.bat` or `start.ps1`

This will:
- Check all prerequisites (Python, PostgreSQL, Redis, Docker)
- Create and activate a Python virtual environment (`.venv`)
- Install Python dependencies
- Run database migrations (Alembic)
- Build the sandbox Docker image
- Start 6 background services (API, orchestrator, 4 agent workers)

When you see the message **"✅ All services started. API at http://localhost:8080"**, the platform is ready.

### 5. Verify everything is working

```bash
curl http://localhost:8080/health
```

Should return:

```json
{
  "status": "ok",
  "timestamp": "2026-03-17T...",
  "version": "1.0.0",
  "environment": "development",
  "metrics": {
    "tasks_completed_total": 0,
    "tasks_failed_total": 0,
    "llm_calls_total": 0,
    "llm_tokens_used_total": 0,
    "sandbox_executions_total": 0,
    "sandbox_timeouts_total": 0,
    "per_agent_task_counts": {}
  }
}
```

## How to Use

### Quick Start with curl

#### 1. Register a user

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "dev@example.com",
    "password": "secure_password_123",
    "role": "developer"
  }' | jq -r '.access_token')

echo "Token: $TOKEN"
```

#### 2. Create a project

```bash
PROJECT=$(curl -s -X POST http://localhost:8080/projects/create \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Build a todo app: Node.js Express backend with PostgreSQL, React frontend. Users can create, list, and delete todos. Include unit tests for the backend."
  }' | jq -r '.id')

echo "Project ID: $PROJECT"
```

The orchestrator immediately starts planning. This takes ~30–60 seconds.

#### 3. View the plan

```bash
curl -s -X GET "http://localhost:8080/projects/$PROJECT/plan" \
  -H "Authorization: Bearer $TOKEN" | jq '.'
```

Returns a `TaskDAGResponse` with all planned tasks:

```json
{
  "project_id": "...",
  "project_summary": "Build a todo app: Node.js Express backend...",
  "total_tasks": 8,
  "skill_breakdown": {
    "backend": 3,
    "frontend": 2,
    "database": 2,
    "qa": 1
  },
  "tasks": [
    {
      "id": "task-1",
      "title": "Set up Express server",
      "skill_required": "backend",
      "depends_on": [],
      "acceptance_criteria": [...]
    },
    ...
  ],
  "plan_approved": false,
  "status": "AWAITING_APPROVAL"
}
```

#### 4. Approve the plan (Human-in-the-Loop)

```bash
curl -s -X POST "http://localhost:8080/projects/$PROJECT/plan/approve" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "approved": true,
    "feedback": "Looks good!"
  }' | jq '.'
```

Response:

```json
{
  "status": "resumed",
  "message": "Plan approved. Execution resumed."
}
```

The graph continues: tasks are routed to agents based on skill, agents execute them in the Docker sandbox, and results stream back to the orchestrator.

#### 5. Request a replan (if desired)

If you reject the plan instead:

```bash
curl -s -X POST "http://localhost:8080/projects/$PROJECT/plan/approve" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "approved": false,
    "feedback": "Please include API authentication via JWT. Also use PostgreSQL for all persistence."
  }' | jq '.'
```

Response:

```json
{
  "status": "replanning",
  "message": "Feedback recorded. Re-planner will iterate on the plan."
}
```

The planner node re-runs with your feedback context, generating a revised plan. The graph rewinds to `AWAITING_APPROVAL` again.

#### 6. Monitor project status

Poll this endpoint to watch progress:

```bash
curl -s -X GET "http://localhost:8080/projects/$PROJECT/status" \
  -H "Authorization: Bearer $TOKEN" | jq '.'
```

Returns:

```json
{
  "project_id": "...",
  "status": "IN_PROGRESS",
  "project_summary": "...",
  "pending_tasks": 5,
  "in_progress_tasks": 2,
  "completed_tasks": [
    {
      "id": "task-1",
      "title": "Set up Express server",
      "skill_required": "backend",
      "status": "completed"
    }
  ],
  "failed_tasks": [],
  "files_written": [
    "backend/server.js",
    "backend/models/Todo.js",
    ...
  ],
  "bug_reports": [],
  "updated_at": "2026-03-17T12:34:56Z"
}
```

#### 7. Test retrieval and logs

```bash
# List all files in the project workspace
curl -s -X GET "http://localhost:8080/projects/$PROJECT/files" \
  -H "Authorization: Bearer $TOKEN" | jq '.'

# Read a specific file
curl -s -X GET "http://localhost:8080/projects/$PROJECT/files/backend/server.js" \
  -H "Authorization: Bearer $TOKEN" | jq '.'

# View execution logs (filtered by agent, action, status, time range)
curl -s -X GET "http://localhost:8080/projects/$PROJECT/logs?agent=backend_agent&status=success&limit=10" \
  -H "Authorization: Bearer $TOKEN" | jq '.'
```

## Human-in-the-Loop (HITL) Flow

The platform pauses execution at exactly one point: **after the plan is generated** and before implementation begins.

### Workflow

```
1. User creates project
   ↓
2. Planner generates task DAG
   ↓
3. Graph pauses (interrupt_before=["router"])
   ↓
4. User reviews plan via GET /projects/{id}/plan
   ↓
5. User approves (POST /projects/{id}/plan/approve with approved=true)
   OR requests replan (approved=false with feedback)
   ↓
   IF approved:
     → Router dispatches ready tasks to agents
     → Agents execute in Docker sandbox
     → QA tests each completed task
     → Reviewer approves or escalates bugs
     → All code merged and tagged

   IF replanning:
     → Graph rewinds to START (same thread_id keeps checkpoint state)
     → Planner re-runs with feedback context
     → Back to step 2
```

### Design Rationale

- **Single interruption point** keeps HITL overhead minimal (only ~30 sec human time).
- **Pause is checkpoint-backed** — graph state is fully persisted to SQLite, so restarts survive crashes.
- **Async graph execution** — `graph.ainvoke()` runs as a background task; user polls for status.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `development` | `production`, `development`, or `testing`. Controls log formatting, error verbosity. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `APP_SECRET_KEY` | *(placeholder)* | **REQUIRED.** 32+ character random secret for signing session tokens. Generate: `openssl rand -hex 16`. |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm. Normally leave as-is. |
| `JWT_EXPIRE_MINUTES` | `60` | Access token expiry in minutes. |
| `OPENAI_API_KEY` | *(placeholder)* | **REQUIRED.** Your OpenAI API key for GPT-4 or GPT-4 Turbo. |
| `POSTGRES_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/aidevplatform` | PostgreSQL async connection URL (asyncpg dialect). |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL for message bus. |
| `CHECKPOINTER` | `sqlite` | Checkpoint backend: `sqlite` (local file) or `postgres` (in-database). |
| `WORKSPACE_BASE_PATH` | `./workspaces` | Root directory for project workspaces (will be created if missing). |
| `SANDBOX_IMAGE` | `node:18-alpine` | Docker image for code sandbox. Can override to `python:3.11-alpine`, etc. |
| `SANDBOX_CPU_LIMIT` | `1000` | CPU limit in CPU shares (1024 = 1 full CPU). |
| `SANDBOX_MEMORY_LIMIT` | `512MB` | Memory limit for sandbox container. |
| `SANDBOX_TIMEOUT_SECONDS` | `30` | Max runtime per sandbox invocation. |
| `SERVICE_TOKEN_SECRET` | *(placeholder)* | Service-to-service authentication secret. Can be same as `APP_SECRET_KEY`. |
| `CORS_ORIGINS` | `*` (dev) or `http://localhost:3000` (prod) | Comma-separated list of allowed CORS origins. |
| `OTLP_ENDPOINT` | *(unset)* | OpenTelemetry gRPC OTLP exporter endpoint (e.g., `http://localhost:4317`). Tracing disabled if unset. |
| `OTLP_HEADERS` | *(unset)* | OTLP auth headers as `key1=val1,key2=val2`. |
| `OTEL_SERVICE_NAME` | `ai-dev-platform` | Service name for OTel resource. |

## Docker Usage

**Important:** Docker is **only** used for the **code execution sandbox**, not for running the platform services themselves.

### Why a Sandbox?

When agents write and execute untrusted user code:

- **Isolation** — malicious code or runaway processes cannot affect the host.
- **Cleanup** — each sandbox invocation is ephemeral; leftover files are discarded.
- **Resource limits** — CPU, memory, and timeouts prevent denial-of-service.
- **Reproducibility** — pinned environment (`node:18-alpine` or similar) ensures consistent test results.

### Building a Custom Sandbox Image

Agents run arbitrary user code in the Docker sandbox, so preload common tools:

```bash
# Dockerfile.sandbox is in ./sandbox/
# It installs npm, postgres client, git, etc.

docker build -t ai-sandbox:latest ./sandbox/
```

Or choose a different base:

```bash
# Minimal Python sandbox
docker build . -f Dockerfile.sandbox -t ai-sandbox:py \
  --build-arg BASE=python:3.11-alpine
```

### Service Architecture (No Docker Compose)

The platform runs **5–6 long-lived processes** on your machine:

1. **API** (uvicorn, port 8080)
2. **Orchestrator** (single-threaded async worker)
3. **BackendAgent** (worker listening to Redis Streams)
4. **FrontendAgent** (worker)
5. **DatabaseAgent** (worker)
6. **QAAgent** (worker)

All communicate via:
- **Redis Streams** for task distribution (one stream per agent + one for orchestrator)
- **PostgreSQL** for durable state (projects, tasks, logs)
- **ChromaDB** (embedded in-process) for semantic memory

**No Docker Compose file exists** because the platform is designed to run on a developer's laptop with existing PostgreSQL + Redis services (installed via Homebrew or apt).

For **production deployment**, create a Docker Compose or Kubernetes manifests separately (not in scope here).

## Logs

### Structured Logging

All log output is **structured JSON** (in production) or **pretty-printed** (in development):

```json
{"log_level": "info", "timestamp": "2026-03-17T12:34:56.789Z", "logger_name": "api.main", "event": "startup_complete", "cors_origins": ["*"], "request_id": "abc123"}
```

### Log Fields

Every structured log record contains:

| Field | Example | Meaning |
|-------|---------|---------|
| `timestamp` | `2026-03-17T...Z` | ISO-8601 UTC timestamp |
| `log_level` | `info`, `error`, `warning` | Severity |
| `logger_name` | `api.main`, `orchestrator.nodes` | Source module |
| `event` | `agent_action`, `task_complete` | Event type |
| `request_id` | `proj-123:task-456` | Correlation ID (if set) |
| `project_id` | `proj-123` | Owning project (if applicable) |
| `task_id` | `task-456` | Affected task (if applicable) |
| `agent` | `backend_agent` | Agent name (for agent events) |
| `duration_ms` | `1234` | Wall-clock duration in milliseconds |
| `error` | `"KeyError: 'password'"` | Error details (on failures) |

### Reading Logs

In **development**, tail the service logs:

```bash
tail -f /tmp/api.log              # API server logs
tail -f /tmp/orchestrator.log     # Orchestrator worker logs
tail -f /tmp/backend_agent.log    # Backend agent logs
```

In **production**, redirect stdout to a syslog aggregator or ELK stack:

```bash
# Example: send logs to Papertrail via systemd
journalctl -u ai-dev-platform -f
```

### Agent Action Logs

When an agent performs an action (e.g., write a file, run a command), the `log_agent_action()` function:

1. Emits a structured log record
2. **Inserts a row into the `agent_logs` database table**

This allows querying historical actions:

```bash
curl -s http://localhost:8080/projects/$PROJECT/logs?agent=backend_agent&status=success | jq '.entries[] | {action, duration_ms, file_path}'
```

## Rollback

After a project completes (status = `DELIVERED`), you can roll back to any prior tagged state:

### List available tags

```bash
cd workspaces/$PROJECT_ID/repo
git tag -l
```

### Rollback to a specific tag

```bash
curl -s -X POST http://localhost:8080/projects/$PROJECT/rollback \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tag": "delivered-2026-03-17T120000Z"
  }' | jq '.'
```

**Response:**

```json
{
  "project_id": "...",
  "tag": "delivered-2026-03-17T120000Z",
  "status": "rolled_back",
  "message": "Successfully rolled back to tag delivered-2026-03-17T120000Z"
}
```

**Under the hood**, the rollback endpoint:

1. Calls `git checkout <tag>` inside the project workspace
2. **Does NOT re-run tests** or re-validate the codebase
3. Returns the workspace to that commit's exact state

Use carefully — rollback is **destructive** to the current working tree.

## Troubleshooting

### API not responding

```bash
# Check if the API process is running
pgrep -f "uvicorn api.main:app"

# Check logs for errors
tail -50 /tmp/api.log

# Manually test importing the app
python3 -c "from api.main import app; print(app)"
```

### Database migration fails

```bash
# Check PostgreSQL is running
psql -c "SELECT version();"

# Check the database exists
psql -l | grep aidevplatform

# Re-run migrations with verbose output
python -m alembic upgrade head -v
```

### Redis is not responding

```bash
# Check if Redis is running
redis-cli ping

# Start Redis
redis-server

# Or via Homebrew
brew services start redis
```

### Agent workers hanging

```bash
# Check for stuck Python processes
ps aux | grep "python -m agents"

# Kill them and restart
./stop.sh
./start.sh
```

### Docker sandbox errors during execution

```bash
# Check if the sandbox image exists
docker images | grep ai-sandbox

# Rebuild the image
docker build -t ai-sandbox ./sandbox/

# Check Docker daemon is running
docker info
```

## Support

For issues, feature requests, or documentation improvements, please open an issue on GitHub or consult the inline code comments throughout the repository.
