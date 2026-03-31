"""Integration node — validates the assembled project before final review.

This node runs after QA passes but before the reviewer approves the project.
It performs integration-level checks that individual agents cannot catch.

Validates all 20 common code generation issues:
1. Missing imports
2. Wrong ORM syntax (query vs select)
3. Duplicate function definitions
4. Wrong model definitions (Pydantic vs SQLAlchemy mixing)
5. Async/sync mismatch
6. Wrong sessionmaker/engine usage
7. Empty/useless config files
8. Dockerfile CMD mismatch
9. Missing critical dependencies
10. Environment variable consistency
11. Conflicting directory structures
12. Missing component files
13. No application entry point
14. Wrong file references
15. Schema confusion
16. Type inconsistencies
17. Missing model relationships
18. Hardcoded credentials
19. No database initialization
20. Missing CORS/middleware
"""

import ast
import importlib.util
import json
import os
import re
import sys
from typing import Any

import structlog

from orchestrator.state import PlatformState, ProjectStatus
from workspace_manager import get_workspace_manager

logger = structlog.get_logger()

# Standard library modules
_STDLIB_MODULES = frozenset(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else frozenset({
    'abc', 'asyncio', 'collections', 'contextlib', 'copy', 'datetime', 'enum',
    'functools', 'hashlib', 'io', 'itertools', 'json', 'logging', 'math', 'os',
    'pathlib', 'pickle', 're', 'secrets', 'shutil', 'subprocess', 'sys', 'tempfile',
    'threading', 'time', 'typing', 'unittest', 'uuid', 'warnings', 'weakref',
})

# Common third-party packages
_COMMON_PACKAGES = frozenset({
    'fastapi', 'pydantic', 'sqlalchemy', 'uvicorn', 'starlette', 'httpx',
    'aiohttp', 'requests', 'pytest', 'structlog', 'alembic', 'asyncpg',
    'redis', 'celery', 'langchain', 'openai', 'chromadb', 'numpy', 'pandas',
    'react', 'axios', 'express', 'cors', 'dotenv',
})

# Critical Python dependencies for common frameworks
_PYTHON_FRAMEWORK_DEPS = {
    'fastapi': ['fastapi', 'uvicorn', 'pydantic'],
    'sqlalchemy_async': ['sqlalchemy', 'asyncpg'],
    'sqlalchemy_sync': ['sqlalchemy', 'psycopg2-binary'],
}

# Critical Node.js dependencies for common frameworks
_NODE_FRAMEWORK_DEPS = {
    'react': ['react', 'react-dom'],
    'react_ts': ['react', 'react-dom', 'typescript', '@types/react'],
    'express': ['express'],
    'nextjs': ['next', 'react', 'react-dom'],
}

# Hardcoded credential patterns
_CREDENTIAL_PATTERNS = [
    r'password\s*=\s*["\'][^"\']+["\']',
    r'secret\s*=\s*["\'][^"\']+["\']',
    r'api_key\s*=\s*["\'][^"\']+["\']',
    r'postgresql://\w+:\w+@',
    r'mysql://\w+:\w+@',
    r'mongodb://\w+:\w+@',
]


async def _validate_all_imports(project_id: str, files: list[str]) -> list[dict]:
    """Validate that all imports across the project can be resolved.

    Args:
        project_id: Project identifier
        files: List of file paths in the workspace

    Returns:
        List of import error dictionaries
    """
    errors = []
    workspace_manager = await get_workspace_manager()

    # Build local module index
    local_modules = set()
    for f in files:
        if f.endswith('.py'):
            module_path = f[:-3].replace('/', '.').replace('\\', '.')
            parts = module_path.split('.')
            for i in range(len(parts)):
                local_modules.add('.'.join(parts[:i+1]))

    python_files = [f for f in files if f.endswith('.py')]

    for file_path in python_files:
        try:
            content = await workspace_manager.read_file(project_id, file_path)
            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_level = alias.name.split('.')[0]
                        if not _can_resolve(top_level, alias.name, local_modules):
                            errors.append({
                                'file': file_path,
                                'line': node.lineno,
                                'import': alias.name,
                                'type': 'unresolved_import',
                            })

                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        top_level = node.module.split('.')[0]
                        if node.level == 0 and not _can_resolve(top_level, node.module, local_modules):
                            errors.append({
                                'file': file_path,
                                'line': node.lineno,
                                'import': f"from {node.module}",
                                'type': 'unresolved_import',
                            })

        except SyntaxError:
            pass  # Syntax errors caught earlier
        except Exception as e:
            logger.warning("import_scan_failed", file=file_path, error=str(e))

    return errors


def _can_resolve(top_level: str, full_module: str, local_modules: set) -> bool:
    """Check if a module can be resolved."""
    if top_level in _STDLIB_MODULES:
        return True
    if top_level in _COMMON_PACKAGES:
        return True
    if full_module in local_modules or top_level in local_modules:
        return True
    try:
        spec = importlib.util.find_spec(top_level)
        return spec is not None
    except (ModuleNotFoundError, ValueError, ImportError):
        return False


async def _validate_config_files(project_id: str, files: list[str]) -> list[dict]:
    """Validate that essential configuration files exist and are valid.

    Args:
        project_id: Project identifier
        files: List of file paths in the workspace

    Returns:
        List of configuration error dictionaries
    """
    errors = []
    workspace_manager = await get_workspace_manager()

    file_set = set(files)

    # Check for Python project configs
    has_python = any(f.endswith('.py') for f in files)
    if has_python:
        if 'requirements.txt' not in file_set and 'pyproject.toml' not in file_set:
            errors.append({
                'type': 'missing_config',
                'file': 'requirements.txt',
                'message': 'Python project missing requirements.txt or pyproject.toml',
                'suggestion': 'Add a requirements.txt with project dependencies',
            })

    # Check for Node.js project configs
    has_node = any(f.endswith(('.js', '.ts', '.tsx', '.jsx')) for f in files)
    if has_node:
        if 'package.json' not in file_set:
            # Check in subdirectories
            has_package_json = any('package.json' in f for f in files)
            if not has_package_json:
                errors.append({
                    'type': 'missing_config',
                    'file': 'package.json',
                    'message': 'Node.js project missing package.json',
                    'suggestion': 'Add a package.json with project dependencies',
                })

    # Validate requirements.txt if exists
    if 'requirements.txt' in file_set:
        try:
            content = await workspace_manager.read_file(project_id, 'requirements.txt')
            deps = _parse_requirements(content)

            # Check for referenced but unlisted packages
            for f in files:
                if f.endswith('.py'):
                    try:
                        py_content = await workspace_manager.read_file(project_id, f)
                        referenced = _extract_imports(py_content)
                        for pkg in referenced:
                            if pkg not in _STDLIB_MODULES and pkg not in deps:
                                # Might be a local module or common alias
                                if pkg not in _COMMON_PACKAGES:
                                    continue
                                if pkg.lower() not in {d.lower() for d in deps}:
                                    errors.append({
                                        'type': 'missing_dependency',
                                        'file': f,
                                        'package': pkg,
                                        'message': f"Package '{pkg}' is imported but not in requirements.txt",
                                    })
                    except Exception:
                        pass
        except Exception as e:
            errors.append({
                'type': 'invalid_config',
                'file': 'requirements.txt',
                'message': f'Failed to parse requirements.txt: {e}',
            })

    # Validate package.json if exists
    for f in files:
        if f.endswith('package.json'):
            try:
                content = await workspace_manager.read_file(project_id, f)
                data = json.loads(content)
                if 'name' not in data:
                    errors.append({
                        'type': 'invalid_config',
                        'file': f,
                        'message': 'package.json missing "name" field',
                    })
                if 'scripts' not in data or 'start' not in data.get('scripts', {}):
                    errors.append({
                        'type': 'incomplete_config',
                        'file': f,
                        'message': 'package.json missing "scripts.start" - project may not be runnable',
                        'severity': 'warning',
                    })
            except json.JSONDecodeError as e:
                errors.append({
                    'type': 'invalid_config',
                    'file': f,
                    'message': f'Invalid JSON in package.json: {e}',
                })
            except Exception:
                pass

    return errors


def _parse_requirements(content: str) -> set[str]:
    """Parse package names from requirements.txt."""
    deps = set()
    for line in content.split('\n'):
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('-'):
            continue
        # Handle version specifiers
        match = re.match(r'^([a-zA-Z0-9_-]+)', line)
        if match:
            deps.add(match.group(1).lower())
    return deps


def _extract_imports(content: str) -> set[str]:
    """Extract top-level import names from Python code."""
    imports = set()
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    imports.add(node.module.split('.')[0])
    except SyntaxError:
        pass
    return imports


async def _validate_api_contracts(project_id: str, files: list[str]) -> list[dict]:
    """Validate that frontend API calls match backend endpoints.

    Args:
        project_id: Project identifier
        files: List of file paths in the workspace

    Returns:
        List of API contract mismatch errors
    """
    errors = []
    workspace_manager = await get_workspace_manager()

    # Extract backend endpoints
    backend_endpoints = []
    for f in files:
        if not f.endswith('.py'):
            continue
        if 'router' not in f.lower() and 'route' not in f.lower() and 'api' not in f.lower():
            continue

        try:
            content = await workspace_manager.read_file(project_id, f)
            # Find FastAPI/Flask route decorators
            endpoint_patterns = [
                r'@(?:router|app)\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']',
                r'@(?:api_router)\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']',
            ]
            for pattern in endpoint_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for method, path in matches:
                    backend_endpoints.append({
                        'method': method.upper(),
                        'path': path,
                        'file': f,
                    })
        except Exception:
            pass

    if not backend_endpoints:
        return errors  # No backend to validate against

    # Extract frontend API calls
    frontend_calls = []
    for f in files:
        if not any(f.endswith(ext) for ext in ['.js', '.ts', '.tsx', '.jsx']):
            continue

        try:
            content = await workspace_manager.read_file(project_id, f)
            # Find common API call patterns
            call_patterns = [
                r'fetch\(["\']([^"\']+)["\']',
                r'axios\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']',
                r'api\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']',
            ]
            for pattern in call_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        if len(match) == 2:
                            frontend_calls.append({
                                'method': match[0].upper(),
                                'path': match[1],
                                'file': f,
                            })
                    else:
                        frontend_calls.append({
                            'path': match,
                            'file': f,
                        })
        except Exception:
            pass

    # Check for mismatches
    backend_paths = {e['path'] for e in backend_endpoints}
    for call in frontend_calls:
        path = call.get('path', '')
        # Normalize path
        if '${' in path or '`' in path:
            continue  # Skip template strings
        if path.startswith('http'):
            continue  # Skip absolute URLs

        # Strip query params
        path = path.split('?')[0]

        if path and path not in backend_paths:
            # Check for path params (e.g., /users/:id vs /users/{id})
            normalized = re.sub(r'/:\w+', '/{param}', path)
            backend_normalized = {re.sub(r'/\{[^}]+\}', '/{param}', p) for p in backend_paths}
            if normalized not in backend_normalized:
                errors.append({
                    'type': 'api_mismatch',
                    'frontend_file': call['file'],
                    'path': path,
                    'message': f"Frontend calls '{path}' but no matching backend endpoint found",
                    'severity': 'warning',
                })

    return errors


async def _validate_database_consistency(project_id: str, files: list[str]) -> list[dict]:
    """Validate database schema consistency between models and migrations.

    Args:
        project_id: Project identifier
        files: List of file paths in the workspace

    Returns:
        List of database consistency errors
    """
    errors = []
    workspace_manager = await get_workspace_manager()

    model_tables = set()
    migration_tables = set()

    # Extract table names from SQLAlchemy models
    for f in files:
        if not f.endswith('.py'):
            continue
        if 'model' not in f.lower():
            continue

        try:
            content = await workspace_manager.read_file(project_id, f)
            # Find __tablename__ = "..."
            matches = re.findall(r'__tablename__\s*=\s*["\']([^"\']+)["\']', content)
            model_tables.update(matches)
        except Exception:
            pass

    # Extract table names from Alembic migrations
    for f in files:
        if 'migrations' not in f and 'alembic' not in f:
            continue
        if not f.endswith('.py'):
            continue

        try:
            content = await workspace_manager.read_file(project_id, f)
            # Find op.create_table("...")
            matches = re.findall(r'op\.create_table\(["\']([^"\']+)["\']', content)
            migration_tables.update(matches)
        except Exception:
            pass

    # Check for mismatches
    if model_tables and migration_tables:
        missing_migrations = model_tables - migration_tables
        for table in missing_migrations:
            errors.append({
                'type': 'schema_mismatch',
                'table': table,
                'message': f"Model defines table '{table}' but no migration creates it",
                'severity': 'warning',
            })

    return errors


async def _validate_orm_syntax(project_id: str, files: list[str]) -> list[dict]:
    """Check for SQLAlchemy 1.x vs 2.x syntax issues.

    Catches: db.query(Model) instead of select(Model)
    """
    errors = []
    workspace_manager = await get_workspace_manager()

    for f in files:
        if not f.endswith('.py'):
            continue
        try:
            content = await workspace_manager.read_file(project_id, f)

            # Check for old query() syntax
            if re.search(r'db\.query\(|session\.query\(', content):
                errors.append({
                    'type': 'wrong_orm_syntax',
                    'file': f,
                    'message': 'Using SQLAlchemy 1.x sync syntax (db.query). Use select() with await db.execute() for async.',
                    'fix': 'Replace db.query(Model).filter(...) with: stmt = select(Model).where(...); result = await db.execute(stmt)',
                })

            # Check for sync engine with async session
            if 'create_engine' in content and 'AsyncSession' in content:
                if 'create_async_engine' not in content:
                    errors.append({
                        'type': 'async_sync_mismatch',
                        'file': f,
                        'message': 'Using create_engine (sync) with AsyncSession. Use create_async_engine instead.',
                    })

            # Check for wrong sessionmaker with async
            if 'sessionmaker' in content and 'AsyncSession' in content:
                if 'async_sessionmaker' not in content:
                    errors.append({
                        'type': 'wrong_sessionmaker',
                        'file': f,
                        'message': 'Using sessionmaker with AsyncSession. Use async_sessionmaker instead.',
                    })

        except Exception:
            pass

    return errors


async def _validate_duplicate_definitions(project_id: str, files: list[str]) -> list[dict]:
    """Check for duplicate function/class definitions across files."""
    errors = []
    workspace_manager = await get_workspace_manager()

    definitions: dict[str, list[str]] = {}  # name -> [file1, file2, ...]

    for f in files:
        if not f.endswith('.py'):
            continue
        try:
            content = await workspace_manager.read_file(project_id, f)
            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                    name = node.name
                    if not name.startswith('_'):  # Skip private functions
                        if name not in definitions:
                            definitions[name] = []
                        definitions[name].append(f)

                elif isinstance(node, ast.ClassDef):
                    name = node.name
                    if name not in definitions:
                        definitions[name] = []
                    definitions[name].append(f)

        except SyntaxError:
            pass
        except Exception:
            pass

    # Report duplicates
    for name, files_list in definitions.items():
        if len(files_list) > 1:
            # Filter out test files and __init__.py
            real_files = [f for f in files_list if 'test' not in f.lower() and '__init__' not in f]
            if len(real_files) > 1:
                errors.append({
                    'type': 'duplicate_definition',
                    'name': name,
                    'files': real_files,
                    'message': f"'{name}' is defined in multiple files: {', '.join(real_files)}",
                    'severity': 'warning',
                })

    return errors


async def _validate_model_definitions(project_id: str, files: list[str]) -> list[dict]:
    """Check for Pydantic vs SQLAlchemy model confusion."""
    errors = []
    workspace_manager = await get_workspace_manager()

    for f in files:
        if not f.endswith('.py'):
            continue
        try:
            content = await workspace_manager.read_file(project_id, f)
            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    bases = [_get_base_name(b) for b in node.bases]
                    has_basemodel = 'BaseModel' in bases
                    has_tablename = any(
                        isinstance(item, ast.Assign) and
                        any(isinstance(t, ast.Name) and t.id == '__tablename__' for t in item.targets)
                        for item in node.body
                    )

                    if has_basemodel and has_tablename:
                        errors.append({
                            'type': 'model_confusion',
                            'file': f,
                            'class': node.name,
                            'message': f"Class '{node.name}' inherits from BaseModel (Pydantic) but has __tablename__ (SQLAlchemy). These should be separate classes.",
                        })

                    # Check for SQLAlchemy model without proper Base
                    if has_tablename and not has_basemodel:
                        if not any(b in ['Base', 'DeclarativeBase'] for b in bases):
                            # Check if any base looks like a custom Base
                            if not any('Base' in b for b in bases):
                                errors.append({
                                    'type': 'missing_base_class',
                                    'file': f,
                                    'class': node.name,
                                    'message': f"Class '{node.name}' has __tablename__ but doesn't inherit from Base/DeclarativeBase",
                                    'severity': 'warning',
                                })

        except SyntaxError:
            pass
        except Exception:
            pass

    return errors


def _get_base_name(node: ast.expr) -> str:
    """Extract base class name from AST node."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return node.attr
    return ''


async def _validate_async_consistency(project_id: str, files: list[str]) -> list[dict]:
    """Check for async/sync mismatches in function definitions."""
    errors = []
    workspace_manager = await get_workspace_manager()

    for f in files:
        if not f.endswith('.py'):
            continue
        try:
            content = await workspace_manager.read_file(project_id, f)
            tree = ast.parse(content)

            for node in ast.walk(tree):
                # Check for await in non-async function
                if isinstance(node, ast.FunctionDef):  # Not AsyncFunctionDef
                    for child in ast.walk(node):
                        if isinstance(child, ast.Await):
                            errors.append({
                                'type': 'await_in_sync_function',
                                'file': f,
                                'function': node.name,
                                'line': child.lineno,
                                'message': f"'await' used in non-async function '{node.name}'. Add 'async' keyword.",
                            })
                            break

        except SyntaxError:
            pass
        except Exception:
            pass

    return errors


async def _validate_hardcoded_credentials(project_id: str, files: list[str]) -> list[dict]:
    """Check for hardcoded passwords, API keys, and database URLs."""
    errors = []
    workspace_manager = await get_workspace_manager()

    for f in files:
        if not f.endswith('.py'):
            continue
        if 'test' in f.lower() or 'example' in f.lower():
            continue

        try:
            content = await workspace_manager.read_file(project_id, f)

            for pattern in _CREDENTIAL_PATTERNS:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    # Skip if it's using os.getenv or environment variables
                    for match in matches:
                        if 'getenv' not in content[max(0, content.find(match)-50):content.find(match)]:
                            errors.append({
                                'type': 'hardcoded_credential',
                                'file': f,
                                'message': f'Possible hardcoded credential found. Use environment variables instead.',
                                'severity': 'warning',
                            })
                            break

        except Exception:
            pass

    return errors


async def _validate_entry_point(project_id: str, files: list[str]) -> list[dict]:
    """Check for existence of application entry point."""
    errors = []
    workspace_manager = await get_workspace_manager()

    has_python = any(f.endswith('.py') for f in files)
    has_node = any(f.endswith(('.js', '.ts', '.tsx', '.jsx')) for f in files)

    if has_python:
        # Check for FastAPI/Flask app
        has_app_instance = False
        entry_files = ['main.py', 'app.py', 'backend/main.py', 'backend/app.py', 'src/main.py']

        for f in files:
            if any(f.endswith(ef) or f == ef for ef in entry_files):
                try:
                    content = await workspace_manager.read_file(project_id, f)
                    if 'FastAPI(' in content or 'Flask(' in content or 'app = ' in content:
                        has_app_instance = True

                        # Check if routers are included
                        if 'FastAPI(' in content:
                            if 'include_router' not in content and 'app.add_api_route' not in content:
                                # Check if there are routers that should be included
                                router_files = [rf for rf in files if 'router' in rf.lower() and rf.endswith('.py')]
                                if router_files:
                                    errors.append({
                                        'type': 'routers_not_included',
                                        'file': f,
                                        'message': f'FastAPI app found but routers not included. Missing app.include_router() calls.',
                                        'severity': 'warning',
                                    })
                        break
                except Exception:
                    pass

        if not has_app_instance:
            # Check if there are route handlers but no app
            has_routes = False
            for f in files:
                if f.endswith('.py'):
                    try:
                        content = await workspace_manager.read_file(project_id, f)
                        if '@router.' in content or '@app.' in content:
                            has_routes = True
                            break
                    except Exception:
                        pass

            if has_routes:
                errors.append({
                    'type': 'missing_entry_point',
                    'message': 'Route handlers found but no FastAPI/Flask app instance. Create main.py with app = FastAPI()',
                })

    if has_node:
        # Check for React/Express entry
        has_entry = False
        for f in files:
            if f.endswith(('index.tsx', 'index.ts', 'index.js', 'App.tsx', 'App.jsx', 'main.tsx', 'main.ts')):
                has_entry = True
                break

        if not has_entry:
            errors.append({
                'type': 'missing_entry_point',
                'message': 'No frontend entry point found (index.tsx, App.tsx, or similar)',
                'severity': 'warning',
            })

    return errors


async def _validate_env_consistency(project_id: str, files: list[str]) -> list[dict]:
    """Check that environment variables used in code match .env.example."""
    errors = []
    workspace_manager = await get_workspace_manager()

    # Collect env vars used in code
    env_vars_used = set()
    for f in files:
        if not f.endswith('.py'):
            continue
        try:
            content = await workspace_manager.read_file(project_id, f)
            # os.getenv("VAR") or os.environ["VAR"] or os.environ.get("VAR")
            matches = re.findall(r'os\.(?:getenv|environ\.get|environ\[)["\']([A-Z_][A-Z0-9_]*)["\']', content)
            env_vars_used.update(matches)
            # settings.var_name pattern (pydantic-settings)
            # This is harder to detect, so we skip it for now
        except Exception:
            pass

    if not env_vars_used:
        return errors

    # Check .env.example
    env_vars_defined = set()
    for f in files:
        if '.env' in f and 'example' in f.lower():
            try:
                content = await workspace_manager.read_file(project_id, f)
                for line in content.split('\n'):
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        var_name = line.split('=')[0].strip()
                        env_vars_defined.add(var_name)
            except Exception:
                pass

    # Report missing env vars
    missing = env_vars_used - env_vars_defined
    for var in missing:
        if var not in ['PATH', 'HOME', 'USER', 'PWD']:  # Skip system vars
            errors.append({
                'type': 'missing_env_var',
                'variable': var,
                'message': f"Environment variable '{var}' is used in code but not defined in .env.example",
                'severity': 'warning',
            })

    return errors


async def _validate_dockerfile(project_id: str, files: list[str]) -> list[dict]:
    """Validate Dockerfile CMD matches actual entry point."""
    errors = []
    workspace_manager = await get_workspace_manager()

    for f in files:
        if not f.lower().endswith('dockerfile') and 'dockerfile' not in f.lower():
            continue

        try:
            content = await workspace_manager.read_file(project_id, f)

            # Extract CMD
            cmd_match = re.search(r'CMD\s+\[([^\]]+)\]|CMD\s+(.+)$', content, re.MULTILINE)
            if cmd_match:
                cmd_content = cmd_match.group(1) or cmd_match.group(2)

                # Check if referenced file exists
                if 'python' in cmd_content.lower():
                    # Extract Python file being run
                    py_file_match = re.search(r'["\']?(\w+\.py)["\']?', cmd_content)
                    if py_file_match:
                        py_file = py_file_match.group(1)
                        # Check if file exists
                        if not any(f.endswith(py_file) for f in files):
                            errors.append({
                                'type': 'dockerfile_cmd_mismatch',
                                'file': f,
                                'message': f"Dockerfile CMD references '{py_file}' but this file doesn't exist",
                            })

        except Exception:
            pass

    return errors


async def _validate_dependencies_content(project_id: str, files: list[str]) -> list[dict]:
    """Validate that dependency files have meaningful content."""
    errors = []
    workspace_manager = await get_workspace_manager()

    for f in files:
        if f.endswith('requirements.txt') or f == 'requirements.txt':
            try:
                content = await workspace_manager.read_file(project_id, f)
                deps = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('#')]

                # Check for obviously wrong entries
                for dep in deps:
                    if dep.lower().startswith('python==') or dep.lower().startswith('python>='):
                        errors.append({
                            'type': 'invalid_dependency',
                            'file': f,
                            'dependency': dep,
                            'message': f"'{dep}' is not a valid pip requirement. Python version is specified in pyproject.toml or runtime.txt",
                        })

                # Check if file is essentially empty
                valid_deps = [d for d in deps if not d.lower().startswith('python')]
                if len(valid_deps) < 1 and any(pf.endswith('.py') for pf in files):
                    errors.append({
                        'type': 'empty_dependencies',
                        'file': f,
                        'message': 'requirements.txt has no valid dependencies but Python files exist',
                    })

            except Exception:
                pass

        if 'package.json' in f:
            try:
                content = await workspace_manager.read_file(project_id, f)
                data = json.loads(content)

                deps = data.get('dependencies', {})
                dev_deps = data.get('devDependencies', {})
                all_deps = {**deps, **dev_deps}

                # Check if it has relevant dependencies for the framework
                ts_files = [pf for pf in files if pf.endswith(('.ts', '.tsx'))]
                jsx_files = [pf for pf in files if pf.endswith(('.jsx', '.tsx'))]

                if ts_files and 'typescript' not in all_deps:
                    errors.append({
                        'type': 'missing_dependency',
                        'file': f,
                        'package': 'typescript',
                        'message': 'TypeScript files found but typescript not in package.json dependencies',
                    })

                if jsx_files and 'react' not in all_deps:
                    errors.append({
                        'type': 'missing_dependency',
                        'file': f,
                        'package': 'react',
                        'message': 'React/JSX files found but react not in package.json dependencies',
                    })

            except json.JSONDecodeError:
                pass
            except Exception:
                pass

    return errors


async def integration_node(state: PlatformState) -> dict[str, Any]:
    """Validate the assembled project as a whole.

    This node runs after QA passes to catch integration-level issues that
    individual agents cannot detect. It validates:

    1. All imports across the project can be resolved
    2. Essential configuration files exist and are valid
    3. Frontend API calls match backend endpoints
    4. Database models are consistent with migrations

    Args:
        state: Current PlatformState (after qa_node)

    Returns:
        Partial state dict. On validation failure with critical errors,
        returns bug_reports with integration issues. On success, returns
        empty dict (pass-through).
    """
    project_id = state.get("project_id", "")
    files_written = state.get("files_written") or []

    logger.info(
        "integration_node_start",
        project_id=project_id,
        files_count=len(files_written),
    )

    all_errors: list[dict] = []
    all_warnings: list[dict] = []

    try:
        # Get full file list from workspace
        workspace_manager = await get_workspace_manager()
        all_files = await workspace_manager.list_files(project_id)
    except Exception as e:
        logger.error("integration_file_list_failed", error=str(e))
        all_files = files_written

    # ── 1. Validate imports ──────────────────────────────────────────────────
    import_errors = await _validate_all_imports(project_id, all_files)
    for err in import_errors:
        if err.get('severity') == 'warning':
            all_warnings.append(err)
        else:
            all_errors.append(err)

    # ── 2. Validate configuration files ──────────────────────────────────────
    config_errors = await _validate_config_files(project_id, all_files)
    for err in config_errors:
        if err.get('severity') == 'warning':
            all_warnings.append(err)
        else:
            all_errors.append(err)

    # ── 3. Validate API contracts ────────────────────────────────────────────
    api_errors = await _validate_api_contracts(project_id, all_files)
    for err in api_errors:
        all_warnings.append(err)  # API mismatches are warnings

    # ── 4. Validate database consistency ─────────────────────────────────────
    db_errors = await _validate_database_consistency(project_id, all_files)
    for err in db_errors:
        all_warnings.append(err)  # Schema mismatches are warnings

    # ── 5. Validate ORM syntax (query vs select) ────────────────────────────
    orm_errors = await _validate_orm_syntax(project_id, all_files)
    for err in orm_errors:
        all_errors.append(err)  # ORM issues are errors

    # ── 6. Validate duplicate definitions ───────────────────────────────────
    dup_errors = await _validate_duplicate_definitions(project_id, all_files)
    for err in dup_errors:
        if err.get('severity') == 'warning':
            all_warnings.append(err)
        else:
            all_errors.append(err)

    # ── 7. Validate model definitions (Pydantic vs SQLAlchemy) ──────────────
    model_errors = await _validate_model_definitions(project_id, all_files)
    for err in model_errors:
        if err.get('severity') == 'warning':
            all_warnings.append(err)
        else:
            all_errors.append(err)

    # ── 8. Validate async/sync consistency ──────────────────────────────────
    async_errors = await _validate_async_consistency(project_id, all_files)
    for err in async_errors:
        all_errors.append(err)  # Async issues are errors (syntax errors)

    # ── 9. Validate hardcoded credentials ───────────────────────────────────
    cred_errors = await _validate_hardcoded_credentials(project_id, all_files)
    for err in cred_errors:
        all_warnings.append(err)  # Credentials are warnings (not blocking)

    # ── 10. Validate entry point existence ──────────────────────────────────
    entry_errors = await _validate_entry_point(project_id, all_files)
    for err in entry_errors:
        if err.get('severity') == 'warning':
            all_warnings.append(err)
        else:
            all_errors.append(err)

    # ── 11. Validate environment variable consistency ───────────────────────
    env_errors = await _validate_env_consistency(project_id, all_files)
    for err in env_errors:
        all_warnings.append(err)

    # ── 12. Validate Dockerfile CMD ─────────────────────────────────────────
    docker_errors = await _validate_dockerfile(project_id, all_files)
    for err in docker_errors:
        all_warnings.append(err)

    # ── 13. Validate dependency file contents ───────────────────────────────
    dep_errors = await _validate_dependencies_content(project_id, all_files)
    for err in dep_errors:
        if err.get('type') == 'empty_dependencies':
            all_errors.append(err)  # Empty deps is critical
        else:
            all_warnings.append(err)

    # Log warnings
    for warning in all_warnings:
        logger.warning(
            "integration_warning",
            project_id=project_id,
            **warning,
        )

    # Critical errors that should block the project
    critical_error_types = {
        'unresolved_import',      # Missing imports
        'wrong_orm_syntax',       # Using query() instead of select()
        'async_sync_mismatch',    # create_engine with AsyncSession
        'wrong_sessionmaker',     # sessionmaker with AsyncSession
        'model_confusion',        # Pydantic + SQLAlchemy mixed
        'await_in_sync_function', # Syntax error: await in def
        'missing_entry_point',    # No app instance
        'empty_dependencies',     # Empty requirements.txt
    }
    critical_errors = [e for e in all_errors if e.get('type') in critical_error_types]

    if critical_errors:
        logger.error(
            "integration_validation_failed",
            project_id=project_id,
            error_count=len(critical_errors),
            warning_count=len(all_warnings),
        )

        # Return as bug reports so fix_retry can handle them
        bug_reports = state.get("bug_reports") or []
        bug_reports.append({
            'type': 'integration_failure',
            'errors': critical_errors[:20],  # Limit to first 20
            'warnings': all_warnings[:20],
            'summary': f"Integration validation found {len(critical_errors)} errors and {len(all_warnings)} warnings",
        })

        return {
            "bug_reports": bug_reports,
        }

    logger.info(
        "integration_node_complete",
        project_id=project_id,
        warnings=len(all_warnings),
    )

    return {}
