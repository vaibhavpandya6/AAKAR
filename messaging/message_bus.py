"""Redis Streams-based message bus for inter-agent communication."""

import json
import logging
from typing import List, Optional

import redis.asyncio as redis
from redis.asyncio import Redis

from config import settings
from messaging.schemas import Message

logger = logging.getLogger(__name__)


class MessageBus:
    """Redis Streams-based message bus for agent communication.

    Uses Redis Streams (XADD/XREAD/XREADGROUP) for reliable message delivery
    with consumer groups and acknowledgment support.
    """

    def __init__(self, redis_client: Optional[Redis] = None):
        """Initialize message bus.

        Args:
            redis_client: Optional Redis client. If not provided, will be created.
        """
        self.redis = redis_client
        self._initialized = False

    async def init(self):
        """Initialize Redis connection."""
        if not self.redis:
            self.redis = await redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        self._initialized = True
        logger.info("Message bus initialized", redis_url=settings.redis_url)

    async def close(self):
        """Close Redis connection."""
        if self.redis:
            await self.redis.close()
            logger.info("Message bus connection closed")

    async def ensure_initialized(self):
        """Ensure Redis connection is initialized."""
        if not self._initialized:
            await self.init()

    async def publish(self, stream: str, message: Message) -> str:
        """Publish message to stream.

        Args:
            stream: Stream name (e.g., "stream:agent_name" or "stream:orchestrator").
            message: Message to publish.

        Returns:
            Redis message ID (e.g., "1234567890000-0").

        Raises:
            Exception: If Redis operation fails.
        """
        await self.ensure_initialized()

        try:
            message_data = message.model_dump_redis()
            message_id = await self.redis.xadd(stream, message_data)
            logger.debug(
                "Message published",
                stream=stream,
                message_id=message.message_id,
                redis_id=message_id,
                sender=message.sender,
                recipient=message.recipient,
                message_type=message.message_type.value,
            )
            return message_id
        except Exception as e:
            logger.error(
                "Failed to publish message",
                stream=stream,
                error=str(e),
            )
            raise

    async def consume(
        self, stream: str, group: str, consumer: str, count: int = 1, timeout: int = 1000
    ) -> List[Message]:
        """Consume messages from stream using consumer group.

        Args:
            stream: Stream name to consume from.
            group: Consumer group name.
            consumer: Consumer instance name.
            count: Maximum number of messages to read.
            timeout: Read timeout in milliseconds.

        Returns:
            List of Message objects.

        Raises:
            Exception: If Redis operation fails.
        """
        await self.ensure_initialized()

        try:
            # XREADGROUP reads messages not yet delivered to this consumer
            # $$ initializes group if it doesn't exist (ignored if exists)
            results = await self.redis.xreadgroup(
                {stream: ">"},  # ">" means new messages
                group,
                consumer,
                count=count,
                block=timeout,
                noack=False,  # Require explicit acknowledgment
            )

            messages = []
            if results:
                for stream_name, stream_messages in results:
                    for redis_id, message_data in stream_messages:
                        try:
                            message = Message.model_validate_redis(message_data)
                            message.message_id = redis_id  # Use Redis ID
                            messages.append(message)
                            logger.debug(
                                "Message consumed",
                                stream=stream,
                                consumer=consumer,
                                message_type=message.message_type.value,
                            )
                        except Exception as e:
                            logger.error(
                                "Failed to parse consumed message",
                                error=str(e),
                                message_data=message_data,
                            )

            return messages
        except Exception as e:
            logger.error(
                "Failed to consume messages",
                stream=stream,
                group=group,
                consumer=consumer,
                error=str(e),
            )
            raise

    async def acknowledge(self, stream: str, group: str, message_id: str) -> None:
        """Acknowledge message processing.

        Args:
            stream: Stream name.
            group: Consumer group name.
            message_id: Redis message ID to acknowledge.

        Raises:
            Exception: If Redis operation fails.
        """
        await self.ensure_initialized()

        try:
            await self.redis.xack(stream, group, message_id)
            logger.debug(
                "Message acknowledged",
                stream=stream,
                group=group,
                message_id=message_id,
            )
        except Exception as e:
            logger.error(
                "Failed to acknowledge message",
                stream=stream,
                message_id=message_id,
                error=str(e),
            )
            raise

    async def create_consumer_group(
        self, stream: str, group: str, start_id: str = "$"
    ) -> None:
        """Create consumer group for stream (idempotent).

        Args:
            stream: Stream name.
            group: Consumer group name to create.
            start_id: Starting point for consumption ("$" = new messages, "0" = all).

        Raises:
            Exception: If creation fails with non-idempotent error.
        """
        await self.ensure_initialized()

        try:
            await self.redis.xgroup_create(stream, group, id=start_id, mkstream=True)
            logger.info(
                "Consumer group created",
                stream=stream,
                group=group,
                start_id=start_id,
            )
        except Exception as e:
            error_msg = str(e)
            # BUSYGROUP error means group already exists - this is fine
            if "BUSYGROUP" in error_msg:
                logger.debug("Consumer group already exists", stream=stream, group=group)
            else:
                logger.error(
                    "Failed to create consumer group",
                    stream=stream,
                    group=group,
                    error=error_msg,
                )
                raise

    async def get_stream_info(self, stream: str) -> dict:
        """Get information about stream.

        Args:
            stream: Stream name.

        Returns:
            Dictionary with stream info (length, first_entry, last_entry).
        """
        await self.ensure_initialized()

        try:
            info = await self.redis.xinfo_stream(stream)
            return {
                "length": info.get("length", 0),
                "first_entry_id": info.get("first-entry", [None])[0],
                "last_entry_id": info.get("last-entry", [None])[0],
                "consumer_groups": info.get("groups", 0),
            }
        except Exception as e:
            logger.warning(f"Failed to get stream info for {stream}: {str(e)}")
            return {"length": 0, "error": str(e)}

    async def delete_stream(self, stream: str) -> None:
        """Delete stream (use with caution).

        Args:
            stream: Stream name to delete.
        """
        await self.ensure_initialized()

        try:
            await self.redis.delete(stream)
            logger.warning("Stream deleted", stream=stream)
        except Exception as e:
            logger.error("Failed to delete stream", stream=stream, error=str(e))
            raise


# Global message bus instance
_message_bus_instance: Optional[MessageBus] = None


async def get_message_bus() -> MessageBus:
    """Get or create global message bus instance.

    Returns:
        MessageBus instance.
    """
    global _message_bus_instance
    if _message_bus_instance is None:
        _message_bus_instance = MessageBus()
        await _message_bus_instance.init()
    return _message_bus_instance


async def close_message_bus() -> None:
    """Close global message bus connection."""
    global _message_bus_instance
    if _message_bus_instance:
        await _message_bus_instance.close()
        _message_bus_instance = None
