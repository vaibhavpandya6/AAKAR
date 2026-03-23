"""Merge all task branches to main/master for a project workspace."""

import re
import sys
from pathlib import Path

from git import Repo
from workspace_manager.git_manager import GitManager


def natural_sort_key(branch_name: str) -> tuple:
    """Extract task number for natural sorting (task_001 before task_002)."""
    match = re.search(r'task-(\w+)', branch_name)
    if match:
        task_id = match.group(1)
        # Extract numeric part if exists (task_001 -> 001)
        num_match = re.search(r'(\d+)', task_id)
        if num_match:
            return (0, int(num_match.group(1)), task_id)
        return (1, 0, task_id)
    return (2, 0, branch_name)


def merge_all_branches(project_id: str):
    """Merge all agent task branches to main in order.

    Args:
        project_id: Project identifier (workspace folder name)
    """
    git_manager = GitManager()
    repo_path = git_manager._get_repo_path(project_id)
    repo = Repo(repo_path)

    # Get main branch
    main_branch = git_manager._get_main_branch(repo)
    print(f"Main branch: {main_branch.name}")

    # Collect all agent task branches (exclude QA test branches for now)
    dev_branches = [
        b.name for b in repo.heads
        if b.name.startswith("agent/") and "/task-task_" in b.name
    ]

    # Sort by task number
    dev_branches.sort(key=natural_sort_key)

    print(f"\nFound {len(dev_branches)} dev task branches to merge:\n")

    merged_count = 0
    failed_count = 0
    skipped_count = 0

    for branch_name in dev_branches:
        try:
            print(f"Merging: {branch_name}... ", end="", flush=True)

            # Check if already merged (no diff)
            branch_commit = repo.heads[branch_name].commit
            main_commit = main_branch.commit

            # Simple check: if branch commit is in main history, skip
            if repo.is_ancestor(branch_commit, main_commit):
                print("[SKIP] Already merged")
                skipped_count += 1
                continue

            # Merge
            success = git_manager.merge_to_main(project_id, branch_name)
            if success:
                print("[OK] Merged successfully")
                merged_count += 1
            else:
                print("[SKIP] No changes to merge")
                skipped_count += 1

        except Exception as e:
            print(f"[FAIL] {e}")
            failed_count += 1

    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Merged:  {merged_count}")
    print(f"  Skipped: {skipped_count}")
    print(f"  Failed:  {failed_count}")
    print(f"{'='*60}")

    # Optionally merge QA test branches
    qa_branches = [
        b.name for b in repo.heads
        if b.name.startswith("agent/qa-agent") and "/task-" in b.name
    ]

    if qa_branches:
        print(f"\nFound {len(qa_branches)} QA test branches. Skipping QA branches.")
        print("(QA branches typically don't need to be merged to main)")



if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python merge_all_branches.py <project_id>")
        print("\nExample:")
        print("  python merge_all_branches.py 00a8890f-2f0c-4e58-90dc-4c61fefbcc7d")
        sys.exit(1)

    project_id = sys.argv[1]
    merge_all_branches(project_id)
