# Testing BRD-to-WBS Pipeline

## Prerequisites

### 1. Environment Setup

Add NVIDIA API key to your `.env` file:

```env
# NVIDIA API for BRD-to-WBS
NVIDIA_API_KEY=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

# Required services
REDIS_URL=redis://localhost:6379
POSTGRES_URL=postgresql+asyncpg://user:password@localhost:5432/aidevplatform
GROQ_API_KEY=gsk-your-groq-key

# Auth
APP_SECRET_KEY=your-secret-key-here
SERVICE_TOKEN_SECRET=your-service-token-here
```

### 2. Start Services

```bash
# Start Redis
docker run -d -p 6379:6379 redis:latest

# Start PostgreSQL
docker run -d -p 5432:5432 \
  -e POSTGRES_USER=user \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=aidevplatform \
  postgres:15

# Or use docker-compose if available
docker-compose up -d
```

### 3. Run Database Migrations

```bash
cd c:/Users/vatsal/Desktop/AAKAR\ prototype/AAKAR
alembic upgrade head
```

### 4. Start the API Server

```bash
# Activate virtual environment
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Start Uvicorn
uvicorn api.main:app --reload --port 8000
```

## Testing Flow

### Step 1: Create Project with BRD

```bash
# Create a test script
cat > test_brd_pipeline.py << 'EOF'
import asyncio
import httpx
import json

API_BASE = "http://localhost:8000"

# Sample BRD
BRD_TEXT = """
# Business Requirements Document

## Project Overview
Project Name: Task Management System
Domain: Productivity Software
Timeline: 12 weeks
Team Size: 5 developers (2 backend, 2 frontend, 1 QA)

## Functional Requirements

### User Management Module
- FR-001: Users must be able to register with email and password
- FR-002: Users must be able to login with email/password
- FR-003: Users must be able to reset forgotten passwords via email
- FR-004: User profiles must support name, avatar, and preferences

### Task Management Module
- FR-005: Users must be able to create tasks with title, description, due date, priority
- FR-006: Users must be able to edit their own tasks
- FR-007: Users must be able to delete their own tasks
- FR-008: Users must be able to mark tasks as complete
- FR-009: Users must be able to filter tasks by status, priority, date
- FR-010: Users must be able to search tasks by title/description

### Collaboration Module
- FR-011: Users must be able to share tasks with other users
- FR-012: Users must be able to comment on shared tasks
- FR-013: Users must receive notifications for task assignments

## Non-Functional Requirements
- NFR-001: System must support 1000 concurrent users
- NFR-002: API response time must be under 200ms for 95% of requests
- NFR-003: System must have 99.9% uptime
- NFR-004: All passwords must be hashed with bcrypt
- NFR-005: All data must be encrypted in transit (HTTPS)

## Technology Constraints
- Backend: Node.js with Express
- Frontend: React with TypeScript
- Database: PostgreSQL
- Hosting: AWS

## Integration Requirements
- INT-001: Integration with SendGrid for email notifications
- INT-002: Integration with AWS S3 for avatar storage
"""

async def test_brd_pipeline():
    async with httpx.AsyncClient(timeout=300.0) as client:
        # 1. Register user (if needed)
        print("\n1. Registering test user...")
        try:
            register_resp = await client.post(
                f"{API_BASE}/auth/register",
                json={
                    "email": "test@example.com",
                    "password": "testpass123",
                    "full_name": "Test User"
                }
            )
            print(f"   Status: {register_resp.status_code}")
        except Exception as e:
            print(f"   User might already exist: {e}")

        # 2. Login
        print("\n2. Logging in...")
        login_resp = await client.post(
            f"{API_BASE}/auth/login",
            data={
                "username": "test@example.com",
                "password": "testpass123"
            }
        )

        if login_resp.status_code != 200:
            print(f"   Login failed: {login_resp.text}")
            return

        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"   Token: {token[:20]}...")

        # 3. Create project with BRD
        print("\n3. Creating project with BRD...")
        create_resp = await client.post(
            f"{API_BASE}/projects",
            json={
                "name": "Task Management System",
                "description": "A comprehensive task management system",
                "prompt": BRD_TEXT
            },
            headers=headers
        )

        if create_resp.status_code != 200:
            print(f"   Failed: {create_resp.text}")
            return

        project_id = create_resp.json()["id"]
        print(f"   Project ID: {project_id}")

        # 4. Wait for BRD-to-WBS processing
        print("\n4. Waiting for BRD-to-WBS pipeline (this may take 2-3 minutes)...")
        max_attempts = 60
        for attempt in range(max_attempts):
            await asyncio.sleep(5)

            status_resp = await client.get(
                f"{API_BASE}/projects/{project_id}/status",
                headers=headers
            )

            status_data = status_resp.json()
            current_status = status_data.get("status")
            print(f"   [{attempt+1}/{max_attempts}] Status: {current_status}")

            if current_status == "AWAITING_APPROVAL":
                print("\n   ✅ WBS generated! Moving to next step...")
                break
            elif current_status == "FAILED":
                print(f"\n   ❌ Pipeline failed: {status_data.get('error')}")
                return

        # 5. Get the generated plan
        print("\n5. Fetching generated WBS plan...")
        plan_resp = await client.get(
            f"{API_BASE}/projects/{project_id}/plan",
            headers=headers
        )

        if plan_resp.status_code != 200:
            print(f"   Failed: {plan_resp.text}")
            return

        plan = plan_resp.json()
        print(f"   Project Summary: {plan['project_summary']}")
        print(f"   Total Tasks: {plan['total_tasks']}")
        print(f"   Skill Breakdown: {json.dumps(plan['skill_breakdown'], indent=2)}")

        # Print first 5 tasks
        print("\n   First 5 tasks:")
        for task in plan['tasks'][:5]:
            print(f"   - {task['id']}: {task['title']}")
            print(f"     Skill: {task['skill_required']}, Depends: {task['depends_on']}")

        # 6. Approve the plan
        print("\n6. Approving plan to start code generation...")
        approve_resp = await client.post(
            f"{API_BASE}/projects/{project_id}/plan/approve",
            json={"approved": True},
            headers=headers
        )

        if approve_resp.status_code != 200:
            print(f"   Failed: {approve_resp.text}")
            return

        print(f"   ✅ Plan approved! Tasks will be enqueued to Redis streams.")
        print(f"   Graph will now: router → task_monitor → agents → code generation")

        # 7. Monitor task execution (optional)
        print("\n7. Monitoring task execution for 30 seconds...")
        for i in range(6):
            await asyncio.sleep(5)
            status_resp = await client.get(
                f"{API_BASE}/projects/{project_id}/status",
                headers=headers
            )
            status_data = status_resp.json()
            print(f"   [{i+1}/6] Status: {status_data.get('status')}, "
                  f"Pending: {status_data.get('pending_tasks', 0)}, "
                  f"Completed: {status_data.get('completed_tasks', 0)}")

if __name__ == "__main__":
    asyncio.run(test_brd_pipeline())
EOF

# Run the test
python test_brd_pipeline.py
```

### Step 2: Manual Testing via cURL

```bash
# 1. Login
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@example.com&password=testpass123" \
  | jq -r '.access_token')

echo "Token: $TOKEN"

# 2. Create project with BRD
PROJECT_ID=$(curl -s -X POST http://localhost:8000/projects \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "E-commerce Platform",
    "description": "Full-featured e-commerce platform",
    "prompt": "# BRD\n\n## Overview\nBuild an e-commerce platform with user authentication, product catalog, shopping cart, and checkout.\n\n## Functional Requirements\n- FR-001: User registration and login\n- FR-002: Product listing with search and filters\n- FR-003: Shopping cart management\n- FR-004: Checkout with payment integration\n\n## Tech Stack\n- Backend: Node.js\n- Frontend: React\n- Database: PostgreSQL"
  }' | jq -r '.id')

echo "Project ID: $PROJECT_ID"

# 3. Check status
curl -s http://localhost:8000/projects/$PROJECT_ID/status \
  -H "Authorization: Bearer $TOKEN" | jq

# 4. Get plan (after AWAITING_APPROVAL)
curl -s http://localhost:8000/projects/$PROJECT_ID/plan \
  -H "Authorization: Bearer $TOKEN" | jq

# 5. Approve plan
curl -s -X POST http://localhost:8000/projects/$PROJECT_ID/plan/approve \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"approved": true}' | jq
```

## Debugging

### Check Logs

```bash
# API server logs (in Uvicorn terminal)
# Look for:
# - brd_to_wbs_start
# - brd_to_wbs_stage
# - nvidia_llm_call
# - brd_to_wbs_complete

# Check Redis streams
redis-cli
> XINFO STREAM tasks:your-project-id
> XINFO STREAM stream:backend_agent
> XINFO STREAM stream:frontend_agent
```

### Common Issues

1. **NVIDIA API Key Not Set**
   ```
   Error: "NVIDIA_API_KEY not configured in environment"
   Solution: Add NVIDIA_API_KEY to .env file
   ```

2. **JSON Parse Failed**
   ```
   Warning: nvidia_json_parse_failed
   Solution: Check logs for raw LLM output, may need prompt tuning
   ```

3. **DAG Validation Failed**
   ```
   Error: "Task DAG invalid: circular dependency detected"
   Solution: Check task dependencies in WBS output
   ```

## Expected Output

### Task DAG Structure
```json
{
  "project_id": "uuid",
  "project_summary": "Task management system with user auth, task CRUD, and collaboration",
  "total_tasks": 18,
  "skill_breakdown": {
    "backend": 10,
    "frontend": 5,
    "database": 2,
    "qa": 1
  },
  "tasks": [
    {
      "id": "wbs_db_001",
      "title": "Design database schema",
      "description": "Create tables for users, tasks, comments with relationships",
      "skill_required": "database",
      "acceptance_criteria": ["Schema diagram created", "Migrations written"],
      "depends_on": [],
      "priority": "Must Have",
      "story_points": 3
    },
    {
      "id": "wbs_auth_001",
      "title": "Implement user registration API",
      "description": "POST /api/auth/register endpoint with validation",
      "skill_required": "backend",
      "acceptance_criteria": ["Endpoint accepts email/password", "Passwords hashed"],
      "depends_on": ["wbs_db_001"],
      "priority": "Must Have",
      "story_points": 5
    }
  ]
}
```

## Next Steps After Approval

1. **Router Node**: Validates DAG and enqueues first wave of tasks
2. **Redis Streams**: Tasks distributed to skill-specific streams
3. **Agent Workers**: Pick up tasks and generate code
4. **Task Monitor**: Tracks completion and enqueues next wave
5. **QA Node**: Runs tests on completed code
6. **Reviewer Node**: Final code review
7. **Delivery Node**: Merges to main branch

Monitor progress at: `GET /projects/{id}/status`
