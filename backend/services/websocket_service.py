"""
WebSocket Service for Real-Time Task Updates

Uses Redis pub/sub to communicate between Celery workers and FastAPI WebSocket connections.

Architecture:
    Celery Worker -> Redis Publish -> Redis Subscriber -> WebSocket -> Browser
                     (channel)        (background task)    (push)      (React)
"""
import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

# Import sync redis for publishing from Celery tasks
import redis as sync_redis
import redis.asyncio as redis
from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Configuration - use environment variable for Redis URL (includes password if set)
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
TASK_UPDATES_CHANNEL = "bnk-forge:task-updates"

# PERFORMANCE OPTIMIZATION (ADR-019):
# Use a connection pool instead of creating new connection per publish
# This significantly reduces connection overhead
_sync_redis_pool = None

def get_sync_redis_client():
    """Get a Redis client from the connection pool (for Celery tasks)."""
    global _sync_redis_pool
    if _sync_redis_pool is None:
        _sync_redis_pool = sync_redis.ConnectionPool.from_url(
            REDIS_URL,
            decode_responses=True,
            max_connections=20
        )
    return sync_redis.Redis(connection_pool=_sync_redis_pool)


class WebSocketManager:
    """Manages WebSocket connections and broadcasts messages."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Unregister a WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected WebSocket clients."""
        if not self.active_connections:
            return

        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending message to WebSocket: {e}")
                disconnected.append(connection)

        # Clean up disconnected clients
        for conn in disconnected:
            self.disconnect(conn)

    async def send_ping(self):
        """Send ping to all connected clients to keep connections alive."""
        ping_message = {
            "type": "ping",
            "timestamp": datetime.now(UTC).isoformat() + 'Z'
        }
        await self.broadcast(ping_message)


# Global WebSocket manager instance
ws_manager = WebSocketManager()


class RedisSubscriber:
    """Subscribes to Redis pub/sub and broadcasts to WebSocket clients."""

    def __init__(self, redis_url: str, channel: str):
        self.redis_url = redis_url
        self.channel = channel
        self.redis_client: redis.Redis | None = None
        self.pubsub: redis.client.PubSub | None = None

    async def connect(self):
        """Connect to Redis and subscribe to channel."""
        try:
            self.redis_client = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            self.pubsub = self.redis_client.pubsub()
            await self.pubsub.subscribe(self.channel)
            logger.info(f"Subscribed to Redis channel: {self.channel}")
        except Exception as e:
            logger.error(f"Error connecting to Redis: {e}")
            raise

    async def disconnect(self):
        """Disconnect from Redis."""
        if self.pubsub:
            await self.pubsub.unsubscribe(self.channel)
            await self.pubsub.close()
        if self.redis_client:
            await self.redis_client.close()
        logger.info("Disconnected from Redis")

    async def listen(self):
        """Listen for messages from Redis and broadcast to WebSocket clients."""
        if not self.pubsub:
            raise RuntimeError("Not connected to Redis. Call connect() first.")

        logger.info("Started listening for Redis messages...")

        try:
            async for message in self.pubsub.listen():
                if message["type"] == "message":
                    try:
                        # Parse message data
                        data = json.loads(message["data"])
                        logger.debug(f"Received task update: {data}")

                        # Broadcast to all WebSocket clients
                        await ws_manager.broadcast(data)
                    except json.JSONDecodeError as e:
                        logger.error(f"Error decoding message: {e}")
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
        except asyncio.CancelledError:
            logger.info("Redis listener cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in Redis listener: {e}")
            raise


# Global Redis subscriber instance
redis_subscriber: RedisSubscriber | None = None


async def start_redis_subscriber():
    """Start the Redis subscriber in the background."""
    global redis_subscriber

    redis_subscriber = RedisSubscriber(REDIS_URL, TASK_UPDATES_CHANNEL)

    try:
        await redis_subscriber.connect()
        await redis_subscriber.listen()
    except asyncio.CancelledError:
        logger.info("Redis subscriber shutdown requested")
    except Exception as e:
        logger.error(f"Redis subscriber error: {e}")
    finally:
        await redis_subscriber.disconnect()


async def stop_redis_subscriber():
    """Stop the Redis subscriber."""
    if redis_subscriber:
        await redis_subscriber.disconnect()


# Keepalive task for WebSocket connections
async def keepalive_task():
    """Send periodic pings to keep WebSocket connections alive."""
    while True:
        try:
            await asyncio.sleep(30)  # Ping every 30 seconds
            await ws_manager.send_ping()
        except asyncio.CancelledError:
            logger.info("Keepalive task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in keepalive task: {e}")


# === Publishing functions (called from Celery tasks) ===

def publish_task_update(
    task_id: int,
    status: str,
    project_id: int,
    module_id: int | None = None,
    task_type: str | None = None,
    exit_code: int | None = None,
    error: str | None = None,
    duration_seconds: float | None = None,
    metadata: dict[str, Any] | None = None
):
    """
    Publish a task update to Redis for WebSocket broadcast.

    Called from Celery tasks (synchronous context).

    Args:
        task_id: Database task ID
        status: Task status (queued, in_progress, completed, failed, cancelled)
        project_id: Project ID
        module_id: Module ID (optional)
        task_type: Task type (init, plan, apply, destroy)
        exit_code: Task exit code (optional)
        error: Error message (optional)
        duration_seconds: Task duration in seconds (optional)
        metadata: Additional metadata (optional)
    """
    try:
        # Use connection-pooled Redis client (ADR-019 performance optimization)
        redis_client = get_sync_redis_client()

        message = {
            "type": "task_update",
            "task_id": task_id,
            "status": status,
            "project_id": project_id,
            "module_id": module_id,
            "task_type": task_type,
            "timestamp": datetime.now(UTC).isoformat() + 'Z',
        }

        # Add optional fields
        if exit_code is not None:
            message["exit_code"] = exit_code
        if error:
            message["error"] = error
        if duration_seconds is not None:
            message["duration_seconds"] = duration_seconds
        if metadata:
            message["metadata"] = metadata

        # Publish to Redis (no close() needed - connection returns to pool)
        redis_client.publish(TASK_UPDATES_CHANNEL, json.dumps(message))
        logger.debug(f"Published task update: task_id={task_id}, status={status}")

    except Exception as e:
        # Don't fail the task if WebSocket notification fails
        logger.error(f"Error publishing task update: {e}")


def publish_module_log(module_id: int, line: str) -> None:
    """Publish a single log line for a module to Redis for WebSocket broadcast.

    Called from Celery tasks (synchronous context) during SSH execution
    to stream real-time output to the DeploymentLogViewer frontend.
    Best-effort: failures are silently logged, never block execution.
    """
    try:
        redis_client = get_sync_redis_client()

        message = {
            "type": "module_log",
            "module_id": module_id,
            "line": line,
            "timestamp": datetime.now(UTC).isoformat() + "Z",
        }

        redis_client.publish(TASK_UPDATES_CHANNEL, json.dumps(message))
    except Exception as e:
        logger.debug("Error publishing module log: %s", e)
