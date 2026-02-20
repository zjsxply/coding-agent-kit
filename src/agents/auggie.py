from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from .base import (
    CodingAgent,
    InstallStrategy,
    RunCommandTemplate,
    extract_gemini_style_stats,
    extract_json_result_stats,
)
from ..models import RunResult


class AuggieAgent(CodingAgent):
    name = "auggie"
    display_name = "Auggie"
    binary = "auggie"
    supports_images = True
    supports_videos = False
    install_strategy = InstallStrategy(kind="npm", package="@augmentcode/auggie")
    run_template = RunCommandTemplate(
        base_args=("--print", "--quiet", "--output-format", "json"),
        prompt_mode="flag",
        prompt_flag="--instruction",
        model_flag="--model",
        media_injection="none",
    )

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
        log_dir = self._make_temp_dir(prefix="cakit-auggie-", keep=True)
        log_path = log_dir / "auggie.log"

        requested_model = self._normalize_text(model_override or os.environ.get("CAKIT_AUGGIE_MODEL"))
        env = {
            "AUGMENT_API_TOKEN": os.environ.get("AUGMENT_API_TOKEN"),
            "AUGMENT_API_URL": os.environ.get("AUGMENT_API_URL"),
            "AUGMENT_SESSION_AUTH": os.environ.get("AUGMENT_SESSION_AUTH"),
            "GITHUB_API_TOKEN": os.environ.get("GITHUB_API_TOKEN"),
            "AUGMENT_DISABLE_AUTO_UPDATE": "1",
        }
        template = self.run_template
        extra_args = [
            "--workspace-root",
            str(self.workdir),
            "--log-file",
            str(log_path),
            "--log-level",
            "debug",
        ]
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=prompt,
            model=requested_model,
            extra_args=extra_args,
        )
        for image in images:
            cmd.extend(["--image", str(image)])

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        payloads = self._load_output_json_payloads(output)
        artifacts = self._build_stats_artifacts(
            raw_output=output,
            jsonl_payloads=payloads,
        )
        stats = self._merge_stats_snapshots(
            snapshots=[
                extract_json_result_stats(
                    artifacts,
                    inner=extract_gemini_style_stats,
                ),
            ]
        )
        response = self._last_selected_text(payloads, '$[?(@.type == "result")].result')
        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=stats.models_usage,
            llm_calls=stats.llm_calls,
            tool_calls=stats.tool_calls,
            total_cost=stats.total_cost,
            telemetry_log=str(log_path),
        )
