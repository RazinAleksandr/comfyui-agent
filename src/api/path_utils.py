"""Utilities for converting between relative and absolute paths.

The database and manifests store paths relative to data_dir (the shared/
directory). These helpers convert between the stored relative form and
absolute filesystem paths needed for I/O.
"""
from __future__ import annotations

from pathlib import Path


def to_relative(abs_path: str, data_dir: Path) -> str:
    """Convert an absolute path to a relative path under *data_dir*.

    If already relative, returns unchanged.  Falls back to extracting
    everything after ``/shared/`` for paths with an old project prefix.
    """
    if not abs_path or not abs_path.startswith("/"):
        return abs_path
    try:
        return str(Path(abs_path).relative_to(data_dir))
    except ValueError:
        if "/shared/" in abs_path:
            return abs_path.split("/shared/", 1)[1]
        return abs_path


def to_absolute(stored_path: str, data_dir: Path) -> Path:
    """Resolve a stored (possibly relative) path to an absolute ``Path``.

    - Relative path -> ``data_dir / stored_path``
    - Absolute path that exists -> returned as-is
    - Stale absolute path (old prefix) -> rewritten via ``/shared/`` anchor
    """
    if not stored_path:
        return data_dir / "__nonexistent__"
    p = Path(stored_path)
    if not p.is_absolute():
        return data_dir / stored_path
    if p.exists():
        return p
    # Stale absolute path from a previous project directory name
    if "/shared/" in stored_path:
        rel = stored_path.split("/shared/", 1)[1]
        return data_dir / rel
    return p
