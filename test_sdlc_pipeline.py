"""Manual testing script for SDLC pipeline validation.

This script helps you inspect and validate:
1. Git workflow (branches, commits, merges)
2. Generated code quality
3. File structure
4. Manifest tracking
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def run_git_command(workspace_path: Path, command: List[str]) -> Tuple[str, int]:
    """Run git command in workspace directory."""
    try:
        result = subprocess.run(
            ['git'] + command,
            cwd=workspace_path,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip(), result.returncode
    except Exception as e:
        return f"Error: {e}", 1


def print_section(title: str):
    """Print formatted section header."""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{title}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.RESET}\n")


def print_pass(message: str):
    """Print success message."""
    print(f"{Colors.GREEN}[OK] {message}{Colors.RESET}")


def print_fail(message: str):
    """Print failure message."""
    print(f"{Colors.RED}[FAIL] {message}{Colors.RESET}")


def print_info(message: str):
    """Print info message."""
    print(f"{Colors.YELLOW}[INFO] {message}{Colors.RESET}")


def test_git_structure(workspace_path: Path) -> Dict[str, any]:
    """Test git repository structure."""
    print_section("1. Git Repository Structure")

    results = {}

    # Check if git repo exists
    if not (workspace_path / ".git").exists():
        print_fail("Not a git repository")
        return {"passed": False}
    print_pass("Valid git repository")

    # Get current branch
    current_branch, _ = run_git_command(workspace_path, ['branch', '--show-current'])
    print_info(f"Current branch: {current_branch}")
    results['current_branch'] = current_branch

    # Count commits
    commit_count, _ = run_git_command(workspace_path, ['rev-list', '--count', 'HEAD'])
    print_info(f"Total commits: {commit_count}")
    results['commit_count'] = int(commit_count) if commit_count.isdigit() else 0

    # List all branches
    branches, _ = run_git_command(workspace_path, ['branch', '-a'])
    branch_list = [b.strip().replace('* ', '') for b in branches.split('\n') if b.strip()]

    dev_branches = [b for b in branch_list if 'agent/' in b and 'task-task_' in b]
    qa_branches = [b for b in branch_list if 'qa-agent' in b]

    print_info(f"Dev task branches: {len(dev_branches)}")
    print_info(f"QA test branches: {len(qa_branches)}")

    results['dev_branches'] = len(dev_branches)
    results['qa_branches'] = len(qa_branches)

    # Check for merge commits
    merge_commits, _ = run_git_command(workspace_path, ['log', '--oneline', '--merges'])
    merge_count = len(merge_commits.split('\n')) if merge_commits else 0
    print_info(f"Merge commits: {merge_count}")
    results['merge_commits'] = merge_count

    if merge_count > 0:
        print_pass(f"Found {merge_count} merge commits (QA-gated integration working)")
    else:
        print_fail("No merge commits found (branches not merged)")

    results['passed'] = True
    return results


def test_file_structure(workspace_path: Path) -> Dict[str, any]:
    """Test generated file structure."""
    print_section("2. Generated File Structure")

    results = {}

    # Define expected directories
    expected_dirs = {
        'backend': workspace_path / 'backend',
        'frontend': workspace_path / 'src',
        'migrations': workspace_path / 'migrations',
    }

    for name, path in expected_dirs.items():
        if path.exists():
            file_count = len(list(path.rglob('*.py' if name != 'frontend' else '*.*')))
            print_pass(f"{name.capitalize()} directory exists ({file_count} files)")
            results[f'{name}_files'] = file_count
        else:
            print_fail(f"{name.capitalize()} directory missing")
            results[f'{name}_files'] = 0

    # Count all generated files
    all_files = list(workspace_path.rglob('*'))
    code_files = [f for f in all_files if f.is_file() and not '.git' in str(f)]

    print_info(f"Total files generated: {len(code_files)}")
    results['total_files'] = len(code_files)

    # List file types
    extensions = {}
    for f in code_files:
        ext = f.suffix or 'no-ext'
        extensions[ext] = extensions.get(ext, 0) + 1

    print_info("File types:")
    for ext, count in sorted(extensions.items(), key=lambda x: x[1], reverse=True):
        print(f"  {ext}: {count}")

    results['extensions'] = extensions
    results['passed'] = len(code_files) > 0

    return results


def test_manifest(workspace_path: Path) -> Dict[str, any]:
    """Test workspace manifest."""
    print_section("3. Workspace Manifest")

    manifest_path = workspace_path / 'workspace.manifest.json'

    if not manifest_path.exists():
        print_fail("workspace.manifest.json not found")
        return {'passed': False}

    try:
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)

        print_pass("Manifest file valid JSON")

        # Check structure
        if 'project_id' in manifest:
            print_pass(f"Project ID: {manifest['project_id']}")

        if 'created_at' in manifest:
            print_pass(f"Created: {manifest['created_at']}")

        if 'files' in manifest:
            file_count = len(manifest['files'])
            print_pass(f"Tracked files: {file_count}")

            # Show file tracking details
            print_info("Tracked file breakdown:")
            agents = {}
            for file_path, metadata in manifest['files'].items():
                agent = metadata.get('agent', 'unknown')
                agents[agent] = agents.get(agent, 0) + 1

            for agent, count in sorted(agents.items()):
                print(f"  {agent}: {count} files")

            return {
                'passed': True,
                'tracked_files': file_count,
                'agents': agents,
            }
        else:
            print_fail("No 'files' section in manifest")
            return {'passed': False}

    except json.JSONDecodeError as e:
        print_fail(f"Invalid JSON: {e}")
        return {'passed': False}


def test_code_quality(workspace_path: Path) -> Dict[str, any]:
    """Test generated code quality."""
    print_section("4. Code Quality Checks")

    results = {}

    # Test Python syntax
    python_files = list(workspace_path.rglob('*.py'))
    python_files = [f for f in python_files if '.git' not in str(f)]

    if python_files:
        print_info(f"Checking {len(python_files)} Python files...")
        syntax_errors = []

        for py_file in python_files:
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    compile(f.read(), str(py_file), 'exec')
            except SyntaxError as e:
                syntax_errors.append((py_file.name, str(e)))

        if syntax_errors:
            print_fail(f"Python syntax errors: {len(syntax_errors)}")
            for filename, error in syntax_errors[:5]:
                print(f"  {filename}: {error}")
            results['python_syntax'] = False
        else:
            print_pass(f"All {len(python_files)} Python files have valid syntax")
            results['python_syntax'] = True

    # Test TypeScript/TSX structure (basic check)
    tsx_files = list(workspace_path.rglob('*.tsx')) + list(workspace_path.rglob('*.ts'))
    tsx_files = [f for f in tsx_files if '.git' not in str(f)]

    if tsx_files:
        print_info(f"Found {len(tsx_files)} TypeScript files")

        # Basic checks
        has_imports = 0
        has_exports = 0

        for tsx_file in tsx_files:
            try:
                with open(tsx_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if 'import' in content:
                        has_imports += 1
                    if 'export' in content:
                        has_exports += 1
            except Exception:
                pass

        print_pass(f"Files with imports: {has_imports}/{len(tsx_files)}")
        print_pass(f"Files with exports: {has_exports}/{len(tsx_files)}")
        results['tsx_structure'] = True

    results['passed'] = results.get('python_syntax', True)
    return results


def show_sample_files(workspace_path: Path):
    """Show sample generated files."""
    print_section("5. Sample Generated Code")

    # Find interesting files to display
    samples = [
        ('Backend API', workspace_path / 'backend' / 'main.py'),
        ('Database Models', workspace_path / 'backend' / 'models.py'),
        ('React Component', workspace_path / 'src' / 'components' / 'NotesList.tsx'),
        ('Migration', workspace_path / 'migrations' / 'db' / 'migrations' / 'versions' / '001_create_notes_app_schema.py'),
    ]

    for title, file_path in samples:
        if file_path.exists():
            print(f"\n{Colors.BOLD}{title}:{Colors.RESET} {file_path.name}")
            print(f"{Colors.YELLOW}{'-'*70}{Colors.RESET}")

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()[:20]  # First 20 lines
                    for i, line in enumerate(lines, 1):
                        print(f"{i:3d} | {line.rstrip()}")

                total_lines = len(open(file_path, 'r', encoding='utf-8').readlines())
                if total_lines > 20:
                    print(f"    | ... ({total_lines - 20} more lines)")

            except Exception as e:
                print(f"    | Error reading file: {e}")


def show_git_history(workspace_path: Path):
    """Show git commit history."""
    print_section("6. Git Commit History")

    # Show recent commits
    log_output, _ = run_git_command(
        workspace_path,
        ['log', '--oneline', '--graph', '--all', '-20']
    )

    print(log_output)

    # Show merge commits specifically
    print(f"\n{Colors.BOLD}Merge Commits (QA-gated integration):{Colors.RESET}")
    merge_output, _ = run_git_command(
        workspace_path,
        ['log', '--oneline', '--merges', '-10']
    )

    if merge_output:
        print(merge_output)
    else:
        print_info("No merge commits found (branches not yet merged)")


def main():
    """Main testing function."""
    if len(sys.argv) < 2:
        print("Usage: python test_sdlc_pipeline.py <project_id>")
        print("\nExample:")
        print("  python test_sdlc_pipeline.py 00a8890f-2f0c-4e58-90dc-4c61fefbcc7d")
        sys.exit(1)

    project_id = sys.argv[1]
    workspace_path = Path(f"workspaces/{project_id}")

    if not workspace_path.exists():
        print_fail(f"Workspace not found: {workspace_path}")
        sys.exit(1)

    print(f"{Colors.BOLD}SDLC Pipeline Validation{Colors.RESET}")
    print(f"Project: {project_id}")
    print(f"Path: {workspace_path.absolute()}\n")

    # Run all tests
    results = {}
    results['git'] = test_git_structure(workspace_path)
    results['files'] = test_file_structure(workspace_path)
    results['manifest'] = test_manifest(workspace_path)
    results['quality'] = test_code_quality(workspace_path)

    # Show samples
    show_sample_files(workspace_path)
    show_git_history(workspace_path)

    # Summary
    print_section("Summary")

    all_passed = all(r.get('passed', False) for r in results.values())

    if all_passed:
        print_pass("All validation checks passed!")
    else:
        print_fail("Some validation checks failed")

    print(f"\n{Colors.BOLD}Key Metrics:{Colors.RESET}")
    print(f"  Commits: {results['git'].get('commit_count', 0)}")
    print(f"  Dev branches: {results['git'].get('dev_branches', 0)}")
    print(f"  Merge commits: {results['git'].get('merge_commits', 0)}")
    print(f"  Files generated: {results['files'].get('total_files', 0)}")
    print(f"  Files tracked: {results['manifest'].get('tracked_files', 0)}")

    print(f"\n{Colors.BOLD}Next Steps:{Colors.RESET}")
    print("1. Review sample code above")
    print("2. Check git history for merge commits")
    print("3. Create a new project to test QA-gated merge")
    print(f"4. Explore workspace: cd {workspace_path}")


if __name__ == "__main__":
    main()
