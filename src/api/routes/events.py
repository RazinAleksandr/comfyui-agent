"""Server-Sent Events endpoint for real-time updates."""
from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from api.deps import get_event_bus
from api.events import sse_stream_generator

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/stream")
async def event_stream(request: Request):
    """SSE endpoint. Streams job progress, state changes, and server events.

    The frontend connects once and receives all updates in real-time.
    Heartbeats are sent every 15 seconds to keep the connection alive.
    """
    bus = get_event_bus()
    topics = ["jobs", "servers"]

    async def generate():
        async for chunk in sse_stream_generator(bus, topics):
            # Stop if client disconnected
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
