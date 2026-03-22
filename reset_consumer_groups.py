"""Reset Redis consumer groups to reprocess all messages from beginning."""
import asyncio
import redis.asyncio as aioredis


async def main():
    r = await aioredis.from_url("redis://localhost:6379", decode_responses=True)

    streams = ["stream:backend_agent", "stream:frontend_agent", "stream:database_agent", "stream:qa_agent"]

    for stream in streams:
        print(f"\n=== Resetting {stream} ===")
        try:
            # Delete the existing consumer group
            await r.xgroup_destroy(stream, "workers")
            print(f"  Deleted old consumer group")

            # Recreate from beginning (0 = start of stream)
            await r.xgroup_create(stream, "workers", id="0", mkstream=True)
            print(f"  Created new consumer group from beginning")

            # Check how many messages are available
            length = await r.xlen(stream)
            print(f"  {length} messages ready for processing")

        except Exception as e:
            if "NOGROUP" in str(e):
                # Group doesn't exist, create it
                await r.xgroup_create(stream, "workers", id="0", mkstream=True)
                print(f"  Created consumer group from beginning")
            else:
                print(f"  Error: {e}")

    print("\n=== Done ===")
    print("Restart your workers - they will pick up ALL existing tasks!")

    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
