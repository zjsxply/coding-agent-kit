from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent, InstallStrategy, RunCommandTemplate, extract_gemini_style_stats, req_str
from ..models import RunResult


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
        settings_path = Path.home() / ".gemini" / "settings.json"
        telemetry_path = Path.home() / ".gemini" / "telemetry.log"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        loaded = self._load_json_dict(settings_path)
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

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        model = model_override or os.environ.get("GEMINI_MODEL")
        images = images or []
        videos = videos or []
        telemetry_path = Path.home() / ".gemini" / "telemetry.log"
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        if not (Path.home() / ".gemini" / "settings.json").exists():
            self.configure()
        env = {
            "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY"),
            "GOOGLE_API_KEY": os.environ.get("GOOGLE_API_KEY"),
            "GOOGLE_GEMINI_BASE_URL": os.environ.get("GOOGLE_GEMINI_BASE_URL"),
            "GOOGLE_CLOUD_PROJECT": os.environ.get("GOOGLE_CLOUD_PROJECT"),
        }
        template = self.run_template
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=prompt,
            model=model,
            images=images,
            videos=videos,
        )
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        payload = self._parse_output_json_object(output)
        artifacts = self._build_stats_artifacts(
            raw_output=output,
            json_payload=payload,
        )
        stats = self._merge_stats_snapshots(
            snapshots=[
                extract_gemini_style_stats(
                    artifacts,
                    source_field="json_payload",
                ),
            ]
        )
        return self.finalize_run(
            command_result=result,
            response=(req_str(payload, "$.response") if isinstance(payload, dict) else None),
            models_usage=stats.models_usage,
            llm_calls=stats.llm_calls,
            tool_calls=stats.tool_calls,
            total_cost=stats.total_cost,
            telemetry_log=str(telemetry_path),
        )
