"""In-process event bus and SSE streaming for real-time updates.

The EventBus is an asyncio pub/sub used to push job progress, state
changes, and server events to connected frontend clients via SSE.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """Simple in-process pub/sub for async consumers."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, topic: str) -> asyncio.Queue:
        """Subscribe to a topic. Returns an asyncio.Queue that receives events."""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(topic, []).append(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(topic)
        if subs:
            try:
                subs.remove(q)
            except ValueError:
                pass

    def publish(self, topic: str, event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to all subscribers of the topic.

        Drops events for slow consumers rather than blocking.
        """
        message = {"type": event_type, "data": data}
        for q in self._subscribers.get(topic, []):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # Drop oldest to make room
                try:
                    q.get_nowait()
                    q.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    @property
    def subscriber_count(self) -> int:
        return sum(len(subs) for subs in self._subscribers.values())


async def sse_stream_generator(
    bus: EventBus,
    topics: list[str],
    heartbeat_interval: float = 15.0,
):
    """Async generator that yields SSE-formatted events.

    Merges events from multiple topics into a single stream.
    Sends a heartbeat comment every ``heartbeat_interval`` seconds
    to keep the connection alive.
    """
    queues = [(topic, bus.subscribe(topic)) for topic in topics]
    last_heartbeat = 0.0
    try:
        while True:
            import time as _time
            # Check all queues for events
            sent = False
            for _topic, q in queues:
                try:
                    event = q.get_nowait()
                    yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                    sent = True
                except asyncio.QueueEmpty:
                    pass

            if not sent:
                now = _time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now
                await asyncio.sleep(0.5)
            else:
                # Drain remaining events quickly
                await asyncio.sleep(0.05)
    finally:
        for topic, q in queues:
            bus.unsubscribe(topic, q)
