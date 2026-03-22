
"""Check Redis consumer groups and pending messages."""
import asyncio
import redis.asyncio as aioredis


async def main():
    r = await aioredis.from_url("redis://localhost:6379", decode_responses=True)

    streams = ["stream:backend_agent", "stream:frontend_agent", "stream:database_agent", "stream:qa_agent"]

    for stream in streams:
        print(f"\n=== {stream} ===")
        try:
            # Check consumer groups
            groups = await r.xinfo_groups(stream)
            print(f"Consumer groups: {len(groups)}")
            for group in groups:
                group_name = group.get('name', 'N/A')
                pending = group.get('pending', 0)
                consumers = group.get('consumers', 0)
                print(f"  Group: {group_name}, Pending: {pending}, Consumers: {consumers}")

                # Check pending messages for this group
                if pending > 0:
                    pending_info = await r.xpending_range(stream, group_name, "-", "+", count=10)
                    print(f"    Pending messages:")
                    for p in pending_info:
                        msg_id = p.get('message_id', 'N/A')
                        consumer = p.get('consumer', 'N/A')
                        times_delivered = p.get('times_delivered', 0)
                        print(f"      - {msg_id} delivered to {consumer} ({times_delivered} times)")

        except Exception as e:
            print(f"  Error: {e}")

    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
