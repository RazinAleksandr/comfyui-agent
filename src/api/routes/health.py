import os
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Health check with DB status."""
    from api.deps import get_db
    db = get_db()
    db_path = Path(str(db._db_path))
    db_size = db_path.stat().st_size if db_path.exists() else 0
    return {
        "status": "ok",
        "db": {
            "path": str(db_path),
            "size_bytes": db_size,
            "size_mb": round(db_size / 1024 / 1024, 2),
        },
    }
