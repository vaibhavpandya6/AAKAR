"""Clear all messages from Redis streams to remove old malformed data."""
import asyncio
import redis.asyncio as aioredis


async def main():
    print("\n" + "="*60)
    print("  Redis Stream Cleanup - Removing Old Messages")
    print("="*60 + "\n")

    r = await aioredis.from_url("redis://localhost:6379", decode_responses=True)

    # All streams including orchestrator
    streams = [
        "stream:orchestrator",
        "stream:backend_agent",
        "stream:frontend_agent",
        "stream:database_agent",
        "stream:qa_agent"
    ]

    for stream in streams:
        print(f"Processing {stream}...")
        try:
            # Check if stream exists
            stream_length = await r.xlen(stream)
            if stream_length == 0:
                print(f"  ✓ Stream is empty (0 messages)")
                continue

            print(f"  Found {stream_length} messages in stream")

            # Delete the entire stream
            deleted = await r.delete(stream)
            if deleted:
                print(f"  ✓ Deleted stream ({stream_length} messages removed)")
            else:
                print(f"  ⚠ Stream not deleted (may not exist)")

        except Exception as e:
            print(f"  ✗ Error: {e}")

    print("\n" + "="*60)
    print("  Cleanup Complete!")
    print("="*60)
    print("\n💡 Consumer groups will be auto-recreated on next start")
    print("💡 You can now restart your services with: .\\start.ps1\n")

    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
