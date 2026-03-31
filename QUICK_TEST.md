# Quick Test Guide - BRD to WBS Pipeline

## 🚀 Super Quick Start (3 commands)

```bash
# 1. Check services (optional but recommended)
check_services.bat    # Windows
# or
bash check_services.sh  # Linux/Mac

# 2. Start the server (handles checks & migrations)
python start_server.py

# 3. Run test (in new terminal)
python test_brd_pipeline.py
```

---

## 📋 Manual Setup (if automatic fails)

### Step 1: Setup Environment

```bash
# Copy example and add your keys
cp .env.example .env
# Edit .env and set NVIDIA_API_KEY
```

### Step 2: Start Services

```bash
# Redis
docker run -d --name redis -p 6379:6379 redis:latest

# PostgreSQL
docker run -d --name postgres -p 5432:5432 \
  -e POSTGRES_USER=user \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=aidevplatform \
  postgres:15
```

### Step 3: Run Database Migrations

```bash
alembic upgrade head
```

### Step 4: Start API Server

```bash
# Option A: Use startup script (recommended - checks everything)
python start_server.py

# Option B: Direct uvicorn
uvicorn api.main:app --reload --port 8000
```

### Step 5: Test Pipeline

```bash
# In a new terminal
python test_brd_pipeline.py
```

---

## 📋 Expected Flow

1. **User Registration** (2s)
2. **Login** (1s)
3. **Create Project** (2s)
4. **BRD → WBS Processing** (120-180s)
   - Stage 1: Requirements extraction
   - Stage 2: Scope definition
   - Stage 3: Architecture design
   - Stage 4: WBS generation
5. **Fetch Plan** (1s) - should show ~15-20 tasks
6. **Approve Plan** (1s)
7. **Monitor Dispatch** (30s) - tasks enqueue to Redis

## ✓ Success Indicators

- Project status reaches `AWAITING_APPROVAL`
- Plan contains tasks with:
  - Valid IDs (e.g., `wbs_auth_001`)
  - Different skills (backend, frontend, database, qa)
  - Dependencies properly set
- After approval:
  - Status changes to `IN_PROGRESS`
  - Tasks appear in Redis streams
  - Agents start picking up tasks

## 🔍 Debug Commands

```bash
# Check Redis streams
redis-cli
> XINFO GROUPS tasks:your-project-id
> XLEN stream:backend_agent
> XLEN stream:frontend_agent

# Check API logs
# Look for: brd_to_wbs_start, nvidia_llm_call, brd_to_wbs_complete

# Check specific project
curl http://localhost:8000/projects/{project_id}/status \
  -H "Authorization: Bearer YOUR_TOKEN" | jq
```

## ❌ Common Issues

| Issue | Solution |
|-------|----------|
| `All connection attempts failed` | API server not running - run `python start_server.py` |
| `NVIDIA_API_KEY not configured` | Add to `.env` file |
| `Connection refused to PostgreSQL` | Start PostgreSQL: `docker run -d -p 5432:5432 -e POSTGRES_USER=user -e POSTGRES_PASSWORD=password -e POSTGRES_DB=aidevplatform postgres:15` |
| `Connection refused to Redis` | Start Redis: `docker run -d -p 6379:6379 redis:latest` |
| `401 Unauthorized` | Check token, re-login |
| `Timeout after 300s` | Check NVIDIA API quota/rate limits |
| `Migration failed` | Run manually: `alembic upgrade head` |

## 📊 Next Steps

After plan approval, agents will:
1. Generate code for each task
2. Commit to feature branches
3. Run QA tests
4. Perform code review
5. Merge to main

Monitor with: `GET /projects/{id}/status`
