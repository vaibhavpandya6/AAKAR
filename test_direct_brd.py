"""Direct test of BRD-to-WBS node - No API, No Auth required.

Tests the FULL BRD-to-WBS pipeline with all 9 stages:
1. Requirements extraction (Agent 2)
2. Scope definition (Agent 3)
3. Proposal generation (Agent 3.5)
4. Architecture design (Agent 4)
5. SOW generation (Agent 5)
6. WBS generation per module (Agent 6)
7. Test case generation per agent
8. Project analysis
9. Task transformation
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.nodes.brd_to_wbs_node import brd_to_wbs_node
from orchestrator.state import PlatformState, ProjectStatus

# Sample BRD (matching notebook quality)
BRD_TEXT = """
# Business Requirements Document

## Project Overview
**Project Name:** Task Management System
**Domain:** Productivity Software
**Timeline:** 12 weeks
**Team Size:** 5 developers (2 backend, 2 frontend, 1 QA)
**Budget:** $150,000

## Executive Summary
A comprehensive task management system enabling teams to create, organize, and track
tasks with real-time collaboration features. The system will support multiple user
roles, task assignments, and notification systems to improve team productivity.

## Functional Requirements

### User Management
- FR-001: Users must register with email and password (High Priority)
- FR-002: Users must login with email/password (High Priority)
- FR-003: Users must reset forgotten passwords via email (High Priority)
- FR-004: User profiles support name, avatar, preferences (Medium Priority)
- FR-005: Admin users can manage all users and roles (Medium Priority)

### Task Management
- FR-006: Users create tasks with title, description, due date, priority, labels (High Priority)
- FR-007: Users edit their own tasks (High Priority)
- FR-008: Users delete their own tasks (Medium Priority)
- FR-009: Users mark tasks as complete with completion notes (High Priority)
- FR-010: Users filter tasks by status, priority, date range, label (High Priority)
- FR-011: Users search tasks by title/description with full-text search (Medium Priority)
- FR-012: Tasks support subtasks with completion tracking (Medium Priority)
- FR-013: Users can set recurring tasks (weekly, monthly) (Low Priority)

### Collaboration
- FR-014: Users share tasks with other users via email invite (High Priority)
- FR-015: Users comment on shared tasks with threading (High Priority)
- FR-016: Users receive real-time notifications for assignments and comments (High Priority)
- FR-017: Users can create teams and team-level task boards (Medium Priority)
- FR-018: Activity feed shows recent changes to shared tasks (Medium Priority)

### Dashboard & Reporting
- FR-019: Users see dashboard with task summary widgets (Medium Priority)
- FR-020: Users generate task completion reports by date range (Low Priority)

## Non-Functional Requirements
- NFR-001: Support 1000 concurrent users (Performance)
- NFR-002: API response under 200ms for 95% of requests (Performance)
- NFR-003: 99.9% uptime monthly (Availability)
- NFR-004: Passwords hashed with bcrypt, sessions with JWT (Security)
- NFR-005: HTTPS encryption for all data in transit (Security)
- NFR-006: WCAG 2.1 AA accessibility compliance (Usability)
- NFR-007: Support for Firefox, Chrome, Safari, Edge (Compatibility)

## Technology Stack (Constraints)
- Backend: Node.js with Express (team expertise)
- Frontend: React with TypeScript (company standard)
- Database: PostgreSQL (existing infrastructure)
- Cloud: AWS (existing account with credits)
- CI/CD: GitHub Actions (existing setup)

## Integrations
- INT-001: SendGrid for email notifications (existing contract)
- INT-002: AWS S3 for avatar and file storage
- INT-003: Sentry for error monitoring (existing setup)

## Timeline & Milestones
- Week 1-2: Setup & User Management
- Week 3-4: Core Task CRUD
- Week 5-6: Collaboration Features
- Week 7-8: Dashboard & Notifications
- Week 9-10: Testing & Bug Fixes
- Week 11-12: Performance Optimization & Launch Prep

## Success Criteria
- 95% of beta testers can create and complete tasks without assistance
- All critical security vulnerabilities addressed before launch
- Performance benchmarks met under load testing
"""


async def test_brd_to_wbs_direct():
    """Test full BRD-to-WBS pipeline directly."""

    print("=" * 70)
    print("FULL BRD-TO-WBS PIPELINE TEST (9 Stages)")
    print("=" * 70)
    print()

    # Create initial state
    print("📝 Setting up test state...")
    state = PlatformState(
        project_id="test-project-001",
        user_id="test-user",
        original_prompt=BRD_TEXT,
        project_status=ProjectStatus.PLANNING,
    )
    print(f"   ✓ Project ID: {state['project_id']}")
    print(f"   ✓ BRD length: {len(BRD_TEXT)} characters")
    print()

    # Check NVIDIA API key
    print("🔑 Checking NVIDIA API configuration...")
    from config.settings import settings

    if not settings.nvidia_api_key:
        print("   ✗ NVIDIA_API_KEY not set in .env")
        print("\n   Add to .env file:")
        print("   NVIDIA_API_KEY=nvapi-your-key-here")
        return

    print(f"   ✓ NVIDIA API key configured ({settings.nvidia_api_key[:15]}...)")
    print(f"   ✓ Model: {settings.nvidia_model} (all stages)")
    print(f"   ✓ Fallback: qwen/qwen3.5-122b-a10b")
    print(f"   ✓ Rate Limit: 39 RPM (~1.5s between calls)")
    print()

    # Run BRD-to-WBS pipeline
    print("🚀 Running BRD-to-WBS pipeline (9 stages)...")
    print("   This takes 10-15 minutes with all stages:")
    print("   Stage 1: Requirements extraction (Agent 2)")
    print("   Stage 2: Scope definition (Agent 3)")
    print("   Stage 3: Proposal generation (Agent 3.5)")
    print("   Stage 4: Architecture design (Agent 4)")
    print("   Stage 5: SOW generation (Agent 5)")
    print("   Stage 6: WBS generation per module (Agent 6)")
    print("   Stage 7: Test case generation per agent")
    print("   Stage 8: Project analysis")
    print("   Stage 9: Task transformation")
    print()

    try:
        result = await brd_to_wbs_node(state)

        # Check for errors
        if result.get("error_message"):
            print(f"   ✗ Pipeline failed: {result['error_message']}")
            print(f"   Status: {result.get('project_status')}")
            return

        # Success!
        print("   ✓ Pipeline completed successfully!")
        print()

        # Display results
        print("=" * 70)
        print("RESULTS")
        print("=" * 70)
        print()

        task_dag = result.get("task_dag", [])
        project_summary = result.get("project_summary", "")
        metadata = result.get("_metadata", {})

        # Project Summary
        print("📊 PROJECT SUMMARY")
        print("-" * 60)
        print(project_summary)
        print()

        # Task Statistics
        print("📋 TASK STATISTICS")
        print("-" * 60)
        print(f"   Total Tasks: {len(task_dag)}")

        # Count by skill
        skill_counts = {}
        for task in task_dag:
            skill = task.get("skill_required", "unknown")
            skill_counts[skill] = skill_counts.get(skill, 0) + 1

        print("\n   Agent Distribution:")
        for skill, count in sorted(skill_counts.items()):
            print(f"     - {skill.capitalize()} Agent: {count} tasks")

        # Count by module
        module_counts = {}
        for task in task_dag:
            module = task.get("module", "core")
            module_counts[module] = module_counts.get(module, 0) + 1

        print("\n   Module Distribution:")
        for module, count in sorted(module_counts.items()):
            print(f"     - {module}: {count} tasks")

        # Count by priority
        priority_counts = {}
        for task in task_dag:
            priority = task.get("priority", "Should Have")
            priority_counts[priority] = priority_counts.get(priority, 0) + 1

        print("\n   Priority Distribution:")
        for priority, count in sorted(priority_counts.items()):
            print(f"     - {priority}: {count} tasks")

        # Story points
        total_sp = sum(task.get("story_points", 3) for task in task_dag)
        total_effort = sum(task.get("effort_days", 1) for task in task_dag)
        print(f"\n   Total Story Points: {total_sp}")
        print(f"   Total Effort Days: {total_effort}")
        print()

        # Architecture Info (from metadata)
        if metadata.get("architecture"):
            arch = metadata["architecture"]
            print("🏗️ ARCHITECTURE")
            print("-" * 60)
            print(f"   Pattern: {arch.get('architecture_pattern', 'N/A')}")
            tech = arch.get("tech_stack", {})
            if tech:
                if isinstance(tech.get("backend"), dict):
                    print(f"   Backend: {tech['backend'].get('framework', 'N/A')}")
                if isinstance(tech.get("frontend"), dict):
                    print(f"   Frontend: {tech['frontend'].get('framework', 'N/A')}")
                if isinstance(tech.get("database"), dict):
                    print(f"   Database: {tech['database'].get('product', 'N/A')}")
            print(f"   APIs: {len(arch.get('apis', []))} endpoints defined")
            print()

        # SOW Info (from metadata)
        if metadata.get("sow"):
            sow = metadata["sow"]
            print("📃 STATEMENT OF WORK")
            print("-" * 60)
            milestones = sow.get("milestones", [])
            print(f"   Milestones: {len(milestones)}")
            for ms in milestones[:3]:
                print(f"     - {ms.get('milestone', 'N/A')} ({ms.get('payment_percent', 0)}%)")
            if len(milestones) > 3:
                print(f"     ... and {len(milestones) - 3} more")
            print()

        # Test Cases (from metadata)
        if metadata.get("test_cases"):
            print("🧪 TEST CASES")
            print("-" * 60)
            test_cases = metadata["test_cases"]
            total_tests = 0
            for agent, agent_data in test_cases.items():
                if isinstance(agent_data, dict):
                    tc_count = len(agent_data.get("test_cases", []))
                    total_tests += tc_count
                    print(f"   {agent.capitalize()} Agent: {tc_count} test cases")
            print(f"   Total: {total_tests} test cases")
            print()

        # Sample Tasks (first 5)
        print("📝 SAMPLE TASKS (first 5)")
        print("-" * 60)
        for i, task in enumerate(task_dag[:5], 1):
            print(f"\n   {i}. [{task['wbs_id']}] {task['title']}")
            print(f"      Agent: {task['skill_required']} | Module: {task.get('module', 'N/A')}")
            print(f"      Priority: {task.get('priority', 'N/A')} | SP: {task.get('story_points', 'N/A')}")
            deps = task.get('depends_on', [])
            print(f"      Dependencies: {', '.join(deps) if deps else 'none'}")
            req_ids = task.get('req_ids', [])
            print(f"      Requirements: {', '.join(req_ids[:3]) if req_ids else 'N/A'}")
            print(f"      Criteria: {task['acceptance_criteria'][0][:70]}...")
            if task.get("test_cases"):
                print(f"      Test Cases: {len(task['test_cases'])} linked")

        print()
        print("=" * 70)
        print("✓ TEST PASSED")
        print("=" * 70)
        print()
        print(f"📈 Summary: {len(task_dag)} tasks | {total_sp} story points | {total_effort} effort days")
        print(f"🎯 Status: {result.get('project_status')}")
        print()
        print("📁 Task DAG structure is valid and ready for router node!")
        print()

        # Save to file for inspection
        import json
        output_file = "test_output_wbs.json"

        # Prepare output data
        output_data = {
            "project_summary": project_summary,
            "statistics": {
                "total_tasks": len(task_dag),
                "total_story_points": total_sp,
                "total_effort_days": total_effort,
                "skill_breakdown": skill_counts,
                "module_breakdown": module_counts,
                "priority_breakdown": priority_counts,
            },
            "tasks": task_dag,
        }

        # Include metadata if available
        if metadata:
            output_data["pipeline_outputs"] = {
                "requirements": metadata.get("requirements", {}),
                "scope": metadata.get("scope", {}),
                "proposal": metadata.get("proposal", {}),
                "architecture": metadata.get("architecture", {}),
                "sow": metadata.get("sow", {}),
                "analysis": metadata.get("analysis", {}),
                "test_cases": metadata.get("test_cases", {}),
            }

        with open(output_file, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"💾 Full output saved to: {output_file}")
        print("   - Includes all pipeline stage outputs")
        print("   - Includes all test cases by agent")
        print("   - Includes architecture and SOW details")

    except Exception as e:
        print(f"   ✗ Pipeline crashed: {e}")
        import traceback
        traceback.print_exc()
        print()
        print("Common issues:")
        print("  - NVIDIA API key invalid or quota exceeded")
        print("  - Network connectivity issues")
        print("  - Model not available or rate limited")
        print("  - Check NVIDIA API dashboard for usage/errors")


if __name__ == "__main__":
    try:
        asyncio.run(test_brd_to_wbs_direct())
    except KeyboardInterrupt:
        print("\n\n⚠ Test interrupted")
    except Exception as e:
        print(f"\n\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
