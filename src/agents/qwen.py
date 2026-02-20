from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

from .base import (
    CodingAgent,
    InstallStrategy,
    RunCommandTemplate,
    extract_gemini_style_stats,
    extract_json_result_stats,
    extract_jsonl_stats,
    req_str,
)
from ..models import RunResult


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
            "--telemetry-outfile",
            str(Path.home() / ".qwen" / "telemetry.log"),
            "--telemetry-log-prompts",
        ),
        prompt_mode="flag",
        prompt_flag="-p",
        model_flag="--model",
        media_injection="symbolic",
    )

    def configure(self) -> Optional[str]:
        tavily_key = os.environ.get("TAVILY_API_KEY")
        google_key = os.environ.get("CAKIT_QWEN_GOOGLE_API_KEY")
        google_se_id = os.environ.get("GOOGLE_SEARCH_ENGINE_ID")
        providers = [{"type": "dashscope"}]
        default_provider = "dashscope"
        if tavily_key:
            providers.append({"type": "tavily", "apiKey": tavily_key})
            default_provider = "tavily"
        if google_key and google_se_id:
            providers.append({"type": "google", "apiKey": google_key, "searchEngineId": google_se_id})
            if default_provider == "dashscope":
                default_provider = "google"
        settings = {
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
        path = Path.home() / ".qwen" / "settings.json"
        self._write_text(path, json.dumps(settings, ensure_ascii=True, indent=2))
        return str(path)

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        images = images or []
        videos = videos or []

        telemetry_path = str(Path.home() / ".qwen" / "telemetry.log")
        qwen_key = self._resolve_openai_api_key("QWEN_OPENAI_API_KEY")
        qwen_base = self._resolve_openai_base_url("QWEN_OPENAI_BASE_URL")
        qwen_model = self._resolve_openai_model("QWEN_OPENAI_MODEL", model_override=model_override)
        qwen_google_api_key = os.environ.get("CAKIT_QWEN_GOOGLE_API_KEY")
        env = {
            "OPENAI_API_KEY": qwen_key,
            "OPENAI_BASE_URL": qwen_base,
            "OPENAI_MODEL": qwen_model,
            "TAVILY_API_KEY": os.environ.get("TAVILY_API_KEY"),
            "GOOGLE_API_KEY": qwen_google_api_key,
            "GOOGLE_SEARCH_ENGINE_ID": os.environ.get("GOOGLE_SEARCH_ENGINE_ID"),
        }
        extra_args: list[str] = []
        if qwen_key:
            extra_args.extend(["--auth-type", "openai"])
        cmd, _ = self._build_templated_command(
            template=self.run_template,
            prompt=prompt,
            model=qwen_model,
            images=images,
            videos=videos,
            extra_args=extra_args,
        )

        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        payload = self._parse_output_json(output)
        jsonl_payloads = self._selected_dicts(payload, "$[*]")
        artifacts = self._build_stats_artifacts(
            raw_output=output,
            json_payload=payload,
            jsonl_payloads=jsonl_payloads,
        )
        stats = self._merge_stats_snapshots(
            snapshots=[
                extract_json_result_stats(
                    artifacts,
                    inner=extract_gemini_style_stats,
                ),
                extract_jsonl_stats(artifacts),
            ],
            strategy="fallback",
        )
        response = (
            req_str(payload, "$.result")
            or self._first_selected_text(
                payload,
                (
                    '$[?(@.type == "result")].result',
                    '$[?(@.type == "assistant")].message.content[?(@.type == "text")].text',
                ),
            )
        )
        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=stats.models_usage,
            llm_calls=stats.llm_calls,
            tool_calls=stats.tool_calls,
            total_cost=stats.total_cost,
            telemetry_log=telemetry_path,
        )
