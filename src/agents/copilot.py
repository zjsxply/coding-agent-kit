from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..agent_runtime import parsing as runtime_parsing
from .base import (
    CodingAgent,
    InstallStrategy,
    RunParseResult,
    RunPlan,
    RunCommandTemplate,
)
from ..stats_extract import (
    JsonlStatsSpec,
    StatsArtifacts,
    extract_jsonl_stats,
    last_value,
    merge_stats_snapshots,
)


class CopilotAgent(CodingAgent):
    name = "copilot"
    display_name = "GitHub Copilot CLI"
    binary = "copilot"
    supports_images = True
    supports_videos = False
    install_strategy = InstallStrategy(kind="npm", package="@github/copilot")
    run_template = RunCommandTemplate(
        base_args=("--yolo", "--no-ask-user", "--log-level", "debug"),
        prompt_mode="flag",
        prompt_flag="--prompt",
        model_flag="--model",
        media_injection="natural",
        media_tool_name="view",
    )
    _LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[^ ]+\s+\[[A-Z]+\]\s?(.*)$")

    def _build_run_plan(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> Optional[RunPlan]:
        output_root = os.environ.get("CAKIT_OUTPUT_DIR")
        base_output = Path(output_root) if output_root else Path.home() / ".cache" / "cakit"
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
        log_dir = base_output / "copilot-logs" / stamp
        log_dir.mkdir(parents=True, exist_ok=True)
        model = model_override or os.environ.get("COPILOT_MODEL")
        env = {
            "GH_TOKEN": os.environ.get("GH_TOKEN"),
            "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN"),
        }
        return self._build_templated_run_plan(
            prompt=prompt,
            model=model,
            env=env,
            images=images,
            videos=None,
            template=self.run_template,
            extra_args=["--log-dir", str(log_dir)],
            parse_output=lambda output, command_result: self._parse_pipeline_output(
                output,
                command_result,
                log_dir=log_dir,
            ),
        )

    def _parse_pipeline_output(
        self,
        output: str,
        command_result: Any,
        *,
        log_dir: Path,
    ) -> RunParseResult:
        model_calls: List[Dict[str, Any]] = []
        if log_dir.exists():
            for path in sorted(log_dir.glob("process-*.log")):
                model_calls.extend(self._parse_process_log(path))
        artifacts = StatsArtifacts(
            raw_output=output,
            jsonl_payloads=tuple(model_calls),
        )
        stats_spec = JsonlStatsSpec(
            payload_filter_paths=(
                '$[?(@.object == "chat.completion")]',
                '$[?(@.model != null)]',
                '$[?(@.usage != null)]',
                '$[?(@.choices != null)]',
            ),
            tool_calls_path="$[*].choices[*].message.tool_calls[*]",
        )
        stats = merge_stats_snapshots(
            snapshots=[
                extract_jsonl_stats(
                    artifacts,
                    spec=stats_spec,
                ),
            ]
        )
        response: Optional[str] = None
        for payload in reversed(model_calls):
            cleaned = runtime_parsing.normalize_text(last_value(payload, "$.choices[*].message.content"))
            if cleaned is not None:
                response = cleaned
                break
        if response is None:
            response = runtime_parsing.normalize_text(runtime_parsing.stdout_only(output))
        return RunParseResult(
            response=response,
            models_usage=stats.models_usage,
            llm_calls=stats.llm_calls,
            tool_calls=stats.tool_calls,
            total_cost=stats.total_cost,
            telemetry_log=str(log_dir),
        )

    def _parse_process_log(self, path: Path) -> List[Dict[str, Any]]:
        payloads: List[Dict[str, Any]] = []
        decoder = json.JSONDecoder()
        data_lines: Optional[List[str]] = None
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as file:
                for raw_line in file:
                    message = self._LOG_LINE_RE.sub(r"\1", raw_line.rstrip("\r\n"))
                    if message.strip() == "data:":
                        parsed = self._decode_data_block(data_lines, decoder)
                        if parsed is not None:
                            payloads.append(parsed)
                        data_lines = []
                        continue
                    if data_lines is not None:
                        data_lines.append(message)
        except Exception:
            return payloads

        parsed = self._decode_data_block(data_lines, decoder)
        if parsed is not None:
            payloads.append(parsed)
        return payloads

    @staticmethod
    def _decode_data_block(lines: Optional[List[str]], decoder: json.JSONDecoder) -> Optional[Dict[str, Any]]:
        if not lines:
            return None
        raw_block = "\n".join(lines).lstrip()
        if not raw_block.startswith("{"):
            return None
        try:
            payload, _ = decoder.raw_decode(raw_block)
        except Exception:
            return None
        if isinstance(payload, dict):
            return payload
        return None
