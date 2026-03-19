# Sandbox Usage Guide

## Overview
The Docker sandbox provides safe execution of untrusted code with two methods:

### 1. `run()` - For pre-installed packages
Use when code only needs packages already in the Dockerfile:
- **Node.js**: express, axios, lodash, moment, dotenv
- **Python**: requests, flask, pytest
- **System tools**: git, curl, wget, bash, jq

**Security**: No network, read-only filesystem (except /tmp)

```python
sandbox = DockerSandbox()
result = await sandbox.run(
    project_id="proj-123",
    task_id="task-456",
    command="node server.js",
    env_vars={"PORT": "3000"},  # Optional: override specific vars
    load_env_file=True  # Default: auto-load from secure .env
)
```

### 2. `run_with_packages()` - For additional packages
Use when code needs packages NOT pre-installed:

**Security**: Network enabled ONLY during package install, then disabled

```python
sandbox = DockerSandbox()
result = await sandbox.run_with_packages(
    project_id="proj-123",
    task_id="task-456",
    command="node app.js",
    package_manager="npm",  # or "pip"
    packages=["fastify", "ws", "redis"],
    env_vars={"API_KEY": "secret"},  # Optional
    load_env_file=True  # Default: auto-load from secure .env
)
```

**Auto-detection**: If `package_manager` is `None`, it guesses based on package names.

## Environment Variables & Credentials

### Secure .env File Location
Credentials are stored OUTSIDE the workspace to prevent git commits:

```
workspaces/
  ├── proj-123/          # Code workspace (read-only mount)
  └── ...
secure_env/              # SEPARATE directory for credentials
  ├── proj-123.env       # Environment variables for proj-123
  └── ...
```

### Setting Up Project Credentials

**1. Create secure_env directory** (one-time setup):
```bash
mkdir -p "$(dirname $WORKSPACE_BASE_PATH)/secure_env"
```

**2. Create project-specific .env file**:
```bash
# Example: secure_env/proj-123.env
DATABASE_URL=postgresql://user:pass@localhost:5432/mydb
API_KEY=sk-1234567890abcdef
REDIS_URL=redis://localhost:6379
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
```

**3. Secure the directory** (important!):
```bash
chmod 700 secure_env
chmod 600 secure_env/*.env
```

### Environment Variable Precedence
1. **Explicit `env_vars` parameter** (highest priority)
2. **Secure .env file** (`secure_env/{project_id}.env`)
3. **Default environment** (lowest priority)

### Disabling .env Auto-loading
For tests that should use mock credentials:
```python
result = await sandbox.run(
    project_id, task_id,
    "pytest tests/",
    env_vars={"DATABASE_URL": "sqlite:///:memory:"},
    load_env_file=False  # Don't load real credentials
)
```

## Return Value (both methods)
```python
{
    "stdout": "...",
    "stderr": "...",
    "exit_code": 0,
    "duration_ms": 1234,
    "timed_out": False
}
```

## When to Use Which Method

| Scenario | Method | Why |
|----------|--------|-----|
| Express web server | `run()` | express pre-installed |
| FastAPI app | `run()` | requests, flask pre-installed |
| React app (Next.js) | `run_with_packages()` | needs react, next |
| Database client | `run_with_packages()` | needs pg, mysql2, pymongo |
| Testing with Jest | `run_with_packages()` | jest not pre-installed |
| Custom npm package | `run_with_packages()` | obviously not pre-installed |

## Performance Note
`run_with_packages()` is **~10-30s slower** due to npm/pip installation.
Use `run()` when possible for faster execution.

## Security Best Practices

1. **Never commit .env files to git**: They're stored outside workspace
2. **Use restrictive permissions**: `chmod 600` on all .env files
3. **Rotate credentials regularly**: Update .env files as needed
4. **Use different creds per environment**: dev vs. prod credentials
5. **Audit .env access**: Check who can read secure_env directory
