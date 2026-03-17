from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from trend_parser.adapters.types import RawTrendVideo
from trend_parser.caption import CaptionRunConfig, run_caption
from trend_parser.config import ParserConfig
from trend_parser.downloader import TrendDownloadService
from trend_parser.filter import CandidateFilterConfig, run_candidate_filter
from trend_parser.ingest import TrendIngestService
from trend_parser.persona import PersonaProfile
from trend_parser.schemas import PipelinePlatformRunOut, PipelineRunOut, PipelineRunRequest
from trend_parser.store import FilesystemStore
from trend_parser.vlm import SelectorRunConfig, SelectorThresholds, find_video_files, run_selector

logger = logging.getLogger(__name__)


class PipelineRunnerService:
    def __init__(self, config: ParserConfig, store: FilesystemStore, seed_dir: Path):
        self.config = config
        self.store = store
        self.seed_dir = seed_dir
        self.ingest = TrendIngestService(config=config, seed_dir=seed_dir)
        self.downloader = TrendDownloadService(config=config, downloads_dir=store.downloads_dir)

    def run(
        self,
        request: PipelineRunRequest,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> tuple[PipelineRunOut, list[dict] | None]:
        """Run the full pipeline. Optionally report stage progress via *progress_callback*.

        Returns a 2-tuple: (pipeline result, auto-review videos or None).
        """
        influencer = self.store.load_influencer(request.influencer_id)
        if influencer is None:
            raise ValueError(f"Influencer '{request.influencer_id}' not found")

        persona = self._to_persona(influencer)
        enabled_platforms = [platform for platform, cfg in request.platforms.items() if cfg.enabled]
        if not enabled_platforms:
            raise ValueError("Enable at least one platform.")
        if request.filter.enabled and not request.download.enabled:
            raise ValueError("Filter stage requires downloads to be enabled.")
        if request.vlm.enabled and not request.filter.enabled:
            raise ValueError("Gemini stage requires filter stage to be enabled.")

        # --- progress tracking ---------------------------------------------------
        # Accumulate items across platforms so multi-platform runs show totals
        stage_totals: dict[str, int] = {"ingestion": 0, "download": 0, "filter": 0, "vlm": 0, "review": 0}
        progress: dict = {
            "current_stage": None,
            "platforms_done": 0,
            "platforms_total": len(enabled_platforms),
            "stages": {
                "ingestion": {"status": "pending", "items": 0},
                "download": {"status": "pending", "items": 0},
                "filter": {"status": "pending", "items": 0},
                "vlm": {"status": "pending", "items": 0},
                "review": {"status": "pending", "items": 0},
            },
        }

        def _report(stage: str, status: str, **extra: object) -> None:
            if status == "running":
                progress["current_stage"] = stage
                progress["stages"][stage]["status"] = "running"
            elif status == "completed":
                items = extra.get("items", 0) or 0
                stage_totals[stage] += items
                progress["stages"][stage] = {"status": "completed", "items": stage_totals[stage]}
            else:
                progress["stages"][stage]["status"] = status
            if progress_callback:
                progress_callback(dict(progress))

        # --- run ----------------------------------------------------------------
        started_at = datetime.now(UTC)
        stamp = started_at.strftime("%Y%m%d_%H%M%S")
        base_dir = self.store.influencer_pipeline_runs_dir(request.influencer_id) / stamp
        base_dir.mkdir(parents=True, exist_ok=True)

        platform_outputs: list[PipelinePlatformRunOut] = []

        # Save initial manifest so the frontend can see the run immediately
        def _save_manifest() -> None:
            self.store.save_pipeline_manifest(
                request.influencer_id,
                stamp,
                {
                    "influencer_id": request.influencer_id,
                    "started_at": started_at.isoformat(),
                    "base_dir": str(base_dir.resolve()),
                    "platforms": [item.model_dump(mode="json") for item in platform_outputs],
                    "request": request.model_dump(mode="json"),
                },
            )

        _save_manifest()  # create empty manifest so the run appears in the list

        for platform, cfg in request.platforms.items():
            platform_name = platform.lower().strip()
            if not cfg.enabled:
                continue

            selector_payload = cfg.selector.model_dump()
            if not selector_payload.get("hashtags"):
                selector_payload["hashtags"] = influencer.hashtags or []

            platform_dir = base_dir / platform_name
            download_dir = platform_dir / "downloads"
            analysis_dir = platform_dir / "analysis"
            filtered_dir = platform_dir / "filtered"
            vlm_dir = platform_dir / "vlm"
            selected_dir = platform_dir / "selected"
            rejected_dir = platform_dir / "rejected"

            platform_output = self._run_platform(
                influencer=influencer,
                persona=persona,
                platform_name=platform_name,
                selector_payload=selector_payload,
                cfg=cfg,
                request=request,
                platform_dir=platform_dir,
                download_dir=download_dir,
                analysis_dir=analysis_dir,
                filtered_dir=filtered_dir,
                vlm_dir=vlm_dir,
                selected_dir=selected_dir,
                rejected_dir=rejected_dir,
                stamp=stamp,
                progress_report=_report,
            )
            platform_outputs.append(platform_output)
            _save_manifest()  # update after each platform completes

        # --- auto-review (caption generation) ------------------------------------
        auto_review_videos: list[dict] | None = None
        if request.review.auto and request.vlm.enabled and request.vlm.sync_folders:
            auto_review_videos = self._collect_auto_review(
                request=request,
                base_dir=base_dir,
                enabled_platforms=enabled_platforms,
                progress_report=_report,
            )
        elif request.review.auto:
            logger.warning("auto_review=True but VLM sync_folders=False or VLM disabled — skipping auto-review")

        result = PipelineRunOut(
            influencer_id=request.influencer_id,
            started_at=started_at,
            base_dir=str(base_dir.resolve()),
            platforms=platform_outputs,
        )
        return result, auto_review_videos

    def _run_platform(
        self,
        *,
        influencer,
        persona,
        platform_name: str,
        selector_payload: dict,
        cfg,
        request: PipelineRunRequest,
        platform_dir: Path,
        download_dir: Path,
        analysis_dir: Path,
        filtered_dir: Path,
        vlm_dir: Path,
        selected_dir: Path,
        rejected_dir: Path,
        stamp: str,
        progress_report: Callable[..., None] | None = None,
    ) -> PipelinePlatformRunOut:
        def _report(stage: str, status: str, **extra: object) -> None:
            if progress_report:
                progress_report(stage, status, platform=platform_name, **extra)

        _report("ingestion", "running")
        collected = self.ingest.collect_raw(
            platforms=[platform_name],
            limit_per_platform=cfg.limit,
            source=cfg.source,
            selectors={platform_name: selector_payload},
        )
        videos = collected.get(platform_name, [])
        _report("ingestion", "completed", items=len(videos))

        download_records: list[dict] = []
        download_counts: dict[str, int] = {}
        if request.download.enabled:
            _report("download", "running")
            download_records = self.downloader.download_raw_videos(
                platform=platform_name,
                videos=videos,
                force=request.download.force,
                download_dir=str(download_dir),
            )
            download_counts = dict(Counter(record["status"] for record in download_records))
            _report("download", "completed", items=download_counts.get("downloaded", 0))

        candidate_report_path = None
        has_downloads = download_counts.get("downloaded", 0) > 0
        filter_accepted = 0
        if request.filter.enabled and has_downloads:
            _report("filter", "running")
            try:
                report, report_path = run_candidate_filter(
                    CandidateFilterConfig(
                        download_dir=download_dir,
                        report_dir=analysis_dir,
                        filtered_dir=filtered_dir,
                        probe_seconds=request.filter.probe_seconds,
                        top_k=request.filter.top_k,
                        workers=request.filter.workers,
                        sync_filtered=True,
                    )
                )
                candidate_report_path = str(report_path.resolve())
                filter_accepted = report.get("accepted", 0) if isinstance(report, dict) else 0
            except RuntimeError:
                pass  # no videos to filter — continue gracefully
            _report("filter", "completed", items=filter_accepted)

        accepted = None
        rejected = None
        vlm_summary_path = None
        if request.vlm.enabled and candidate_report_path is not None:
            _report("vlm", "running")
            run_selector(
                SelectorRunConfig(
                    input_dir=filtered_dir,
                    output_dir=vlm_dir,
                    selected_dir=selected_dir,
                    rejected_dir=rejected_dir,
                    theme=request.vlm.theme,
                    hashtags=influencer.hashtags or [],
                    model=request.vlm.model,
                    api_key_env=request.vlm.api_key_env,
                    timeout_sec=request.vlm.timeout_sec,
                    mock=request.vlm.mock,
                    max_videos=request.vlm.max_videos,
                    sync_folders=request.vlm.sync_folders,
                    thresholds=SelectorThresholds(
                        min_readiness=request.vlm.thresholds.min_readiness,
                        min_confidence=request.vlm.thresholds.min_confidence,
                        min_persona_fit=request.vlm.thresholds.min_persona_fit,
                        max_occlusion_risk=request.vlm.thresholds.max_occlusion_risk,
                        max_scene_cut_complexity=request.vlm.thresholds.max_scene_cut_complexity,
                    ),
                    persona=persona,
                    video_suggestions_requirement=influencer.video_suggestions_requirement,
                )
            )
            summary_file = _latest_summary(vlm_dir)
            if summary_file is not None:
                payload = json.loads(summary_file.read_text(encoding="utf-8"))
                accepted = int(payload.get("accepted", 0))
                rejected = int(payload.get("rejected", 0))
                vlm_summary_path = str(summary_file.resolve())
            _report("vlm", "completed", items=accepted or 0)

        manifest_payload = {
            "platform": platform_name,
            "source": cfg.source,
            "selector": selector_payload,
            "ingested_items": [_raw_video_to_dict(video) for video in videos],
            "download_records": download_records,
            "candidate_report_path": candidate_report_path,
            "vlm_summary_path": vlm_summary_path,
            "selected_dir": str(selected_dir.resolve()) if selected_dir.exists() else None,
            "accepted": accepted,
            "rejected": rejected,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        manifest_path = platform_dir / "platform_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

        return PipelinePlatformRunOut(
            platform=platform_name,
            source=cfg.source,
            ingested_items=len(videos),
            download_counts=download_counts,
            candidate_report_path=candidate_report_path,
            filtered_dir=str(filtered_dir.resolve()) if request.filter.enabled else None,
            vlm_summary_path=vlm_summary_path,
            selected_dir=str(selected_dir.resolve()) if request.vlm.enabled else None,
            accepted=accepted,
            rejected=rejected,
        )

    def _collect_auto_review(
        self,
        *,
        request: PipelineRunRequest,
        base_dir: Path,
        enabled_platforms: list[str],
        progress_report: Callable[..., None] | None = None,
    ) -> list[dict]:
        """Run caption generation on VLM-selected videos for auto-review."""

        def _report(stage: str, status: str, **extra: object) -> None:
            if progress_report:
                progress_report(stage, status, **extra)

        # Gather video files from each platform's selected_dir
        video_paths: list[Path] = []
        for platform in enabled_platforms:
            selected_dir = base_dir / platform / "selected"
            if selected_dir.is_dir():
                video_paths.extend(find_video_files(selected_dir, max_videos=200))

        if not video_paths:
            _report("review", "completed", items=0)
            return []

        # Check if this influencer has a LoRA configured
        has_lora = False
        try:
            from comfy_pipeline.config import WorkflowConfig
            wf_config = WorkflowConfig.from_yaml(
                Path(__file__).resolve().parents[2] / "configs" / "wan_animate.yaml"
            )
            has_lora = wf_config.characters.get(request.influencer_id) is not None
        except Exception as exc:
            logger.warning("Could not load WorkflowConfig for LoRA check, defaulting has_lora=False: %s", exc)

        _report("review", "running")
        results = run_caption(
            CaptionRunConfig(
                video_paths=video_paths,
                model=request.review.model,
                api_key_env=request.review.api_key_env,
                timeout_sec=request.review.timeout_sec,
                has_lora=has_lora,
            )
        )

        auto_videos = [
            {"file_name": r.file_name, "approved": True, "prompt": r.caption}
            for r in results
        ]
        _report("review", "completed", items=len(auto_videos))
        return auto_videos

    def _to_persona(self, influencer) -> PersonaProfile | None:
        persona_path = self.store.influencer_dir(influencer.influencer_id) / "persona.json"
        if persona_path.exists():
            payload = json.loads(persona_path.read_text(encoding="utf-8"))
            return PersonaProfile.from_dict(payload)
        return PersonaProfile(
            persona_id=influencer.influencer_id,
            name=influencer.name,
            summary=influencer.description or "",
        )


def _latest_summary(output_dir: Path) -> Path | None:
    summaries = sorted(output_dir.glob("vlm_summary_*.json"))
    if not summaries:
        return None
    return summaries[-1]


def _raw_video_to_dict(video: RawTrendVideo) -> dict:
    return {
        "platform": video.platform,
        "source_item_id": video.source_item_id,
        "video_url": video.video_url,
        "caption": video.caption,
        "hashtags": video.hashtags,
        "audio": video.audio,
        "style_hint": video.style_hint,
        "published_at": video.published_at.isoformat() if video.published_at else None,
        "views": video.views,
        "likes": video.likes,
        "comments": video.comments,
        "shares": video.shares,
    }
