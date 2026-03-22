"""Test xreadgroup signature for redis-py 7.x"""
import asyncio
import redis.asyncio as aioredis

async def test():
    r = await aioredis.from_url(
        "redis://localhost:6379",
        encoding="utf-8",
        decode_responses=True,
    )

    stream = "stream:test_stream"
    group = "test_group"
    consumer = "test_consumer"

    # Create stream and group
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
        print(f"✓ Created group {group} on stream {stream}")
    except Exception as e:
        if "BUSYGROUP" in str(e):
            print(f"✓ Group {group} already exists")
        else:
            print(f"✗ Failed to create group: {e}")
            return

    # Test xreadgroup with different signatures
    print("\n=== Testing xreadgroup signatures ===\n")

    # Signature 1: (groupname, consumername, streams, ...)
    try:
        print("Test 1: xreadgroup(group, consumer, {stream: '>'}, count=1, block=1000)")
        result = await r.xreadgroup(
            group,
            consumer,
            {stream: ">"},
            count=1,
            block=1000,
        )
        print(f"✓ Success! Result: {result}")
    except Exception as e:
        print(f"✗ Failed: {e}")

    # Signature 2: (streams, groupname, consumername, ...) - OLD
    try:
        print("\nTest 2: xreadgroup({stream: '>'}, group, consumer, count=1, block=1000)")
        result = await r.xreadgroup(
            {stream: ">"},
            group,
            consumer,
            count=1,
            block=1000,
        )
        print(f"✓ Success! Result: {result}")
    except Exception as e:
        print(f"✗ Failed: {e}")

    await r.close()

asyncio.run(test())
