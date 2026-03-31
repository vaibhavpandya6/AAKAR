"""Test BRD-to-WBS pipeline with your custom BRD file.

Usage:
    python test_custom_brd.py path/to/your/brd.md
    python test_custom_brd.py path/to/your/brd.txt
    python test_custom_brd.py path/to/your/brd.pdf

Supports: .md, .txt, .pdf, .docx files
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.nodes.brd_to_wbs_node import brd_to_wbs_node
from orchestrator.state import PlatformState, ProjectStatus


def read_brd_file(file_path: str) -> str:
    """Read BRD content from file (supports txt, md, pdf, docx)."""
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"BRD file not found: {file_path}")

    # Text files (.txt, .md)
    if path.suffix.lower() in ['.txt', '.md']:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    # PDF files
    elif path.suffix.lower() == '.pdf':
        try:
            import PyPDF2
            with open(path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                text = []
                for page in reader.pages:
                    text.append(page.extract_text())
                return '\n\n'.join(text)
        except ImportError:
            print("   ⚠ PDF support requires PyPDF2: pip install PyPDF2")
            sys.exit(1)

    # Word documents (.docx)
    elif path.suffix.lower() == '.docx':
        try:
            from docx import Document
            doc = Document(path)
            return '\n\n'.join([para.text for para in doc.paragraphs])
        except ImportError:
            print("   ⚠ DOCX support requires python-docx: pip install python-docx")
            sys.exit(1)

    else:
        raise ValueError(f"Unsupported file format: {path.suffix}. Use .txt, .md, .pdf, or .docx")


async def test_custom_brd(brd_path: str):
    """Test BRD-to-WBS with custom BRD file."""

    print("=" * 70)
    print("CUSTOM BRD-TO-WBS PIPELINE TEST")
    print("=" * 70)
    print()

    # Read BRD file
    print(f"📄 Reading BRD from: {brd_path}")
    try:
        brd_text = read_brd_file(brd_path)
        print(f"   ✓ BRD loaded: {len(brd_text)} characters")
        print(f"   ✓ Word count: ~{len(brd_text.split())} words")
    except Exception as e:
        print(f"   ✗ Failed to read BRD: {e}")
        return
    print()

    # Validate BRD has some content
    if len(brd_text.strip()) < 100:
        print("   ⚠ Warning: BRD seems very short (< 100 chars)")
        print("   This might not generate good results.")
        print()

    # Check NVIDIA API key
    print("🔑 Checking NVIDIA API configuration...")
    from config.settings import settings

    if not settings.nvidia_api_key:
        print("   ✗ NVIDIA_API_KEY not set in .env")
        print("\n   Add to .env file:")
        print("   NVIDIA_API_KEY=nvapi-your-key-here")
        return

    print(f"   ✓ NVIDIA API key configured")
    print(f"   ✓ Primary Model: {settings.nvidia_model}")
    print(f"   ✓ SOW Model: {settings.nvidia_sow_model}")
    print(f"   ✓ Test Model: {settings.nvidia_test_model}")
    print()

    # Create state
    project_name = Path(brd_path).stem.replace('_', '-').replace(' ', '-')
    print(f"📝 Creating project: {project_name}")
    state = PlatformState(
        project_id=f"{project_name}-001",
        user_id="test-user",
        original_prompt=brd_text,
        project_status=ProjectStatus.PLANNING,
    )
    print()

    # Run pipeline
    print("🚀 Running BRD-to-WBS pipeline (9 stages)...")
    print("   ⏱️  Expected time: 10-15 minutes")
    print()
    print("   Stage 1: Requirements extraction (Agent 2)")
    print("   Stage 2: Scope definition (Agent 3)")
    print("   Stage 3: Proposal generation (Agent 3.5)")
    print("   Stage 4: Architecture design (Agent 4)")
    print("   Stage 5: SOW generation (Agent 5)")
    print("   Stage 6: WBS per module (Agent 6)")
    print("   Stage 7: Test cases (Agent 7)")
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

        # Requirements extracted
        if metadata.get("requirements"):
            req = metadata["requirements"]
            fr_count = len(req.get("functional_requirements", []))
            nfr_count = len(req.get("non_functional_requirements", []))
            int_count = len(req.get("integrations", []))
            gap_count = len(req.get("gaps", []))

            print("📑 REQUIREMENTS EXTRACTED")
            print("-" * 60)
            print(f"   Functional: {fr_count}")
            print(f"   Non-Functional: {nfr_count}")
            print(f"   Integrations: {int_count}")
            print(f"   Gaps/Risks: {gap_count}")
            print()

        # Architecture
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
            print(f"   APIs: {len(arch.get('apis', []))} endpoints")
            print()

        # SOW
        if metadata.get("sow"):
            sow = metadata["sow"]
            milestones = sow.get("milestones", [])
            print("📃 STATEMENT OF WORK")
            print("-" * 60)
            print(f"   Milestones: {len(milestones)}")
            if milestones:
                for i, ms in enumerate(milestones[:3], 1):
                    print(f"     {i}. {ms.get('milestone', 'N/A')} ({ms.get('payment_percent', 0)}%)")
                if len(milestones) > 3:
                    print(f"     ... and {len(milestones) - 3} more")
            print()

        # Test Cases
        if metadata.get("test_cases"):
            print("🧪 TEST CASES GENERATED")
            print("-" * 60)
            test_cases = metadata["test_cases"]
            total_tests = 0
            for agent, agent_data in test_cases.items():
                if isinstance(agent_data, dict):
                    tc_count = len(agent_data.get("test_cases", []))
                    total_tests += tc_count
                    print(f"   {agent.capitalize()} Agent: {tc_count} tests")
            print(f"   Total: {total_tests} test cases")
            print()

        # Sample Tasks
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

        print()
        print("=" * 70)
        print("✓ TEST PASSED")
        print("=" * 70)
        print()
        print(f"📈 Summary: {len(task_dag)} tasks | {total_sp} story points | {total_effort} effort days")
        print(f"🎯 Status: {result.get('project_status')}")
        print()

        # Save output
        import json
        output_file = f"output_{project_name}_wbs.json"

        output_data = {
            "brd_source": str(brd_path),
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
        print("   - Complete WBS with all tasks")
        print("   - Requirements, Architecture, SOW")
        print("   - Test cases per agent")
        print("   - Ready for import into project pipeline")

    except Exception as e:
        print(f"   ✗ Pipeline crashed: {e}")
        import traceback
        traceback.print_exc()
        print()
        print("Common issues:")
        print("  - NVIDIA API key invalid or quota exceeded")
        print("  - Network connectivity issues")
        print("  - Model rate limits")
        print("  - BRD format issues (too short, unclear)")


def main():
    """Main entry point."""

    # Check args
    if len(sys.argv) < 2:
        print("Usage: python test_custom_brd.py path/to/your/brd.md")
        print()
        print("Examples:")
        print("  python test_custom_brd.py my_project_brd.md")
        print("  python test_custom_brd.py docs/requirements.txt")
        print("  python test_custom_brd.py ./BRD_Project_X.pdf")
        print()
        print("Supported formats: .txt, .md, .pdf, .docx")
        sys.exit(1)

    brd_path = sys.argv[1]

    try:
        asyncio.run(test_custom_brd(brd_path))
    except KeyboardInterrupt:
        print("\n\n⚠ Test interrupted")
    except Exception as e:
        print(f"\n\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
