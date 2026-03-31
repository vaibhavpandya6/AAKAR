"""Startup script to check services and start the API server.

This script:
1. Checks if Redis and PostgreSQL are accessible
2. Validates environment variables
3. Runs database migrations
4. Starts the API server
"""

import asyncio
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def check_redis():
    """Check if Redis is accessible."""
    try:
        import redis
        from config.settings import settings

        # Parse Redis URL
        url = settings.redis_url
        print(f"🔍 Checking Redis at {url}...")

        r = redis.from_url(url)
        r.ping()
        print("   ✓ Redis is running")
        return True
    except Exception as e:
        print(f"   ✗ Redis error: {e}")
        print("\n   Start Redis with:")
        print("   docker run -d -p 6379:6379 redis:latest")
        return False


async def check_postgres():
    """Check if PostgreSQL is accessible."""
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from config.settings import settings

        url = settings.postgres_url
        print(f"🔍 Checking PostgreSQL...")

        engine = create_async_engine(url, echo=False)
        async with engine.connect() as conn:
            await conn.execute("SELECT 1")
        await engine.dispose()

        print("   ✓ PostgreSQL is running")
        return True
    except Exception as e:
        print(f"   ✗ PostgreSQL error: {e}")
        print("\n   Start PostgreSQL with:")
        print("   docker run -d -p 5432:5432 \\")
        print("     -e POSTGRES_USER=user \\")
        print("     -e POSTGRES_PASSWORD=password \\")
        print("     -e POSTGRES_DB=aidevplatform \\")
        print("     postgres:15")
        return False


def check_env():
    """Check if required environment variables are set."""
    print("🔍 Checking environment variables...")

    from config.settings import settings

    required = {
        "NVIDIA_API_KEY": settings.nvidia_api_key,
        "GROQ_API_KEY": settings.groq_api_key,
        "POSTGRES_URL": settings.postgres_url,
        "REDIS_URL": settings.redis_url,
    }

    missing = []
    for key, value in required.items():
        if not value or value.startswith("gsk-placeholder") or value == "":
            missing.append(key)
            print(f"   ✗ {key} not set")
        else:
            # Show first few chars for verification
            display_value = value[:20] + "..." if len(value) > 20 else value
            print(f"   ✓ {key} = {display_value}")

    if missing:
        print(f"\n   Missing: {', '.join(missing)}")
        print("   Set them in .env file")
        return False

    return True


def run_migrations():
    """Run database migrations."""
    print("🔍 Running database migrations...")

    import subprocess

    try:
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            check=True
        )
        print("   ✓ Migrations complete")
        return True
    except subprocess.CalledProcessError as e:
        print(f"   ✗ Migration failed: {e.stderr}")
        return False
    except FileNotFoundError:
        print("   ✗ Alembic not found - install with: pip install alembic")
        return False


def start_server():
    """Start the Uvicorn server."""
    print("\n🚀 Starting API server...")
    print("   Server will run at: http://localhost:8000")
    print("   API docs available at: http://localhost:8000/docs")
    print("   Press CTRL+C to stop\n")

    import subprocess

    try:
        subprocess.run(
            ["uvicorn", "api.main:app", "--reload", "--port", "8000"],
            check=True
        )
    except KeyboardInterrupt:
        print("\n\n✓ Server stopped")
    except subprocess.CalledProcessError as e:
        print(f"\n✗ Server failed: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("✗ Uvicorn not found - install with: pip install uvicorn")
        sys.exit(1)


async def main():
    """Run all startup checks and start server."""
    print("=" * 70)
    print("AAKAR STARTUP - BRD-TO-WBS PIPELINE")
    print("=" * 70)
    print()

    # Check environment
    if not check_env():
        print("\n❌ Environment check failed")
        sys.exit(1)

    print()

    # Check Redis
    redis_ok = check_redis()
    print()

    # Check PostgreSQL
    postgres_ok = await check_postgres()
    print()

    if not redis_ok or not postgres_ok:
        print("❌ Service checks failed")
        print("\nPlease start the required services and try again.")
        sys.exit(1)

    # Run migrations
    migrations_ok = run_migrations()
    print()

    if not migrations_ok:
        print("❌ Migration check failed")
        print("\nYou can try running manually: alembic upgrade head")

        # Ask if they want to continue anyway
        response = input("\nContinue anyway? (y/n): ").strip().lower()
        if response != 'y':
            sys.exit(1)

    print("✓ All checks passed!")
    print()

    # Start server
    start_server()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠ Startup interrupted")
        sys.exit(0)
