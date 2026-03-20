"""Server-Sent Events endpoint for real-time updates."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from starlette.responses import StreamingResponse

from api.auth import decode_access_token
from api.deps import get_event_bus
from api.events import sse_stream_generator

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/stream")
async def event_stream(request: Request, token: str = Query(...)):
    """SSE endpoint. Streams job progress, state changes, and server events.

    The frontend connects once and receives all updates in real-time.
    Heartbeats are sent every 15 seconds to keep the connection alive.
    Token is passed as query param since SSE (EventSource) can't set headers.
    """
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

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
