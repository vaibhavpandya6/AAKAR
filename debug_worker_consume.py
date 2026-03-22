"""Debug script to test if workers can consume from Redis streams."""
import asyncio
import redis.asyncio as aioredis


async def main():
    print("\n=== Testing Worker Consumption from Redis ===\n")

    r = await aioredis.from_url("redis://localhost:6379", decode_responses=True)

    stream = "stream:backend_agent"
    group = "workers"
    consumer = "debug-consumer"

    try:
        # Check stream info
        info = await r.xinfo_stream(stream)
        print(f"Stream: {stream}")
        print(f"  Length: {info.get('length', 0)}")
        print(f"  First entry: {info.get('first-entry', 'None')}")
        print(f"  Last entry: {info.get('last-entry', 'None')}")
        print()

        # Check consumer groups
        groups = await r.xinfo_groups(stream)
        print(f"Consumer Groups:")
        for g in groups:
            print(f"  - {g['name']}: {g['pending']} pending, {g['consumers']} consumers")
        print()

        # Check pending messages
        pending = await r.xpending_range(stream, group, "-", "+", count=10)
        print(f"Pending Messages (claimed but not ACKed): {len(pending)}")
        for p in pending:
            print(f"  - {p['message_id']}: consumer={p['consumer']}, times_delivered={p['times_delivered']}")
        print()

        # Try to read with XREADGROUP
        print(f"Attempting XREADGROUP (timeout 2 seconds)...")
        results = await r.xreadgroup(
            group,
            consumer,
            {stream: ">"},
            count=1,
            block=2000,  # 2 second timeout
            noack=False,
        )

        if results:
            print(f"✓ Successfully read {len(results)} streams")
            for stream_name, messages in results:
                print(f"  Stream: {stream_name}, Messages: {len(messages)}")
                for msg_id, data in messages:
                    print(f"    {msg_id}: {list(data.keys())}")
        else:
            print("✗ No messages returned (empty queue or all delivered)")

    except Exception as e:
        print(f"✗ Error: {e}")
    finally:
        await r.aclose()

    print("\n=== Debug Complete ===\n")


if __name__ == "__main__":
    asyncio.run(main())
