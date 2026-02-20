from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import CodingAgent, InstallStrategy, RunCommandTemplate, extract_jsonl_stats, last_value
from ..models import RunResult


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

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
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
        template = self.run_template
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=prompt,
            model=model,
            images=images,
            videos=None,
            extra_args=["--log-dir", str(log_dir)],
        )
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        model_calls: List[Dict[str, Any]] = []
        if log_dir.exists():
            for path in sorted(log_dir.glob("process-*.log")):
                log_text = self._read_text_lossy(path)
                if log_text is None:
                    continue
                lines = log_text.splitlines()
                messages = [self._LOG_LINE_RE.sub(r"\1", line) for line in lines]
                data_indices = [index for index, message in enumerate(messages) if message.strip() == "data:"]
                for current, start in enumerate(data_indices):
                    next_start = data_indices[current + 1] if current + 1 < len(data_indices) else len(messages)
                    raw_block = "\n".join(messages[start + 1 : next_start]).lstrip()
                    if not raw_block.startswith("{"):
                        continue
                    try:
                        payload, _ = json.JSONDecoder().raw_decode(raw_block)
                    except Exception:
                        continue
                    if isinstance(payload, dict):
                        model_calls.append(payload)
        artifacts = self._build_stats_artifacts(
            raw_output=output,
            jsonl_payloads=model_calls,
        )
        stats = self._merge_stats_snapshots(
            snapshots=[
                extract_jsonl_stats(
                    artifacts,
                    payload_filter_paths=(
                        '$[?(@.object == "chat.completion")]',
                        '$[?(@.model != null)]',
                        '$[?(@.usage != null)]',
                        '$[?(@.choices != null)]',
                    ),
                    tool_calls_path="$[*].choices[*].message.tool_calls[*]",
                ),
            ]
        )
        response: Optional[str] = None
        for payload in reversed(model_calls):
            cleaned = self._normalize_text(last_value(payload, "$.choices[*].message.content"))
            if cleaned is not None:
                response = cleaned
                break
        if response is None:
            response = self._normalize_text(self._stdout_only(output))
        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=stats.models_usage,
            llm_calls=stats.llm_calls,
            tool_calls=stats.tool_calls,
            total_cost=stats.total_cost,
            telemetry_log=str(log_dir),
        )
