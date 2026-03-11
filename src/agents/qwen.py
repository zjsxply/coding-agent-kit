from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from ..agent_runtime import env as runtime_env
from ..agent_runtime import parsing as runtime_parsing
from .base import (
    CodingAgent,
    InstallStrategy,
    RunCommandTemplate,
    RunParseResult,
    RunPlan,
)
from ..stats_extract import (
    JsonlStatsSpec,
    StatsArtifacts,
    StatsMergeStrategy,
    extract_gemini_style_stats,
    extract_json_result_stats,
    extract_jsonl_stats,
    merge_stats_snapshots,
    req_str,
    select_values,
)


class QwenAgent(CodingAgent):
    name = "qwen"
    display_name = "Qwen Code"
    binary = "qwen"
    supports_images = True
    supports_videos = True
    install_strategy = InstallStrategy(kind="npm", package="@qwen-code/qwen-code")
    run_template = RunCommandTemplate(
        base_args=(
            "--output-format",
            "json",
            "--approval-mode",
            "yolo",
            "--telemetry",
            "--telemetry-target",
            "local",
            "--telemetry-otlp-endpoint",
            "",
            "--telemetry-log-prompts",
        ),
        prompt_mode="flag",
        prompt_flag="-p",
        model_flag="--model",
        media_injection="symbolic",
    )

    def configure(self) -> Optional[str]:
        settings = self._resolve_runtime_settings(model_override=None)
        updates = self._build_settings_updates(settings)
        path = Path.home() / ".qwen" / "settings.json"
        current_settings = runtime_parsing.load_json_dict(path) or {}
        merged_settings = self._merge_dict(current_settings, updates)
        self._write_text(path, json.dumps(merged_settings, ensure_ascii=True, indent=2))
        return str(path)

    def _build_run_plan(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> Optional[RunPlan]:
        images = images or []
        videos = videos or []

        telemetry_path = self._build_run_telemetry_path()
        settings = self._resolve_runtime_settings(model_override=model_override)
        qwen_key = settings["qwen_key"]
        qwen_base = settings["qwen_base"]
        qwen_model = settings["qwen_model"]
        env = {
            "OPENAI_API_KEY": qwen_key,
            "OPENAI_BASE_URL": qwen_base,
            "OPENAI_MODEL": qwen_model,
            "TAVILY_API_KEY": settings["tavily_key"],
            "GOOGLE_API_KEY": settings["google_key"],
            "GOOGLE_SEARCH_ENGINE_ID": settings["google_search_engine_id"],
        }
        extra_args: list[str] = []
        extra_args.extend(["--telemetry-outfile", str(telemetry_path)])
        if qwen_key:
            extra_args.extend(["--auth-type", "openai"])
        return self._build_templated_run_plan(
            prompt=prompt,
            model=qwen_model,
            images=images,
            videos=videos,
            env=env,
            extra_args=extra_args,
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
        payload = runtime_parsing.parse_output_json(output)
        jsonl_payloads = [item for item in (select_values(payload, "$[*]") or []) if isinstance(item, dict)]
        artifacts = StatsArtifacts(
            raw_output=output,
            json_payload=payload,
            jsonl_payloads=tuple(jsonl_payloads),
        )
        stats = merge_stats_snapshots(
            snapshots=[
                extract_json_result_stats(
                    artifacts,
                    inner=extract_gemini_style_stats,
                ),
                extract_jsonl_stats(artifacts, spec=JsonlStatsSpec()),
            ],
            strategy=StatsMergeStrategy.FALLBACK,
        )
        response = (
            req_str(payload, "$.result")
            or next(
                (
                    text
                    for text in (
                        runtime_parsing.last_nonempty_text(select_values(payload, path))
                        for path in (
                            '$[?(@.type == "result")].result',
                            '$[?(@.type == "assistant")].message.content[?(@.type == "text")].text',
                        )
                    )
                    if text is not None
                ),
                None,
            )
        )
        return RunParseResult(
            response=response,
            models_usage=stats.models_usage,
            llm_calls=stats.llm_calls,
            tool_calls=stats.tool_calls,
            total_cost=stats.total_cost,
            telemetry_log=str(telemetry_path),
        )

    @staticmethod
    def _merge_dict(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        merged: Dict[str, Any] = dict(base)
        for key, value in updates.items():
            existing = merged.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                merged[key] = QwenAgent._merge_dict(existing, value)
            else:
                merged[key] = value
        return merged

    def _build_run_telemetry_path(self) -> Path:
        directory = Path.home() / ".qwen" / "telemetry"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
        return directory / f"cakit-{stamp}.log"

    def _resolve_runtime_settings(self, *, model_override: Optional[str]) -> Dict[str, Optional[str]]:
        return {
            "qwen_key": runtime_env.resolve_openai_api_key("QWEN_OPENAI_API_KEY"),
            "qwen_base": runtime_env.resolve_openai_base_url("QWEN_OPENAI_BASE_URL"),
            "qwen_model": runtime_env.resolve_openai_model("QWEN_OPENAI_MODEL", model_override=model_override),
            "tavily_key": os.environ.get("TAVILY_API_KEY"),
            "google_key": os.environ.get("CAKIT_QWEN_GOOGLE_API_KEY"),
            "google_search_engine_id": os.environ.get("GOOGLE_SEARCH_ENGINE_ID"),
        }

    def _build_settings_updates(self, settings: Dict[str, Optional[str]]) -> Dict[str, Any]:
        providers = [{"type": "dashscope"}]
        default_provider = "dashscope"
        tavily_key = settings["tavily_key"]
        google_key = settings["google_key"]
        google_search_engine_id = settings["google_search_engine_id"]
        if tavily_key:
            providers.append({"type": "tavily", "apiKey": tavily_key})
            default_provider = "tavily"
        if google_key and google_search_engine_id:
            providers.append({"type": "google", "apiKey": google_key, "searchEngineId": google_search_engine_id})
            if default_provider == "dashscope":
                default_provider = "google"
        return {
            "webSearch": {
                "provider": providers,
                "default": default_provider,
            },
            "permissions": {
                "defaultMode": "yolo",
                "confirmShellCommands": False,
                "confirmFileEdits": False,
            },
            "telemetry": {
                "enabled": True,
                "target": "local",
                "otlpEndpoint": "",
                "logPrompts": True,
                "outfile": str(Path.home() / ".qwen" / "telemetry.log"),
            },
        }
