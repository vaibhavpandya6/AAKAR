"""Test script for BRD-to-WBS pipeline.

Run this to test the complete flow from BRD input to task generation.
"""

import asyncio
import httpx
import json
from datetime import datetime

API_BASE = "http://localhost:8080"

# Sample BRD for a task management system
BRD_TEXT = """
# Business Requirements Document

## Project Overview
**Project Name:** Task Management System
**Domain:** Productivity Software
**Timeline:** 12 weeks
**Team Size:** 5 developers (2 backend, 2 frontend, 1 QA)
**Budget:** $150,000

## Executive Summary
Build a modern task management web application that allows users to create, organize,
and collaborate on tasks. The system should support individual task management and
team collaboration features.

## Functional Requirements

### User Management Module
- **FR-001:** Users must be able to register with email and password
- **FR-002:** Users must be able to login with email/password authentication
- **FR-003:** Users must be able to reset forgotten passwords via email link
- **FR-004:** User profiles must support name, avatar image, and notification preferences
- **FR-005:** Users must be able to update their profile information

### Task Management Module
- **FR-006:** Users must be able to create tasks with title, description, due date, and priority level
- **FR-007:** Users must be able to edit their own tasks
- **FR-008:** Users must be able to delete their own tasks
- **FR-009:** Users must be able to mark tasks as complete/incomplete
- **FR-010:** Users must be able to filter tasks by status (pending/complete)
- **FR-011:** Users must be able to filter tasks by priority (high/medium/low)
- **FR-012:** Users must be able to search tasks by title or description keywords
- **FR-013:** Tasks must support due date with visual indicators for overdue tasks

### Collaboration Module
- **FR-014:** Users must be able to share tasks with other registered users
- **FR-015:** Users must be able to add comments to tasks
- **FR-016:** Users must receive email notifications for task assignments
- **FR-017:** Users must receive email notifications for comments on their tasks
- **FR-018:** Task owners must be able to remove shared access

## Non-Functional Requirements

### Performance
- **NFR-001:** System must support 1000 concurrent users
- **NFR-002:** API response time must be under 200ms for 95% of requests
- **NFR-003:** Database queries must be optimized with proper indexing
- **NFR-004:** Frontend must achieve Lighthouse score above 90

### Security
- **NFR-005:** All passwords must be hashed using bcrypt (cost factor 10)
- **NFR-006:** All data transmission must use HTTPS/TLS 1.3
- **NFR-007:** API must implement rate limiting (100 requests per minute per user)
- **NFR-008:** JWT tokens must expire after 24 hours
- **NFR-009:** SQL injection prevention through parameterized queries

### Reliability
- **NFR-010:** System must have 99.9% uptime SLA
- **NFR-011:** Database backups must run daily with 7-day retention
- **NFR-012:** Application logs must be retained for 30 days

### Scalability
- **NFR-013:** System must scale horizontally behind load balancer
- **NFR-014:** Database must support read replicas for query scaling

## Technology Constraints
- **Backend:** Node.js with Express framework
- **Frontend:** React 18 with TypeScript
- **Database:** PostgreSQL 15
- **Caching:** Redis for session management
- **Cloud Provider:** AWS (EC2, RDS, S3)
- **Email Service:** SendGrid API

## Integration Requirements
- **INT-001:** Integration with SendGrid API for transactional email notifications
- **INT-002:** Integration with AWS S3 for user avatar image storage
- **INT-003:** Integration with Stripe API for future premium subscription (Phase 2)

## Out of Scope (Phase 1)
- Mobile native applications (iOS/Android)
- Real-time collaborative editing
- File attachments to tasks
- Calendar view/integration
- Gantt charts or advanced project management features
- Premium subscription billing (reserved for Phase 2)

## Acceptance Criteria
- All functional requirements implemented and tested
- Unit test coverage above 80%
- Integration tests pass for all API endpoints
- Frontend E2E tests pass for critical user flows
- Security audit completed with no critical vulnerabilities
- Performance benchmarks meet NFR targets
- Documentation complete (API docs, deployment guide, user manual)
"""


async def test_brd_pipeline():
    """Test the complete BRD-to-WBS pipeline."""

    print("=" * 70)
    print("BRD-TO-WBS PIPELINE TEST")
    print("=" * 70)

    async with httpx.AsyncClient(timeout=300.0) as client:
        # Step 1: Register user
        print("\n[1/7] Registering test user...")
        try:
            register_resp = await client.post(
                f"{API_BASE}/auth/register",
                json={
                    "email": "test@example.com",
                    "password": "testpass123",
                    "full_name": "Test User"
                }
            )
            if register_resp.status_code == 200:
                print("      ✓ User registered successfully")
            else:
                print(f"      ! User might already exist (status: {register_resp.status_code})")
        except Exception as e:
            print(f"      ! Registration error (user might exist): {str(e)[:100]}")

        # Step 2: Login
        print("\n[2/7] Logging in...")
        login_resp = await client.post(
            f"{API_BASE}/auth/login",
            data={
                "username": "test@example.com",
                "password": "testpass123"
            }
        )

        if login_resp.status_code != 200:
            print(f"      ✗ Login failed: {login_resp.text}")
            print("\n⚠ Please ensure:")
            print("  1. API server is running (uvicorn api.main:app --reload)")
            print("  2. Database is accessible")
            print("  3. User credentials are correct")
            return

        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"      ✓ Logged in successfully")
        print(f"      Token: {token[:30]}...")

        # Step 3: Create project with BRD
        print("\n[3/7] Creating project with BRD...")
        create_resp = await client.post(
            f"{API_BASE}/projects",
            json={
                "name": "Task Management System",
                "description": "Full-featured task management with collaboration",
                "prompt": BRD_TEXT
            },
            headers=headers
        )

        if create_resp.status_code != 200:
            print(f"      ✗ Failed to create project: {create_resp.text}")
            return

        project_data = create_resp.json()
        project_id = project_data["id"]
        print(f"      ✓ Project created")
        print(f"      Project ID: {project_id}")
        print(f"      Status: {project_data.get('status')}")

        # Step 4: Wait for BRD-to-WBS processing
        print("\n[4/7] Waiting for BRD-to-WBS pipeline...")
        print("      This involves 4 stages:")
        print("      1. Requirements extraction (30-45s)")
        print("      2. Scope definition (30-45s)")
        print("      3. Architecture design (30-45s)")
        print("      4. WBS generation (30-45s)")
        print("      Expected total time: 2-3 minutes\n")

        max_attempts = 60
        start_time = datetime.now()

        for attempt in range(max_attempts):
            await asyncio.sleep(5)

            status_resp = await client.get(
                f"{API_BASE}/projects/{project_id}/status",
                headers=headers
            )

            if status_resp.status_code != 200:
                print(f"      ! Status check failed: {status_resp.status_code}")
                continue

            status_data = status_resp.json()
            current_status = status_data.get("status")
            elapsed = (datetime.now() - start_time).seconds

            print(f"      [{elapsed}s] Status: {current_status}")

            if current_status == "AWAITING_APPROVAL":
                print(f"\n      ✓ WBS generated successfully in {elapsed} seconds!")
                break
            elif current_status == "FAILED":
                error = status_data.get("error_message", "Unknown error")
                print(f"\n      ✗ Pipeline failed: {error}")
                return
            elif attempt == max_attempts - 1:
                print(f"\n      ⚠ Timeout after {elapsed} seconds")
                print(f"      Current status: {current_status}")
                return

        # Step 5: Get the generated plan
        print("\n[5/7] Fetching generated WBS plan...")
        plan_resp = await client.get(
            f"{API_BASE}/projects/{project_id}/plan",
            headers=headers
        )

        if plan_resp.status_code != 200:
            print(f"      ✗ Failed to fetch plan: {plan_resp.text}")
            return

        plan = plan_resp.json()
        print(f"      ✓ Plan retrieved successfully\n")

        print("      " + "=" * 60)
        print(f"      PROJECT SUMMARY")
        print("      " + "=" * 60)
        print(f"      {plan['project_summary']}\n")

        print("      " + "-" * 60)
        print(f"      TASKS BREAKDOWN")
        print("      " + "-" * 60)
        print(f"      Total Tasks: {plan['total_tasks']}")
        print(f"      Skills Required:")
        for skill, count in plan['skill_breakdown'].items():
            print(f"        - {skill.capitalize()}: {count} tasks")

        # Show sample tasks
        print("\n      " + "-" * 60)
        print(f"      SAMPLE TASKS (first 5)")
        print("      " + "-" * 60)
        for i, task in enumerate(plan['tasks'][:5], 1):
            print(f"\n      {i}. {task['title']}")
            print(f"         ID: {task['id']}")
            print(f"         Skill: {task['skill_required']}")
            print(f"         Depends on: {task['depends_on'] or 'none'}")
            print(f"         Criteria: {task['acceptance_criteria'][0] if task['acceptance_criteria'] else 'N/A'}")

        # Step 6: Approve the plan
        print("\n[6/7] Approving plan to start code generation...")
        approve_resp = await client.post(
            f"{API_BASE}/projects/{project_id}/plan/approve",
            json={"approved": True},
            headers=headers
        )

        if approve_resp.status_code != 200:
            print(f"      ✗ Failed to approve: {approve_resp.text}")
            return

        approve_data = approve_resp.json()
        print(f"      ✓ Plan approved!")
        print(f"      Message: {approve_data.get('message')}")
        print("\n      Pipeline will now:")
        print("      1. Router validates DAG and enqueues first wave")
        print("      2. Tasks distributed to Redis streams by skill")
        print("      3. Agent workers pick up tasks and generate code")
        print("      4. Task monitor tracks completion and enqueues next wave")
        print("      5. QA node runs tests")
        print("      6. Reviewer node performs code review")
        print("      7. Delivery node merges to main")

        # Step 7: Monitor initial task dispatch
        print("\n[7/7] Monitoring task dispatch (30 seconds)...")
        for i in range(6):
            await asyncio.sleep(5)
            status_resp = await client.get(
                f"{API_BASE}/projects/{project_id}/status",
                headers=headers
            )

            if status_resp.status_code == 200:
                status_data = status_resp.json()
                print(f"      [{(i+1)*5}s] Status: {status_data.get('status')}, "
                      f"Pending: {status_data.get('pending_tasks', 0)}, "
                      f"Completed: {status_data.get('completed_tasks', 0)}, "
                      f"Failed: {status_data.get('failed_tasks', 0)}")

        print("\n" + "=" * 70)
        print("TEST COMPLETE")
        print("=" * 70)
        print(f"\nProject ID: {project_id}")
        print(f"Monitor progress: GET {API_BASE}/projects/{project_id}/status")
        print(f"View plan: GET {API_BASE}/projects/{project_id}/plan")
        print("\n✓ BRD-to-WBS pipeline is working correctly!")


if __name__ == "__main__":
    try:
        asyncio.run(test_brd_pipeline())
    except KeyboardInterrupt:
        print("\n\n⚠ Test interrupted by user")
    except Exception as e:
        print(f"\n\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
