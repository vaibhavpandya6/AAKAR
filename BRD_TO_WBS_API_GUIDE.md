# BRD to WBS - Frontend Integration Guide

## 🎯 Overview

Your API is **already configured** to accept BRD from the frontend and generate WBS tasks through the 9-stage pipeline!

**Frontend URL**: http://localhost:8080/docs#
**API Base**: http://localhost:8080/api/v1

---

## 📋 Complete Flow

```
Frontend → Submit BRD → Backend (9 stages) → Return WBS → User Approves → Code Generation
```

### Timeline
- **WBS Generation**: 10-15 minutes (9 AI agent stages)
- **User Review**: Human-in-the-loop approval
- **Code Generation**: Continues after approval

---

## 🔑 Authentication

All endpoints require JWT authentication:

```bash
# Login first
curl -X POST http://localhost:8080/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=your_email@example.com&password=your_password"

# Response includes access_token
{
  "access_token": "eyJ...",
  "token_type": "bearer"
}
```

Use this token in all subsequent requests:
```bash
Authorization: Bearer eyJ...
```

---

## 🚀 API Endpoints

### 1. Create Project (Submit BRD)

**POST** `/api/v1/projects/create`

**Request**:
```json
{
  "prompt": "# Business Requirements Document\n\n## Project Overview\nProject Name: Task Management System\nTimeline: 12 weeks\n\n## Functional Requirements\n- FR-001: Users must register..."
}
```

**Response**:
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "CREATED",
  "prompt": "Your BRD text...",
  "created_at": "2026-03-31T10:00:00Z",
  "updated_at": "2026-03-31T10:00:00Z"
}
```

**Status Flow**: `CREATED` → `PLANNING` → `AWAITING_APPROVAL`

---

### 2. Check Project Status

**GET** `/api/v1/projects/{project_id}/status`

**Response**:
```json
{
  "project_id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "PLANNING",
  "project_summary": "",
  "pending_tasks": [],
  "error_message": null,
  "updated_at": "2026-03-31T10:05:00Z"
}
```

**Poll this endpoint every 5-10 seconds** until `status == "AWAITING_APPROVAL"`

---

### 3. Get Generated WBS Plan

**GET** `/api/v1/projects/{project_id}/plan`

**Available when**: `status == "AWAITING_APPROVAL"`

**Response**:
```json
{
  "project_id": "123e4567-e89b-12d3-a456-426614174000",
  "project_summary": "This WBS outlines the 12-week development plan for the Task Management System, focusing on User Management, Task Management, and Collaboration modules using a Node.js/React/PostgreSQL stack. The plan covers 45 critical tasks distributed across backend, frontend, database, QA, and DevOps agents...",
  "total_tasks": 45,
  "skill_breakdown": {
    "backend": 20,
    "frontend": 15,
    "database": 5,
    "qa": 5
  },
  "tasks": [
    {
      "id": "wbs_db_001",
      "title": "Design and create Users, Tasks, Shares, and Comments tables",
      "description": "Create normalized PostgreSQL schema with proper indexes, foreign keys, and constraints. Include user roles, task statuses, sharing permissions, and comment threading.",
      "skill_required": "database",
      "acceptance_criteria": [
        "Database schema deployed to dev environment with all tables, indexes, and constraints documented"
      ],
      "depends_on": []
    },
    {
      "id": "wbs_be_001",
      "title": "Implement User Registration API with bcrypt hashing",
      "description": "POST /api/v1/auth/register endpoint that validates email, hashes password with bcrypt, stores user in database, and returns 201 status.",
      "skill_required": "backend",
      "acceptance_criteria": [
        "API accepts valid registration data, stores hashed password, and returns 201 status with user ID"
      ],
      "depends_on": ["wbs_db_001"]
    }
  ],
  "plan_approved": false,
  "status": "AWAITING_APPROVAL"
}
```

---

### 4. Approve WBS Plan (Start Code Generation)

**POST** `/api/v1/projects/{project_id}/plan/approve`

**Approve**:
```json
{
  "approved": true
}
```

**Response**:
```json
{
  "status": "resumed",
  "message": "Plan approved. Task dispatch is starting — poll GET /projects/{id}/status for updates."
}
```

**Next**: Status changes to `IN_PROGRESS` and agents start generating code!

---

### 5. Reject WBS Plan (Regenerate with Feedback)

**POST** `/api/v1/projects/{project_id}/plan/approve`

**Reject**:
```json
{
  "approved": false,
  "feedback": "Please add more QA tasks and focus on security testing for authentication endpoints. Also need load testing tasks."
}
```

**Response**:
```json
{
  "status": "replanning",
  "message": "Plan rejected. Re-running the planner with your feedback. Poll GET /projects/{id}/plan once the new plan is ready."
}
```

**Next**: Status goes back to `PLANNING`, incorporates feedback, and generates new WBS. Poll again!

---

## 🧪 Testing with cURL

### Complete Flow Example

```bash
# Step 1: Login
TOKEN=$(curl -X POST http://localhost:8080/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@example.com&password=testpass" \
  | jq -r '.access_token')

# Step 2: Submit BRD
PROJECT_ID=$(curl -X POST http://localhost:8080/api/v1/projects/create \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "# Business Requirements Document\n\n## Project Overview\nProject Name: E-commerce Platform\nTimeline: 16 weeks\n\n## Functional Requirements\n- FR-001: Users browse products\n- FR-002: Users add to cart\n- FR-003: Users checkout with Stripe\n\n## Non-Functional Requirements\n- NFR-001: Support 10k concurrent users\n- NFR-002: 99.9% uptime"
  }' | jq -r '.id')

echo "Project ID: $PROJECT_ID"

# Step 3: Poll status (repeat every 10 seconds until AWAITING_APPROVAL)
curl -X GET "http://localhost:8080/api/v1/projects/$PROJECT_ID/status" \
  -H "Authorization: Bearer $TOKEN" | jq '.status'

# Step 4: Get WBS plan (when status is AWAITING_APPROVAL)
curl -X GET "http://localhost:8080/api/v1/projects/$PROJECT_ID/plan" \
  -H "Authorization: Bearer $TOKEN" | jq '.'

# Step 5a: Approve plan
curl -X POST "http://localhost:8080/api/v1/projects/$PROJECT_ID/plan/approve" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"approved": true}'

# Step 5b: OR reject with feedback
curl -X POST "http://localhost:8080/api/v1/projects/$PROJECT_ID/plan/approve" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "approved": false,
    "feedback": "Add more security tasks and increase QA coverage"
  }'
```

---

## 📱 Frontend UI Flow

### Recommended User Experience

1. **BRD Input Page**
   - Large textarea for BRD content (markdown format)
   - Character counter (min 10, max 5000)
   - "Generate WBS" button
   - File upload option (.md, .txt, .pdf)

2. **Processing Page** (10-15 min wait)
   - Show progress: "Stage 1/9: Extracting requirements..."
   - Progress bar or spinner
   - Estimated time remaining
   - Allow user to navigate away (save project_id)

3. **WBS Review Page**
   - **Summary Card**: Project overview, timeline, tech stack
   - **Metrics**: Total tasks, story points, effort days
   - **Agent Distribution Chart**: Bar chart of tasks per agent
   - **Task List**: Expandable cards showing:
     - Task ID, title, description
     - Assigned agent
     - Dependencies (linked)
     - Acceptance criteria
     - Test cases (if available)
   - **Action Buttons**:
     - ✅ "Approve & Start Coding" (green, primary)
     - ❌ "Reject & Regenerate" (opens feedback modal)
     - 💾 "Download WBS" (JSON/CSV export)

4. **Feedback Modal** (if rejected)
   - Textarea for feedback
   - "What would you like changed?"
   - "Regenerate Plan" button

5. **Code Generation Page**
   - Real-time task progress
   - Files being created
   - Agent logs
   - Code preview

---

## 🔧 Frontend Implementation Checklist

- [ ] Create BRD input form with validation
- [ ] Implement project creation API call
- [ ] Build status polling mechanism (every 5-10s)
- [ ] Design WBS review UI
- [ ] Add approve/reject buttons with confirmation
- [ ] Implement feedback modal for rejection
- [ ] Show real-time progress during planning
- [ ] Display task dependencies as a graph (optional)
- [ ] Add export WBS to JSON/CSV
- [ ] Handle error states (API failures, timeouts)
- [ ] Add loading states and spinners
- [ ] Implement auto-refresh on status changes

---

## 🎨 Example UI Components

### BRD Input Form
```jsx
<form onSubmit={handleSubmit}>
  <h2>Submit Business Requirements Document</h2>
  <textarea
    value={brdText}
    onChange={(e) => setBrdText(e.target.value)}
    placeholder="Paste your BRD here (markdown format)..."
    minLength={10}
    maxLength={5000}
    rows={20}
  />
  <p>{brdText.length} / 5000 characters</p>
  <button type="submit">Generate WBS</button>
</form>
```

### WBS Review
```jsx
<div className="wbs-review">
  <div className="summary-card">
    <h3>Project Summary</h3>
    <p>{wbs.project_summary}</p>
  </div>

  <div className="metrics">
    <div className="metric">
      <span className="value">{wbs.total_tasks}</span>
      <span className="label">Total Tasks</span>
    </div>
    {/* More metrics... */}
  </div>

  <div className="task-list">
    {wbs.tasks.map(task => (
      <TaskCard key={task.id} task={task} />
    ))}
  </div>

  <div className="actions">
    <button onClick={handleApprove} className="btn-approve">
      Approve & Start Coding
    </button>
    <button onClick={handleReject} className="btn-reject">
      Reject & Regenerate
    </button>
  </div>
</div>
```

---

## 📊 What the Backend Does (9 Stages)

When you submit a BRD, the backend runs these stages:

1. **Requirements Extraction** (Agent 2)
   - Extracts FR-xxx, NFR-xxx, INT-xxx IDs
   - Identifies modules
   - Flags gaps and ambiguities

2. **Scope Definition** (Agent 3)
   - Defines in-scope vs out-of-scope
   - Lists assumptions and constraints
   - Identifies dependencies

3. **Proposal Generation** (Agent 3.5)
   - Creates executive summary
   - Defines deliverables and timeline
   - Identifies risks

4. **Architecture Design** (Agent 4)
   - Chooses architecture pattern
   - Defines tech stack
   - Maps API endpoints

5. **SOW Generation** (Agent 5)
   - Creates milestones
   - Defines payment terms
   - Legal terms and acceptance criteria

6. **WBS per Module** (Agent 6)
   - Generates tasks for each module
   - Assigns task IDs, priorities, story points
   - Maps requirements to tasks

7. **Test Case Generation** (Agent 7)
   - Creates 3+ tests per task
   - Happy path, edge case, failure scenarios
   - Maps to specific assertions

8. **Project Analysis**
   - Calculates critical path
   - Identifies top risks
   - Team allocation planning

9. **Task Transformation**
   - Converts to internal format
   - Validates DAG (no cycles)
   - Ready for router/queue

---

## ⚙️ Configuration

The pipeline uses these models (configured in your `.env`):

```bash
# Primary model (Agents 2, 3, 3.5, 4, 6)
NVIDIA_MODEL=nvidia/nemotron-3-super-120b-a12b

# SOW generation (Agent 5)
NVIDIA_SOW_MODEL=minimaxai/minimax-m2.1

# Test generation (Agent 7)
NVIDIA_TEST_MODEL=deepseek-ai/deepseek-v3.2
```

All use the same API key: `NVIDIA_API_KEY`

---

## 🐛 Error Handling

### Common Errors

**400 Bad Request**
- BRD too short (< 10 chars)
- BRD too long (> 5000 chars)
- Invalid project UUID

**401 Unauthorized**
- Missing or invalid JWT token
- Token expired

**404 Not Found**
- Project doesn't exist
- Plan not generated yet

**409 Conflict**
- No plan available yet (still planning)
- No checkpoint found

**503 Service Unavailable**
- Graph/checkpointer not initialized
- Backend services not running

### Handling in Frontend

```typescript
try {
  const wbs = await getWBSPlan(projectId, token);
  setWbs(wbs);
} catch (error) {
  if (error.status === 409) {
    // Still planning, poll again later
    setTimeout(() => pollStatus(), 5000);
  } else if (error.status === 401) {
    // Token expired, redirect to login
    navigateToLogin();
  } else {
    // Show error to user
    showError(error.message);
  }
}
```

---

## 📚 Additional Resources

- **Full code example**: See `FRONTEND_INTEGRATION.tsx` for complete React implementation
- **API docs**: http://localhost:8080/docs (Swagger UI)
- **Test CLI**: See `test_custom_brd.py` for backend testing

---

## 🎉 Quick Start

1. **Start your backend**:
   ```bash
   python start_server.py
   ```

2. **Open API docs**:
   http://localhost:8080/docs#

3. **Try the flow**:
   - Login via `/auth/login`
   - Create project via `/projects/create` (paste BRD in `prompt`)
   - Poll `/projects/{id}/status` until `AWAITING_APPROVAL`
   - Get plan via `/projects/{id}/plan`
   - Approve via `/projects/{id}/plan/approve`

Your API is ready! Just build the frontend UI around these endpoints. 🚀
