from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from .base import (
    CodingAgent,
    InstallStrategy,
    RunParseResult,
    RunPlan,
    RunCommandTemplate,
    VersionCommandTemplate,
)
from ..agent_runtime import parsing as runtime_parsing
from ..stats_extract import (
    StatsArtifacts,
    extract_gemini_style_stats,
    extract_json_result_stats,
    merge_stats_snapshots,
    select_values,
)


class AuggieAgent(CodingAgent):
    name = "auggie"
    display_name = "Auggie"
    binary = "auggie"
    supports_images = True
    supports_videos = False
    install_strategy = InstallStrategy(kind="npm", package="@augmentcode/auggie")
    version_template = VersionCommandTemplate(
        args=("auggie", "--version"),
        parse_mode="regex_first_line",
        regex=r"^([0-9]+(?:\.[0-9]+)*)\b",
    )
    run_template = RunCommandTemplate(
        base_args=("--print", "--quiet", "--output-format", "json"),
        prompt_mode="flag",
        prompt_flag="--instruction",
        model_flag="--model",
        media_injection="none",
    )

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
        log_dir = self._make_temp_dir(prefix="cakit-auggie-", keep=True)
        log_path = log_dir / "auggie.log"

        requested_model = runtime_parsing.normalize_text(model_override or os.environ.get("CAKIT_AUGGIE_MODEL"))
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
        for image in images:
            extra_args.extend(["--image", str(image)])
        return self._build_templated_run_plan(
            prompt=prompt,
            model=requested_model,
            env=env,
            extra_args=extra_args,
            template=template,
            parse_output=lambda output, command_result: self._parse_pipeline_output(
                output,
                log_path=log_path,
            ),
        )

    def _parse_pipeline_output(self, output: str, *, log_path: Path) -> RunParseResult:
        payloads = runtime_parsing.load_output_json_payloads(output)
        artifacts = StatsArtifacts(
            raw_output=output,
            jsonl_payloads=tuple(payloads),
        )
        stats = merge_stats_snapshots(
            snapshots=[
                extract_json_result_stats(
                    artifacts,
                    inner=extract_gemini_style_stats,
                ),
            ]
        )
        response = runtime_parsing.last_nonempty_text(select_values(payloads, '$[?(@.type == "result")].result'))
        return RunParseResult(
            response=response,
            models_usage=stats.models_usage,
            llm_calls=stats.llm_calls,
            tool_calls=stats.tool_calls,
            total_cost=stats.total_cost,
            telemetry_log=str(log_path),
        )
