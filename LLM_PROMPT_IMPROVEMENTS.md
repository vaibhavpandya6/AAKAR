# LLM Prompt Improvements - Summary

## Changes Made to Prevent Code Quality Issues

### 1. Backend Agent Prompts (agents/backend_agent/prompts.py)

#### Enhanced SYSTEM_PROMPT (Lines 5-75)
**Added explicit async/await rules:**

```python
3. Async/await rules - CRITICAL:
   - ALL functions that use 'await' MUST be declared as 'async def', not 'def'
   - ALL route handlers (@router.get, @router.post, etc.) MUST be 'async def'
   - Database dependencies (Depends(get_db)) require 'async def'
   - NEVER use 'await' inside a 'def' function - this is a syntax error
```

**Added syntax validation checklist:**
```
SYNTAX VALIDATION CHECKLIST:
Before generating code, verify:
✓ Every function with 'await' is declared as 'async def'
✓ Every route handler (@router.*) is 'async def'
✓ Every function with AsyncSession parameter is 'async def'
✓ No 'await' statements inside regular 'def' functions
```

#### Enhanced TASK_PROMPT (Lines 60-98)
**Added requirement #4 with examples:**

```python
4. CRITICAL - Async syntax rules:
   - ALL route handlers MUST be declared as 'async def', never 'def'
   - ALL functions that call 'await' MUST be declared as 'async def'
   - Database operations with AsyncSession always require 'async def'
   - NEVER write: def my_handler(...): await ...  (syntax error!)
   - ALWAYS write: async def my_handler(...): await ...
```

**Added example pattern:**
```python
EXAMPLE CORRECT PATTERN:
@router.post("/notes", response_model=NoteResponse)
async def create_note(  # ← MUST be 'async def'
    request: CreateNoteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    note = Note(title=request.title, content=request.content, user_id=user.id)
    db.add(note)
    await db.commit()  # ← 'await' requires 'async def'
    return note
```

### 2. Backend Agent Validation (agents/backend_agent/agent.py)

#### Added Pre-Commit Syntax Check (Lines 144-167)
After files are written, validate Python syntax before committing:

```python
# ── 6.5. Validate Python syntax ───────────────────────────────
syntax_errors = await self._validate_python_syntax(
    project_id=project_id,
    files=files_written,
)

if syntax_errors:
    await self.log.aerror(
        "python_syntax_errors_detected",
        task_id=task_id,
        errors=syntax_errors,
    )
```

#### Added Validation Helper Method (Lines 222-277)
New method `_validate_python_syntax()`:
- Compiles Python files to check syntax
- Detects `await` in non-async functions
- Returns detailed error info (file, line, snippet)
- Logs warnings but allows commit (QA will catch)

### 3. Git Manager Improvements (workspace_manager/git_manager.py)

#### Auto-Resolve Manifest Conflicts (Lines 357-427)
Added `_try_auto_resolve_manifest()` method:
- Detects workspace.manifest.json conflicts
- Merges JSON file dictionaries from both branches
- Auto-commits if only conflict is manifest
- Prevents manual conflict resolution

#### Fixed Merge Logic (Lines 296-329)
- Uses `git merge` command directly (better conflict handling)
- Detects "already up to date" vs conflict cases
- Aborts failed merges to keep repo clean
- Force checkout to ensure clean state

### 4. QA Agent Integration (agents/qa_agent/agent.py)

#### QA-Gated Auto-Merge (Lines 474-488)
When tests pass, automatically merge dev branch to main:
```python
if test_result.all_passed:
    original_task_id = self._extract_original_task_id(task_id)
    merged = await self._merge_dev_branch_to_main(
        project_id=project_id,
        original_task_id=original_task_id,
        qa_task_id=task_id,
    )
```

#### Helper Methods (Lines 134-208)
- `_extract_original_task_id()`: Parse QA task ID → dev task ID
- `_merge_dev_branch_to_main()`: Find and merge dev branch after QA

---

## Impact on Code Quality

### Before Changes:
- LLM generated `def` functions with `await` statements (syntax error)
- No validation before commit
- No clear examples in prompts
- Tasks couldn't be merged due to conflicts

### After Changes:
- ✅ Explicit async/await rules with examples
- ✅ Syntax validation checklist in prompt
- ✅ Pre-commit validation catches errors early
- ✅ Auto-merge after QA tests pass
- ✅ Manifest conflicts auto-resolved
- ✅ Detailed error logging for debugging

---

## Testing the Improvements

### 1. Restart Services with New Prompts
```powershell
.\stop.ps1
.\start.ps1 -SkipInfra
```

### 2. Create Test Project
Create a simple project with backend tasks:
- Task: "Create user registration endpoint"
- Task: "Create user login endpoint"

### 3. Monitor Logs for Improvements

**Look for:**
```json
// No syntax errors (improved prompts working)
{"event": "file_written", "file_path": "backend/auth.py"}

// Validation passes
(no "python_syntax_errors_detected" events)

// QA tests pass
{"event": "qa_task_all_passed"}

// Auto-merge happens
{"event": "dev_branch_merged_after_qa_pass"}
```

**Old behavior (should not see):**
```json
{"event": "python_syntax_errors_detected", "errors": [...]}
{"error": "'await' outside async function"}
```

### 4. Validate Generated Code
```bash
cd "workspaces/{new-project-id}"
git checkout master

# Check syntax
python -m py_compile backend/*.py
# Should complete without errors

# Inspect code
cat backend/auth.py
# All route handlers should be 'async def'
```

---

## Additional Improvements

### For Other Agent Types:

**Frontend Agent:**
- Already has good prompts (React functional components)
- No async/await issues in client-side code

**Database Agent:**
- Uses Alembic migrations (synchronous)
- No async/await concerns

**QA Agent:**
- Generates pytest test files
- Should also follow async patterns for async endpoints

### Future Enhancements:
1. **Linter integration** - Run ruff/black before commit
2. **Type checking** - Run mypy on generated Python
3. **Import validation** - Check all imports are available
4. **Retry on syntax error** - Auto-regenerate if validation fails
5. **Few-shot examples** - Include correct code samples in prompts

---

## Monitoring Quality Improvements

Track these metrics over time:

```sql
-- Syntax error rate
SELECT COUNT(*) FROM tasks
WHERE logs LIKE '%python_syntax_errors_detected%'
GROUP BY DATE(created_at);

-- QA pass rate
SELECT
  COUNT(*) FILTER (WHERE event = 'qa_task_all_passed') AS passed,
  COUNT(*) FILTER (WHERE event = 'qa_task_failed') AS failed
FROM task_events
WHERE task_id LIKE 'qa_task_%';
```

Monitor logs:
```bash
# Check for syntax errors
grep "python_syntax_errors_detected" logs/backend-agent.log

# Check QA merge rate
grep "dev_branch_merged_after_qa_pass" logs/qa-agent.log
```

---

## Expected Outcomes

### Immediate (Next Project):
- ✅ 0 Python syntax errors in generated code
- ✅ All route handlers properly declared as `async def`
- ✅ Validation logs show clean files
- ✅ Auto-merge works on first try

### Medium Term (After 10 Projects):
- ✅ 95%+ syntax-error-free generation
- ✅ QA pass rate improves (fewer bugs)
- ✅ Faster development cycle (no manual fixes)
- ✅ Main branch always in working state

### Long Term:
- ✅ LLM learns from feedback (RAG + long-term memory)
- ✅ Fewer common mistakes over time
- ✅ Code quality improves as context grows
- ✅ Fully automated SDLC pipeline

---

## Files Modified

1. **agents/backend_agent/prompts.py**
   - Enhanced SYSTEM_PROMPT with async/await rules
   - Added syntax validation checklist
   - Added correct code example

2. **agents/backend_agent/agent.py**
   - Added `_validate_python_syntax()` method
   - Added pre-commit validation step
   - Logs syntax errors for debugging

3. **agents/qa_agent/agent.py**
   - Added QA-gated auto-merge
   - Added `_extract_original_task_id()` helper
   - Added `_merge_dev_branch_to_main()` helper

4. **workspace_manager/git_manager.py**
   - Fixed merge conflict handling
   - Added `_try_auto_resolve_manifest()` for automatic conflict resolution
   - Improved checkout and abort logic

---

## Next Steps

1. **Test immediately:**
   ```powershell
   .\stop.ps1
   .\start.ps1 -SkipInfra
   # Create new project and monitor for syntax errors
   ```

2. **Validate improvements:**
   ```bash
   python test_sdlc_pipeline.py {new-project-id}
   # Should show 0 syntax errors
   ```

3. **Monitor over time:**
   - Track syntax error rate in logs
   - Compare QA pass rates
   - Measure reduction in manual fixes

The improved prompts should significantly reduce syntax errors in future projects!
