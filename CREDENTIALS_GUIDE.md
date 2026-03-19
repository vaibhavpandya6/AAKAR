# Credentials & Environment Variables in Sandbox

## The Problem
Generated code often needs credentials (database passwords, API keys) to run and test, but:
- ❌ Can't store them in workspace (committed to git)
- ❌ Can't hardcode them (security risk)
- ❌ Can't expose them in logs

## The Solution: Secure Environment Injection

### Architecture
```
Project Root/
├── workspaces/
│   └── proj-123/              # Code files (git-tracked, read-only in sandbox)
│       ├── server.js
│       └── ...
├── secure_env/                # Credentials (NOT tracked, isolated)
│   ├── proj-123.env           # Real credentials for proj-123
│   └── .env.template          # Template for new projects
└── tools/
    └── docker_executor.py     # Injects env vars into sandbox
```

### How It Works
1. **Credentials stored separately**: `secure_env/{project_id}.env` (outside workspace)
2. **Auto-loaded by sandbox**: `load_project_env(project_id)` reads .env file
3. **Injected at runtime**: Docker `-e KEY=VALUE` flags pass env vars to container
4. **Never committed**: `secure_env/` is in `.gitignore`

## Setup Instructions

### 1. Create Secure Environment for a Project

**Linux/Mac:**
```bash
./setup_secure_env.sh proj-123
```

**Windows:**
```bat
setup_secure_env.bat proj-123
```

This creates `secure_env/proj-123.env` with restrictive permissions.

### 2. Edit Credentials
```bash
# Edit: secure_env/proj-123.env
DATABASE_URL=postgresql://user:pass@localhost:5432/dbname
API_KEY=sk-1234567890abcdef
REDIS_URL=redis://localhost:6379
STRIPE_API_KEY=sk_test_...
```

### 3. Use in Code
Generated code can now access environment variables:

**Node.js:**
```javascript
const dbUrl = process.env.DATABASE_URL;
const apiKey = process.env.API_KEY;
```

**Python:**
```python
import os
db_url = os.getenv('DATABASE_URL')
api_key = os.getenv('API_KEY')
```

## Usage Examples

### Auto-load credentials from .env file
```python
sandbox = DockerSandbox()
result = await sandbox.run(
    project_id="proj-123",
    task_id="task-456",
    command="node server.js",
    # Automatically loads secure_env/proj-123.env
)
```

### Override specific credentials
```python
result = await sandbox.run(
    project_id="proj-123",
    task_id="task-456",
    command="python app.py",
    env_vars={"API_KEY": "test-key-override"},  # Overrides .env value
)
```

### Use mock credentials for tests
```python
result = await sandbox.run(
    project_id="proj-123",
    task_id="task-456",
    command="pytest tests/",
    env_vars={
        "DATABASE_URL": "sqlite:///:memory:",
        "API_KEY": "mock-key",
    },
    load_env_file=False,  # Don't load real credentials
)
```

## Security Features

✅ **Isolated storage**: Credentials outside workspace (not in git)
✅ **Restrictive permissions**: `chmod 600` on .env files (owner-only)
✅ **Read-only injection**: Env vars passed to container, not written to disk
✅ **No logging**: Credentials not logged (check `struct_logger` calls)
✅ **Ephemeral containers**: Containers auto-destroyed after execution

## Best Practices

1. **Never commit .env files**: Always in `.gitignore`
2. **Use different credentials per environment**: `proj-123-dev.env`, `proj-123-prod.env`
3. **Rotate credentials regularly**: Update .env files when leaked
4. **Audit access**: Check who can read `secure_env/` directory
5. **Use least privilege**: Only grant necessary permissions to credentials

## Troubleshooting

### Code can't access environment variables
- Check if `secure_env/{project_id}.env` exists
- Verify file permissions: `ls -la secure_env/`
- Check sandbox logs for "Failed to load .env"

### Wrong credentials loaded
- Check precedence: explicit `env_vars` > `.env file` > defaults
- Verify project_id matches filename: `proj-123` → `secure_env/proj-123.env`

### Credentials exposed in logs
- Check `structlog` configuration (should redact secrets)
- Never log `env_vars` dictionary directly
- Use `**redacted**` placeholders in logs
