"""Clear pending messages from Redis streams to fix stuck workers.

Run this when the system has pending messages that are blocking execution:
    python scripts/clear_redis_pending.py
"""

import redis


def clear_pending_messages():
    """Clear all pending messages from Redis streams."""
    r = redis.Redis(host='localhost', port=6379, decode_responses=True)

    streams = [k for k in r.keys('stream:*')]

    print(f"Found {len(streams)} streams")

    for stream in streams:
        try:
            groups = r.xinfo_groups(stream)

            for group_info in groups:
                group_name = group_info['name']
                pending_count = group_info['pending']

                if pending_count > 0:
                    print(f"\n{stream} / {group_name}: {pending_count} pending messages")

                    # Get pending message details
                    pending = r.xpending_range(
                        stream,
                        group_name,
                        min='-',
                        max='+',
                        count=1000
                    )

                    # ACK all pending messages
                    for msg in pending:
                        msg_id = msg['message_id']
                        r.xack(stream, group_name, msg_id)
                        print(f"  ACKed: {msg_id}")

                    print(f"  [OK] Cleared {len(pending)} pending messages")
                else:
                    print(f"[OK] {stream} / {group_name}: No pending messages")

        except Exception as e:
            print(f"Error processing {stream}: {e}")

    print("\n[SUCCESS] Done! All pending messages cleared.")


if __name__ == "__main__":
    clear_pending_messages()
