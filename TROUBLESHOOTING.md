# 🔧 Troubleshooting Guide - LLM Plan Generation & Log Access Issues

## Problem 1: LLM Not Generating a Plan

### Diagnostic Steps

1. **Test LLM Connectivity**
```bash
# Start your API server
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# In another terminal, test the LLM endpoint
curl -X GET http://localhost:8000/diagnostics/llm \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Expected response if working:
```json
{
  "status": "ok",
  "llm_model": "llama-3.3-70b-versatile",
  "duration_ms": 1200,
  "json_valid": true,
  "parsed_content": {"test": "success", "timestamp": "..."}
}
```

2. **Check Graph State**
```bash
# After creating a project, check its graph state
curl -X GET http://localhost:8000/diagnostics/graph/{PROJECT_ID} \
  -H "Authorization: Bearer YOUR_TOKEN"
```

This will show you the exact state the orchestrator is in.

3. **Common Causes:**

   **a) Groq API Key Missing or Invalid**
   ```bash
   # Check your .env file
   cat .env | grep GROQ_API_KEY

   # Test directly with Groq
   curl https://api.groq.com/openai/v1/models \
     -H "Authorization: Bearer YOUR_GROQ_API_KEY"
   ```

   **b) LLM Returning Invalid JSON**
   - The planner node expects strict JSON format
   - Check API logs: `tail -f /tmp/api.log | grep planner`
   - Look for `planner_json_parse_failed` or `planner_llm_failed`

   **c) Graph Not Starting**
   - Check if graph is attached to app.state
   - Look for `startup_graph_ready` in API startup logs
   - Verify checkpointer is initialized

4. **Manual Test of Planner**
```python
# Create test_planner.py
import asyncio
from config import create_json_mode_llm
from langchain_core.messages import SystemMessage, HumanMessage

async def test():
    llm = create_json_mode_llm()

    system = """You are a planner. Respond with valid JSON only.
    Format: {"project_summary": "...", "tasks": [...]}
    """

    user = "Create a simple REST API with user authentication"

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=user),
    ])

    print("Raw response:")
    print(response.content)

    import json
    parsed = json.loads(response.content)
    print("\nParsed successfully!")
    print(json.dumps(parsed, indent=2))

asyncio.run(test())
```

Run it:
```bash
python test_planner.py
```

---

## Problem 2: Cannot Access Agent Logs

### Diagnostic Steps

1. **Test Log Query**
```bash
# Test the diagnostic log endpoint
curl -X GET http://localhost:8000/diagnostics/logs/test \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Expected response:
```json
{
  "status": "ok",
  "log_count": 10,
  "sample_logs": [...]
}
```

2. **Test Database Connection**
```bash
curl -X GET http://localhost:8000/diagnostics/database \
  -H "Authorization: Bearer YOUR_TOKEN"
```

3. **Common Causes:**

   **a) UUID Conversion Error**
   The logs endpoint might be receiving an invalid project_id format.

   **Fix:** Ensure you're passing a valid UUID:
   ```bash
   # Bad (wrong format)
   curl http://localhost:8000/projects/abc123/logs

   # Good (valid UUID v4)
   curl http://localhost:8000/projects/550e8400-e29b-41d4-a716-446655440000/logs
   ```

   **b) Database Migration Issues**
   ```bash
   # Check if agent_logs table exists
   psql -U postgres -d ai_dev_platform -c "\d agent_logs"

   # Run migrations if needed
   alembic upgrade head
   ```

   **c) No Logs Written Yet**
   Agents haven't written any logs yet. Wait for workers to process tasks.

4. **Check Postgres Directly**
```bash
psql -U postgres -d ai_dev_platform

# Count logs
SELECT COUNT(*) FROM agent_logs;

# See recent logs
SELECT agent, action, status, timestamp
FROM agent_logs
ORDER BY timestamp DESC
LIMIT 10;

# Check if project exists
SELECT id, status FROM projects LIMIT 5;
```

---

## Complete Debugging Session

### Step 1: Check All Services Are Running

```bash
# Check API
curl http://localhost:8000/health

# Check Redis
redis-cli ping

# Check PostgreSQL
psql -U postgres -c "SELECT 1"

# Check if workers are running
ps aux | grep "agents.*worker"
```

### Step 2: Run Full Diagnostics

```bash
# Get auth token first
TOKEN=$(curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"admin123"}' \
  | jq -r '.access_token')

# Test LLM
curl -X GET http://localhost:8000/diagnostics/llm \
  -H "Authorization: Bearer $TOKEN" | jq

# Test Redis
curl -X GET http://localhost:8000/diagnostics/redis \
  -H "Authorization: Bearer $TOKEN" | jq

# Test Database
curl -X GET http://localhost:8000/diagnostics/database \
  -H "Authorization: Bearer $TOKEN" | jq

# Test Logs
curl -X GET http://localhost:8000/diagnostics/logs/test \
  -H "Authorization: Bearer $TOKEN" | jq
```

### Step 3: Create a Test Project

```bash
# Create a project
PROJECT_ID=$(curl -X POST http://localhost:8000/projects/create \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Create a simple hello world API endpoint"}' \
  | jq -r '.id')

echo "Project ID: $PROJECT_ID"

# Wait a few seconds for planner to run
sleep 5

# Check project status
curl -X GET "http://localhost:8000/projects/$PROJECT_ID/status" \
  -H "Authorization: Bearer $TOKEN" | jq

# Check graph state
curl -X GET "http://localhost:8000/diagnostics/graph/$PROJECT_ID" \
  -H "Authorization: Bearer $TOKEN" | jq

# Check logs
curl -X GET "http://localhost:8000/projects/$PROJECT_ID/logs" \
  -H "Authorization: Bearer $TOKEN" | jq
```

### Step 4: Check API Server Logs

```bash
# If using uvicorn directly
tail -f uvicorn.log

# If using start.sh
tail -f /tmp/api.log

# Look for these patterns:
# - "planner_node_start" - planner started
# - "planner_llm_failed" - LLM call failed
# - "planner_json_parse_failed" - JSON parsing failed
# - "planner_node_complete" - plan generated successfully
```

---

## Quick Fixes

### Fix 1: Reset Everything and Start Fresh

```bash
# Stop all services
pkill -f "uvicorn"
pkill -f "agents.*worker"

# Clear Redis
redis-cli FLUSHALL

# Reset database
dropdb ai_dev_platform
createdb ai_dev_platform
alembic upgrade head

# Restart
python start_dev.py
```

### Fix 2: Enable Verbose Logging

Edit `config/settings.py` and ensure:
```python
log_level = "DEBUG"  # or set environment variable LOG_LEVEL=DEBUG
```

Then restart the API server.

### Fix 3: Test Planner in Isolation

Create `test_planner_isolated.py`:
```python
import asyncio
from orchestrator.nodes.planner_node import planner_node
from orchestrator.state import initial_state

async def test():
    state = initial_state(
        project_id="test-123",
        user_id="test-user",
        original_prompt="Create a simple REST API with user authentication",
    )

    result = await planner_node(state)

    print("Result:", result)

    if "error_message" in result and result["error_message"]:
        print("ERROR:", result["error_message"])
    else:
        print("SUCCESS!")
        print("Tasks:", len(result.get("task_dag", [])))

asyncio.run(test())
```

Run it:
```bash
python test_planner_isolated.py
```

---

## Expected Behaviors

### Successful Plan Generation

API logs should show:
```
{"event": "planner_node_start", "project_id": "...", "is_replan": false}
{"event": "llm_call_success", "model": "llama-3.3-70b-versatile", "duration_ms": 1500}
{"event": "planner_node_complete", "project_id": "...", "task_count": 5}
```

Project status endpoint should return:
```json
{
  "project_id": "...",
  "status": "AWAITING_APPROVAL",
  "project_summary": "...",
  "pending_tasks": [],
  "completed_tasks": [],
  "failed_tasks": []
}
```

### Successful Log Access

Logs endpoint should return:
```json
{
  "project_id": "...",
  "total": 15,
  "entries": [
    {
      "id": "...",
      "agent": "backend-agent-1",
      "action": "file_write",
      "status": "success",
      "timestamp": "2026-03-21T10:30:00Z"
    }
  ]
}
```

---

## Get Help

If issues persist after trying the above:

1. **Capture full diagnostic output:**
```bash
bash debug_capture.sh  # Creates debug_report.txt
```

2. **Check specific error logs:**
```bash
# API errors
grep -i error /tmp/api.log | tail -20

# Worker errors
grep -i error /tmp/backend_agent.log | tail -20

# Redis streams
redis-cli XINFO STREAM stream:orchestrator
```

3. **Enable trace-level debugging:**
```python
# In config/settings.py
log_level = "TRACE"
```
