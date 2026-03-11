from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from ..agent_runtime import parsing as runtime_parsing
from .base import (
    CodingAgent,
    InstallStrategy,
    RunCommandTemplate,
    RunParseResult,
    RunPlan,
)
from ..stats_extract import StatsArtifacts, extract_gemini_style_stats, merge_stats_snapshots, req_str


class GeminiAgent(CodingAgent):
    name = "gemini"
    display_name = "Google Gemini CLI"
    binary = "gemini"
    supports_images = True
    supports_videos = True
    install_strategy = InstallStrategy(kind="npm", package="@google/gemini-cli")
    run_template = RunCommandTemplate(
        base_args=("--output-format", "json", "--approval-mode", "yolo"),
        prompt_mode="flag",
        prompt_flag="-p",
        model_flag="--model",
        media_injection="symbolic",
    )

    def configure(self) -> Optional[str]:
        settings_path, telemetry_path = self._settings_paths()
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        loaded = runtime_parsing.load_json_dict(settings_path)
        data: Dict[str, Any] = loaded if loaded is not None else {}
        data["telemetry"] = {
            "enabled": True,
            "target": "local",
            "otlpEndpoint": "",
            "otlpProtocol": "http",
            "logPrompts": True,
            "outfile": str(telemetry_path),
        }
        self._write_text(settings_path, json.dumps(data, ensure_ascii=True, indent=2))
        return str(settings_path)

    def _build_run_plan(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> Optional[RunPlan]:
        model = model_override or os.environ.get("GEMINI_MODEL")
        images = images or []
        videos = videos or []
        settings_path, telemetry_path = self._settings_paths()
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        if not settings_path.exists():
            self.configure()
        env = {
            "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY"),
            "GOOGLE_API_KEY": os.environ.get("GOOGLE_API_KEY"),
            "GOOGLE_GEMINI_BASE_URL": os.environ.get("GOOGLE_GEMINI_BASE_URL"),
            "GOOGLE_CLOUD_PROJECT": os.environ.get("GOOGLE_CLOUD_PROJECT"),
        }
        return self._build_templated_run_plan(
            prompt=prompt,
            model=model,
            images=images,
            videos=videos,
            env=env,
            template=self.run_template,
            parse_output=lambda output, command_result: self._parse_pipeline_output(
                output,
                command_result,
                telemetry_path=telemetry_path,
            ),
        )

    def _parse_pipeline_output(
        self,
        output: str,
        command_result: Any,
        *,
        telemetry_path: Path,
    ) -> RunParseResult:
        payload = runtime_parsing.parse_output_json_object(output)
        artifacts = StatsArtifacts(
            raw_output=output,
            json_payload=payload,
        )
        stats = merge_stats_snapshots(
            snapshots=[
                extract_gemini_style_stats(
                    artifacts,
                    source_field="json_payload",
                ),
            ]
        )
        return RunParseResult(
            response=(req_str(payload, "$.response") if isinstance(payload, dict) else None),
            models_usage=stats.models_usage,
            llm_calls=stats.llm_calls,
            tool_calls=stats.tool_calls,
            total_cost=stats.total_cost,
            telemetry_log=str(telemetry_path),
        )

    def _settings_paths(self) -> tuple[Path, Path]:
        settings_dir = Path.home() / ".gemini"
        return settings_dir / "settings.json", settings_dir / "telemetry.log"
