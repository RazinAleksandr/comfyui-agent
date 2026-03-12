from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Statuses that mean "not done yet"
_INCOMPLETE_STATUSES = ("pending", "failed", "generating")

# ---------------------------------------------------------------------------
# Dataclasses for the /parse review flow
# ---------------------------------------------------------------------------


@dataclass
class QueuedGeneration:
    """A single approved video ready for generation."""

    trend_item_id: int
    caption: str
    video_path: str  # local path to downloaded trend video
    image_path: str  # user-provided reference image
    prompt: str  # user-provided prompt
    platform: str = ""  # tiktok / instagram
    status: str = "pending"  # pending / generating / completed / failed
    output_paths: list[str] = field(default_factory=list)
    # Cost tracking
    generation_start: float | None = None  # time.time() epoch
    generation_end: float | None = None
    dph_rate: float | None = None  # vast.ai $/hr at generation time
    cost_usd: float | None = None  # computed: dph_rate * elapsed_hours


@dataclass
class ParseSession:
    """Tracks the /parse review flow in context.user_data."""

    run_id: int
    items: list[dict]  # TrendItemOut dicts from Studio API
    current_index: int = 0  # which item the user is currently reviewing
    queue: list[QueuedGeneration] = field(default_factory=list)
    session_dir: Path | None = None
    influencer_id: str = ""
    selected_dir: str = ""
    workflow: str = "wan_animate"
    created_at: str = ""  # ISO timestamp, set once in init_session_dir

    @property
    def current_item(self) -> dict | None:
        """Return the item at current_index, or None if exhausted."""
        if 0 <= self.current_index < len(self.items):
            return self.items[self.current_index]
        return None

    def advance(self) -> dict | None:
        """Move to the next item and return it, or None if exhausted."""
        self.current_index += 1
        return self.current_item

    # -- Persistence ---------------------------------------------------------

    def init_session_dir(self, base_output_dir: Path, reference_image: str) -> None:
        """Create the session directory and copy the reference image into it."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.session_dir = base_output_dir / "parse_sessions" / timestamp
        self.session_dir.mkdir(parents=True, exist_ok=True)
        (self.session_dir / "results").mkdir(exist_ok=True)

        # Copy reference image
        if reference_image and Path(reference_image).exists():
            dest = self.session_dir / "reference_image.jpg"
            shutil.copy2(reference_image, dest)

        self.save()

    def save(self) -> None:
        """Write session.json to session_dir."""
        if self.session_dir is None:
            return

        data = {
            "created_at": self.created_at,
            "run_id": self.run_id,
            "influencer_id": self.influencer_id,
            "selected_dir": self.selected_dir,
            "reference_image": "reference_image.jpg",
            "workflow": self.workflow,
            "queue": [
                {
                    "index": i + 1,
                    "trend_item_id": q.trend_item_id,
                    "video_path": q.video_path,
                    "image_path": q.image_path,
                    "caption": q.caption,
                    "prompt": q.prompt,
                    "platform": q.platform,
                    "status": q.status,
                    "output_paths": q.output_paths,
                    "generation_start": q.generation_start,
                    "generation_end": q.generation_end,
                    "dph_rate": q.dph_rate,
                    "cost_usd": q.cost_usd,
                }
                for i, q in enumerate(self.queue)
            ],
        }

        path = self.session_dir / "session.json"
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, session_dir: Path) -> ParseSession:
        """Load a ParseSession from a session_dir containing session.json."""
        data = json.loads((session_dir / "session.json").read_text())

        queue: list[QueuedGeneration] = []
        for entry in data.get("queue", []):
            # Normalize: generating → failed (crash recovery)
            status = entry.get("status", "pending")
            if status == "generating":
                status = "failed"
            queue.append(QueuedGeneration(
                trend_item_id=entry.get("trend_item_id", 0),
                caption=entry.get("caption", ""),
                video_path=entry.get("video_path", ""),
                image_path=entry.get("image_path", ""),
                prompt=entry.get("prompt", ""),
                platform=entry.get("platform", ""),
                status=status,
                output_paths=entry.get("output_paths", []),
                generation_start=entry.get("generation_start"),
                generation_end=entry.get("generation_end"),
                dph_rate=entry.get("dph_rate"),
                cost_usd=entry.get("cost_usd"),
            ))

        session = cls(
            run_id=data.get("run_id", 0),
            items=[],  # items not needed for resume
            queue=queue,
            session_dir=session_dir,
            influencer_id=data.get("influencer_id", ""),
            selected_dir=data.get("selected_dir", ""),
            workflow=data.get("workflow", "wan_animate"),
            created_at=data.get("created_at", ""),
        )
        return session

    def pending_or_failed_count(self) -> int:
        """Return number of queue items that are not completed."""
        return sum(1 for q in self.queue if q.status in _INCOMPLETE_STATUSES)

    @staticmethod
    def find_incomplete_sessions(base_output_dir: Path) -> list[Path]:
        """Scan parse_sessions/ for dirs with incomplete items."""
        sessions_root = base_output_dir / "parse_sessions"
        if not sessions_root.exists():
            return []

        incomplete: list[Path] = []
        for session_dir in sorted(sessions_root.iterdir()):
            manifest = session_dir / "session.json"
            if not manifest.exists():
                continue
            try:
                data = json.loads(manifest.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            for entry in data.get("queue", []):
                if entry.get("status") in _INCOMPLETE_STATUSES:
                    incomplete.append(session_dir)
                    break

        return incomplete
