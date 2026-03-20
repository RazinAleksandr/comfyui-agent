"""Mock VastAgentService for frontend debugging.

Drop-in replacement that simulates the full GPU pipeline without
renting real hardware. Produces a dummy output video (solid color
with text overlay) so the full flow can be tested end-to-end.

Temporary — remove after frontend debugging is done.
"""
from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from vast_agent.service import RunResult, ServerStatus

logger = logging.getLogger(__name__)

_MOCK_INSTANCE_ID = 99999
_MOCK_SSH_HOST = "mock.vast.ai"
_MOCK_SSH_PORT = 10600
_MOCK_DPH = 0.35

# Simulated delays (seconds)
_UP_DELAY = 3
_RUN_DELAY = 5
_DOWN_DELAY = 1


class VastAgentServiceMock:
    """Mock that mirrors VastAgentService interface."""

    def __init__(self, **kwargs) -> None:
        self._running = False
        self._workflow: str | None = None

    def status(self) -> ServerStatus:
        return ServerStatus(
            running=self._running,
            instance_id=_MOCK_INSTANCE_ID if self._running else None,
            ssh_host=_MOCK_SSH_HOST if self._running else None,
            ssh_port=_MOCK_SSH_PORT if self._running else None,
            actual_status="running" if self._running else None,
            dph_total=_MOCK_DPH if self._running else None,
            ssh_reachable=self._running,
            label="mock-avatar-factory",
        )

    def up(self, workflow: str) -> ServerStatus:
        logger.info("[mock] Starting GPU server (workflow=%s)...", workflow)
        time.sleep(_UP_DELAY)
        self._running = True
        self._workflow = workflow
        logger.info("[mock] GPU server ready.")
        return self.status()

    def run(
        self,
        workflow: str,
        inputs: dict[str, Path] | None = None,
        overrides: dict[str, str] | None = None,
        output_dir: str = "output",
    ) -> RunResult:
        if not self._running:
            raise RuntimeError("Mock server not running. Call up() first.")

        inputs = inputs or {}
        overrides = overrides or {}

        logger.info(
            "[mock] Running generation: workflow=%s inputs=%s overrides=%s",
            workflow,
            list(inputs.keys()),
            list(overrides.keys()),
        )
        time.sleep(_RUN_DELAY)

        # Produce dummy output files
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        outputs: list[str] = []

        # If a reference video was provided, copy it as "raw" output
        ref_video = inputs.get("reference_video")
        if ref_video and ref_video.exists():
            raw_path = out_dir / f"raw_mock_output{ref_video.suffix}"
            shutil.copy2(ref_video, raw_path)
            outputs.append(str(raw_path))
            logger.info("[mock] Copied reference video as raw output: %s", raw_path.name)
        else:
            # Create a minimal placeholder file
            placeholder = out_dir / "raw_mock_output.mp4"
            placeholder.write_bytes(_MINIMAL_MP4)
            outputs.append(str(placeholder))
            logger.info("[mock] Created placeholder output: %s", placeholder.name)

        logger.info("[mock] Generation complete. %d output(s).", len(outputs))
        return RunResult(outputs=outputs, output_dir=str(out_dir))

    def down(self) -> None:
        logger.info("[mock] Destroying GPU server...")
        time.sleep(_DOWN_DELAY)
        self._running = False
        self._workflow = None
        logger.info("[mock] Server destroyed.")

    def destroy(self) -> None:
        self._running = False
        self._workflow = None
        logger.info("[mock] Server force-destroyed.")

    def push(self) -> None:
        logger.info("[mock] Push (no-op in mock mode).")

    def has_instance(self) -> bool:
        return self._running


# Smallest valid MP4 (ftyp + moov boxes, ~200 bytes, 0 frames).
# Just enough so downstream code sees a real file with .mp4 extension.
_MINIMAL_MP4 = (
    b"\x00\x00\x00\x1c"  # box size
    b"ftyp"               # box type
    b"isom"               # major brand
    b"\x00\x00\x02\x00"  # minor version
    b"isom"               # compatible brand
    b"iso2"               # compatible brand
    b"\x00\x00\x00\x08"  # moov box size
    b"moov"               # moov box type
)
