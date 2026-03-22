"""Clear pending messages from Redis consumer groups."""
import asyncio
import redis.asyncio as aioredis


async def main():
    r = await aioredis.from_url("redis://localhost:6379", decode_responses=True)

    streams = ["stream:backend_agent", "stream:frontend_agent", "stream:database_agent", "stream:qa_agent"]

    for stream in streams:
        print(f"\n=== Clearing {stream} ===")
        try:
            # Get pending messages
            pending_info = await r.xpending_range(stream, "workers", "-", "+", count=100)

            if not pending_info:
                print(f"  No pending messages")
                continue

            print(f"  Found {len(pending_info)} pending messages")

            # ACK each pending message to clear them
            for p in pending_info:
                msg_id = p.get('message_id')
                if msg_id:
                    await r.xack(stream, "workers", msg_id)
                    print(f"    ACKed {msg_id}")

            print(f"  ✓ Cleared {len(pending_info)} pending messages")

        except Exception as e:
            print(f"  Error: {e}")

    print("\n=== Done ===")
    print("Now create a NEW project - the workers will pick up fresh tasks!")

    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
