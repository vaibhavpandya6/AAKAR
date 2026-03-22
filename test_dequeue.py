"""Test if task dequeue is working."""
import asyncio
from task_system.task_queue import TaskQueue


async def main():
    queue = TaskQueue()

    print("Attempting to dequeue from stream:backend_agent...")
    print("(this will block for 3 seconds if no tasks)")

    task = await queue.dequeue(
        agent_name="test-agent",
        stream_key="stream:backend_agent",
        block_ms=3000,
    )

    if task:
        print(f"\n✓ Got task!")
        print(f"  Task ID: {task.get('id')}")
        print(f"  Title: {task.get('title')}")
        print(f"  Redis ID: {task.get('_redis_id')}")
        print(f"  Project ID: {task.get('project_id')}")
    else:
        print("\n✗ No task returned (queue empty or timeout)")


if __name__ == "__main__":
    asyncio.run(main())
