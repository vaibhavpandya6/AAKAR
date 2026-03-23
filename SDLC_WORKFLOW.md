# AAKAR SDLC Automation Workflow

## Overview
QA-gated integration ensures main branch always has tested code.

## Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│  1. Task Dequeued (Backend/Frontend/Database Agent)            │
│     • Creates branch from current main                          │
│     • Branch: agent/{agent-name}/task-{task_id}                 │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│  2. Dev Agent Writes Code                                       │
│     • Generates files based on task                             │
│     • Commits to task branch                                    │
│     • Does NOT merge to main yet                                │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│  3. QA Agent Creates Tests                                      │
│     • Reads task branch code                                    │
│     • Generates test files                                      │
│     • Creates QA branch: agent/qa-agent-1/task-qa_{id}_{hash}   │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│  4. QA Agent Runs Tests in Docker                               │
│     • Executes pytest in isolated sandbox                       │
│     • Parses test results (passed/failed/errors)                │
└─────────────────────────────────────────────────────────────────┘
                            ↓
                    ┌───────┴───────┐
                    │               │
              Tests PASS        Tests FAIL
                    │               │
                    ↓               ↓
      ┌─────────────────────┐   ┌──────────────────────┐
      │ 5. AUTO-MERGE       │   │ 5. Report Bugs       │
      │  • Extract original │   │  • Create bug report │
      │    task ID          │   │  • Mark task failed  │
      │  • Find dev branch  │   │  • Human review      │
      │  • Merge to main    │   │                      │
      │  • Main updated! ✓  │   └──────────────────────┘
      └─────────────────────┘
                    ↓
      ┌─────────────────────────────────────────────┐
      │ 6. Next Task Branches from Updated Main    │
      │    • Includes all previous tested code      │
      │    • Sequential builds                      │
      └─────────────────────────────────────────────┘
```

## Key Components

### 1. QA Agent Auto-Merge (agents/qa_agent/agent.py)
**Lines 474-488:**
```python
if test_result.all_passed:
    # Extract original task ID (qa_task_001_hash -> task_001)
    original_task_id = self._extract_original_task_id(task_id)

    # Merge dev branch to main
    merged = await self._merge_dev_branch_to_main(
        project_id=project_id,
        original_task_id=original_task_id,
        qa_task_id=task_id,
    )
```

**Lines 158-208:** Helper method searches for dev branch and merges:
- Tries: backend-agent-1, frontend-agent-1, database-agent-1
- Calls: `self.git.merge_to_main(project_id, dev_branch)`
- Logs: Success or failure

### 2. Git Manager Auto-Resolve (workspace_manager/git_manager.py)
**Lines 272-355:** `merge_to_main()` method
- Uses `git merge --no-ff` to preserve branch history
- Auto-resolves workspace.manifest.json conflicts
- Aborts failed merges to keep repo clean

**Lines 357-427:** `_try_auto_resolve_manifest()` helper
- Detects manifest-only conflicts
- Merges JSON file dictionaries (union of both sides)
- Auto-commits if only conflict is manifest

### 3. Branch Creation (workspace_manager/git_manager.py)
**Lines 159-203:** `create_branch()` method
- Branches from current main: `repo.create_head(branch_name, main_branch)`
- Format: `agent/{agent-name}/task-{task_id}`

## Benefits

✅ **Main always has tested code**
- Only merges after QA passes
- No untested code on main

✅ **Sequential builds**
- Each task builds on previous tested work
- No parallel branch conflicts

✅ **Auto-conflict resolution**
- workspace.manifest.json conflicts auto-resolved
- Minimal manual intervention

✅ **Branch history preserved**
- `--no-ff` merges create merge commits
- Easy to trace which task added what

## Testing the Workflow

### Create New Project
```bash
# Restart to load changes
.\stop.ps1
.\start.ps1 -SkipInfra

# Create project via API
# Monitor logs for:
# - "Task branch created"
# - "Changes committed"
# - "qa_task_all_passed"
# - "dev_branch_merged_after_qa_pass" ← KEY LOG
```

### Verify Merges
```bash
cd "workspaces/{project-id}"
git log --oneline --graph --all

# Look for merge commits:
# * Merge branch 'agent/backend-agent-1/task-task_001' into master
```

### Check Files on Main
```bash
git checkout master
ls -R backend frontend tests
```

## Fallback for Existing Projects

For projects with parallel branches (not QA-gated):
```bash
python merge_all_branches.py {project-id}
```

This force-merges all task branches with conflict resolution.

## Future Enhancements

- [ ] Rebase workflow for cleaner history
- [ ] Partial test failures (merge if critical tests pass)
- [ ] Retry logic for flaky tests
- [ ] Merge squashing option for cleaner commits
