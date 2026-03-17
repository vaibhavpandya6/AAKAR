"""Dead Letter Queue handler for failed messages."""

import json
import logging
from typing import Dict, List, Optional
from collections import defaultdict

import structlog

from config import settings
from messaging.schemas import Message
from messaging.message_bus import MessageBus

logger = logging.getLogger(__name__)
struct_logger = structlog.get_logger()


class DeadLetterQueue:
    """Manages dead lettering of failed messages with retry tracking."""

    def __init__(self, message_bus: MessageBus):
        """Initialize DLQ handler.

        Args:
            message_bus: MessageBus instance for publishing/consuming.
        """
        self.message_bus = message_bus
        # Track failure counts per correlation_id
        self._failure_tracker: Dict[str, int] = defaultdict(int)

    async def send_to_dlq(
        self, project_id: str, message: Message, reason: str
    ) -> None:
        """Send failed message to Dead Letter Queue.

        Args:
            project_id: Project ID for DLQ stream.
            message: Message that failed.
            reason: Reason for failure/dead lettering.

        Logs:
            CRITICAL alert after 3 failures for same correlation_id.
        """
        dlq_stream = f"dlq:{project_id}"

        # Track failures by correlation_id
        self._failure_tracker[message.correlation_id] += 1
        failure_count = self._failure_tracker[message.correlation_id]

        # Create DLQ entry with metadata
        dlq_entry = {
            "original_message_id": message.message_id,
            "correlation_id": message.correlation_id,
            "sender": message.sender,
            "recipient": message.recipient,
            "message_type": message.message_type.value,
            "payload": json.dumps(message.payload),
            "failure_reason": reason,
            "failure_count": str(failure_count),
            "original_timestamp": message.timestamp.isoformat(),
        }

        try:
            redis_id = await self.message_bus.redis.xadd(dlq_stream, dlq_entry)
            logger.warning(
                "Message sent to DLQ",
                dlq_stream=dlq_stream,
                message_id=message.message_id,
                reason=reason,
                failure_count=failure_count,
            )
        except Exception as e:
            logger.error(
                "Failed to send message to DLQ",
                dlq_stream=dlq_stream,
                error=str(e),
            )
            raise

        # Log CRITICAL alert after 3 failures for same correlation
        if failure_count >= 3:
            await struct_logger.acritical(
                "message_repeated_failure",
                correlation_id=message.correlation_id,
                project_id=project_id,
                failure_count=failure_count,
                message_type=message.message_type.value,
                sender=message.sender,
                recipient=message.recipient,
                reason=reason,
            )

    async def list_dlq(self, project_id: str) -> List[Dict]:
        """List all messages in Dead Letter Queue for project.

        Args:
            project_id: Project ID.

        Returns:
            List of DLQ entries as dictionaries.
        """
        dlq_stream = f"dlq:{project_id}"

        try:
            # Read all messages from DLQ stream
            entries = await self.message_bus.redis.xrange(dlq_stream)
            dlq_messages = []

            for redis_id, entry_data in entries:
                dlq_messages.append({
                    "redis_id": redis_id,
                    "original_message_id": entry_data.get("original_message_id"),
                    "correlation_id": entry_data.get("correlation_id"),
                    "sender": entry_data.get("sender"),
                    "recipient": entry_data.get("recipient"),
                    "message_type": entry_data.get("message_type"),
                    "failure_count": int(entry_data.get("failure_count", 0)),
                    "failure_reason": entry_data.get("failure_reason"),
                    "original_timestamp": entry_data.get("original_timestamp"),
                })

            logger.debug(
                "DLQ list retrieved",
                project_id=project_id,
                count=len(dlq_messages),
            )
            return dlq_messages

        except Exception as e:
            logger.error(
                "Failed to list DLQ",
                project_id=project_id,
                error=str(e),
            )
            return []

    async def replay_message(
        self, project_id: str, message_id: str, target_stream: Optional[str] = None
    ) -> bool:
        """Replay message from DLQ back to original stream.

        Args:
            project_id: Project ID.
            message_id: Original message ID to replay.
            target_stream: Target stream to replay to (if not original recipient).

        Returns:
            True if successful, False otherwise.
        """
        dlq_stream = f"dlq:{project_id}"

        try:
            # Find the DLQ entry by original_message_id
            entries = await self.message_bus.redis.xrange(dlq_stream)
            dlq_entry = None
            redis_id = None

            for rid, entry_data in entries:
                if entry_data.get("original_message_id") == message_id:
                    dlq_entry = entry_data
                    redis_id = rid
                    break

            if not dlq_entry:
                logger.warning(
                    "DLQ entry not found for replay",
                    project_id=project_id,
                    message_id=message_id,
                )
                return False

            # Reconstruct message
            message = Message(
                message_id=dlq_entry.get("original_message_id", message_id),
                correlation_id=dlq_entry["correlation_id"],
                sender=dlq_entry["sender"],
                recipient=dlq_entry["recipient"],
                message_type=dlq_entry["message_type"],
                payload=json.loads(dlq_entry.get("payload", "{}")),
            )

            # Determine target stream
            if not target_stream:
                # Default to recipient's stream
                target_stream = f"stream:{message.recipient}"

            # Publish to target stream
            await self.message_bus.publish(target_stream, message)

            # Remove from DLQ by marking as replayed (we can't delete, but we can track)
            logger.info(
                "Message replayed from DLQ",
                project_id=project_id,
                message_id=message_id,
                target_stream=target_stream,
            )

            return True

        except Exception as e:
            logger.error(
                "Failed to replay message from DLQ",
                project_id=project_id,
                message_id=message_id,
                error=str(e),
            )
            return False
