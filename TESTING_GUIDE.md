# Manual Testing Guide for SDLC Pipeline

Quick reference for manually inspecting and validating your SDLC automation pipeline.

## Quick Start

### Run Automated Validation
```bash
python test_sdlc_pipeline.py 00a8890f-2f0c-4e58-90dc-4c61fefbcc7d
```

This will check:
- ✓ Git structure (branches, commits, merges)
- ✓ Generated files (backend, frontend, migrations)
- ✓ Workspace manifest tracking
- ✓ Code syntax and quality
- ✓ Sample code preview

---

## Manual Commands

### 1. Navigate to Workspace
```bash
cd "workspaces/00a8890f-2f0c-4e58-90dc-4c61fefbcc7d"
```

### 2. Inspect Git Structure

#### View all branches
```bash
git branch -a
# Shows: agent/backend-agent-1/task-task_001, etc.
```

#### View commit history (graph)
```bash
git log --oneline --graph --all --decorate | head -50
# Look for: Merge branch 'agent/...' into master
```

#### Count commits
```bash
git rev-list --count HEAD
# Should be > number of tasks (includes merges)
```

#### See merge commits (QA-gated integration)
```bash
git log --oneline --merges
# Each line = 1 task merged after QA passed
```

#### Check current branch and status
```bash
git status
git branch --show-current
```

### 3. Inspect Generated Files

#### List all files
```bash
# On Windows PowerShell
Get-ChildItem -Recurse -File | Where-Object {$_.FullName -notlike "*\.git\*"} | Select-Object FullName

# On Bash
find . -type f -not -path "./.git/*" | sort
```

#### Count files by type
```bash
# Python files
find . -name "*.py" -not -path "./.git/*" | wc -l

# TypeScript/React files
find . -name "*.tsx" -o -name "*.ts" | wc -l

# CSS files
find . -name "*.css" | wc -l
```

#### View directory structure
```bash
ls -R backend/
ls -R src/
ls -R migrations/
```

### 4. Inspect Generated Code

#### Backend API
```bash
cat backend/main.py
# Check: FastAPI routes, endpoints, error handling
```

#### Database Models
```bash
cat backend/models.py
cat backend/database.py
# Check: SQLAlchemy models, database connection
```

#### React Components
```bash
cat src/components/NotesList.tsx
cat src/components/AddNoteForm.tsx
# Check: React hooks, API calls, state management
```

#### Database Migrations
```bash
cat migrations/db/migrations/versions/001_create_notes_app_schema.py
# Check: SQL schema creation
```

### 5. Validate Code Syntax

#### Python syntax check
```bash
python -m py_compile backend/*.py
python -m py_compile backend/routers/*.py
python -m py_compile migrations/db/migrations/versions/*.py

# If no output = syntax OK
```

#### Check Python imports
```bash
grep -r "^import\|^from" backend/ | head -20
# Verify: No circular imports, valid module names
```

#### Check TypeScript structure
```bash
grep -r "export default" src/
grep -r "interface" src/
# Verify: Proper exports, TypeScript types
```

### 6. Inspect Workspace Manifest

#### View manifest
```bash
cat workspace.manifest.json | python -m json.tool
```

#### Count tracked files
```bash
cat workspace.manifest.json | grep -o '"agent":' | wc -l
```

#### Files by agent
```bash
cat workspace.manifest.json | grep '"agent":' | sort | uniq -c
```

### 7. Test Merge Workflow

#### Checkout a task branch
```bash
git checkout agent/backend-agent-1/task-task_002
ls -la backend/
# Check: Does it have files from task_001? (Sequential build test)
```

#### Compare branches
```bash
# Files in task_001 vs task_002
git diff agent/backend-agent-1/task-task_001 agent/backend-agent-1/task-task_002 --name-only
```

#### Check if branch is merged
```bash
git branch --merged master | grep "agent/"
# Lists all branches already merged to master
```

#### View merge details
```bash
git show --stat HEAD  # If on master
# Shows what the last merge added
```

### 8. Validate QA-Gated Integration

#### Check for QA test files
```bash
find . -path "*/tests/*" -name "test_*.py"
# QA agent should have created test files
```

#### View QA agent branches
```bash
git branch | grep "qa-agent"
```

#### Check QA branch content
```bash
git checkout agent/qa-agent-1/task-qa_task_001_xxxxx
cat tests/test_*.py
```

### 9. Compare Master Timeline

#### See what's on master
```bash
git checkout master
ls -R
# Should have ALL files from merged tasks
```

#### Master commit timeline
```bash
git log --oneline master
# Should show:
# - Initial commit
# - Merge task_001
# - Merge task_002
# - etc.
```

### 10. Debug Issues

#### Check for uncommitted changes
```bash
git status
git diff
```

#### Check for merge conflicts
```bash
git log --oneline --all | grep -i conflict
```

#### View detailed commit info
```bash
git show <commit-hash>
git show HEAD~5  # Show 5 commits ago
```

#### Check branch divergence
```bash
git log --oneline master..agent/backend-agent-1/task-task_003
# Shows commits in branch but not in master
```

---

## Expected Results

### ✓ Good Signs
- Master has 15+ commits (initial + task merges)
- 20+ files on master branch
- Merge commits visible in `git log --merges`
- Each task branch has own files
- workspace.manifest.json tracks all files
- Python files have valid syntax
- React components have imports/exports

### ✗ Red Flags
- Master only has initial commit (branches not merged)
- No merge commits (QA-gated integration not working)
- Syntax errors in generated code
- Missing directories (backend/, src/, migrations/)
- Empty workspace.manifest.json

---

## Create New Test Project

To test the FULL QA-gated pipeline:

```powershell
# 1. Restart services
.\stop.ps1
.\start.ps1 -SkipInfra

# 2. Create new project via your API/UI with simple tasks

# 3. Monitor logs
Get-Content logs/backend-agent.log -Wait -Tail 50
# Look for: "dev_branch_merged_after_qa_pass"

# 4. Once complete, validate
cd "workspaces/<new-project-id>"
git log --oneline --graph --all
```

---

## Quick Validation Checklist

```bash
# Run these 5 commands for quick validation:

cd "workspaces/00a8890f-2f0c-4e58-90dc-4c61fefbcc7d"

# 1. File count
find . -type f -not -path "./.git/*" | wc -l
# Expected: 20+

# 2. Commit count
git rev-list --count master
# Expected: 15+

# 3. Merge count
git log --oneline --merges | wc -l
# Expected: 10+

# 4. Branch count
git branch | grep "agent/" | wc -l
# Expected: 20+

# 5. Python syntax
python -m py_compile backend/*.py && echo "✓ Syntax OK"
```

---

## Troubleshooting

### Issue: No files on master
```bash
# Check if branches exist
git branch | grep "agent/"

# Manually merge
python merge_all_branches.py 00a8890f-2f0c-4e58-90dc-4c61fefbcc7d
```

### Issue: Merge conflicts
```bash
# Check conflict state
git status

# Abort merge
git merge --abort

# Reset to clean state
git reset --hard HEAD
```

### Issue: Python syntax errors
```bash
# Find problematic files
for f in backend/*.py; do
    python -m py_compile "$f" || echo "ERROR: $f"
done
```

---

## Files to Inspect

### Key Generated Files
1. `backend/main.py` - FastAPI routes
2. `backend/models.py` - Database models
3. `backend/database.py` - DB connection
4. `src/components/NotesList.tsx` - React list component
5. `migrations/db/migrations/versions/*.py` - Schema migrations
6. `workspace.manifest.json` - File tracking metadata

### Test Files (QA Agent)
7. `tests/test_*.py` - Generated tests (if QA ran)
