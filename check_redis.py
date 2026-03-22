"""Quick script to check Redis streams and tasks."""
import asyncio
import redis.asyncio as aioredis


async def main():
    r = await aioredis.from_url("redis://localhost:6379", decode_responses=True)

    print("=== Redis Streams ===")
    # Check agent streams
    for stream in ["stream:backend_agent", "stream:frontend_agent", "stream:database_agent", "stream:qa_agent"]:
        try:
            length = await r.xlen(stream)
            print(f"{stream}: {length} messages")

            # Show first few messages
            if length > 0:
                messages = await r.xrange(stream, count=3)
                for msg_id, data in messages:
                    print(f"  - {msg_id}: task_id={data.get('id', 'N/A')}, title={data.get('title', 'N/A')[:50]}")
        except Exception as e:
            print(f"{stream}: Error - {e}")

    print("\n=== Orchestrator Stream ===")
    try:
        length = await r.xlen("stream:orchestrator")
        print(f"stream:orchestrator: {length} messages")
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== Task Streams (per project) ===")
    # Find all task streams
    task_keys = []
    cursor = 0
    while True:
        cursor, keys = await r.scan(cursor, match="tasks:*", count=100)
        task_keys.extend(keys)
        if cursor == 0:
            break

    if task_keys:
        for key in task_keys:
            length = await r.xlen(key)
            print(f"{key}: {length} messages")
    else:
        print("No task streams found")

    await r.close()


if __name__ == "__main__":
    asyncio.run(main())
