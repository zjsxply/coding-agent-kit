from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .base import (
    CodingAgent,
    InstallStrategy,
    ParsedStats,
    RunCommandTemplate,
    RunParseResult,
    RunPlan,
)
from ..stats_extract import (
    build_single_model_stats_snapshot,
    parse_usage_by_model,
    req_str,
    select_values,
    sum_usage_entries,
)
from ..agent_runtime import parsing as runtime_parsing
from ..agent_runtime import env as runtime_env
from ..agent_runtime import trajectory as runtime_trajectory
from ..io_helpers import dump_yaml


class TraeOssAgent(CodingAgent):
    name = "trae-oss"
    display_name = "Trae Agent (OSS)"
    binary = "trae-cli"
    install_strategy = InstallStrategy(
        kind="uv_tool",
        package="git+https://github.com/bytedance/trae-agent.git",
        version_style="git_ref",
        python_version="3.12",
        force=True,
        with_packages=("docker", "pexpect", "unidiff"),
        fallback_no_cache_dir=True,
    )
    run_template = RunCommandTemplate(
        base_args=("run",),
        prompt_mode="arg",
        prompt_flag=None,
        model_flag="--model",
        media_injection="none",
    )

    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        result = self._run(["trae-cli", "--version"])
        return result.exit_code == 0 and bool(result.output.strip())

    def configure(self) -> Optional[str]:
        api_key, api_base, model = self._resolve_runtime_settings()
        if not api_key or not api_base or not model:
            return None

        config = {
            "agents": {
                "trae_agent": {
                    "enable_lakeview": False,
                    "model": "trae_agent_model",
                    "max_steps": 200,
                    "tools": [
                        "bash",
                        "str_replace_based_edit_tool",
                        "sequentialthinking",
                        "task_done",
                    ],
                }
            },
            "model_providers": {
                "custom": {
                    "api_key": api_key,
                    "provider": "openai",
                    "base_url": api_base,
                }
            },
            "models": {
                "trae_agent_model": {
                    "model_provider": "custom",
                    "model": model,
                }
            },
        }
        path = Path.home() / ".config" / "trae" / "config.yaml"
        self._write_text(path, dump_yaml(config))
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
        api_key, api_base, model = self._resolve_runtime_settings(model_override=model_override)
        env = {
            "TRAE_AGENT_API_KEY": api_key,
            "TRAE_AGENT_API_BASE": api_base,
            "OPENAI_API_KEY": api_key,
            "OPENAI_API_BASE": api_base,
            "OPENAI_BASE_URL": api_base,
        }
        traj_env = os.environ.get("CAKIT_TRAE_TRAJECTORY")
        if traj_env:
            trajectory_file = Path(traj_env).expanduser()
        else:
            trajectory_file = Path(tempfile.gettempdir()) / f"cakit-trae-{uuid.uuid4().hex}.json"
        trajectory_file.parent.mkdir(parents=True, exist_ok=True)
        template = self.run_template
        extra_args = [
            "--working-dir",
            str(self.workdir),
            "--trajectory-file",
            str(trajectory_file),
        ]
        config_path = Path.home() / ".config" / "trae" / "config.yaml"
        if config_path.exists():
            extra_args.extend(["--config-file", str(config_path)])
        return self._build_templated_run_plan(
            prompt=prompt,
            model=model,
            env=env,
            extra_args=extra_args,
            template=template,
            parse_output=lambda output, command_result: self._parse_pipeline_output(
                output,
                command_result,
                trajectory_file=trajectory_file,
            ),
        )

    def _parse_pipeline_output(
        self,
        output: str,
        command_result: Any,
        *,
        trajectory_file: Path,
    ) -> RunParseResult:
        trajectory_payload = runtime_parsing.load_json_dict(trajectory_file)
        parsed_stats = self._extract_trajectory_stats(trajectory_payload)
        snapshot = build_single_model_stats_snapshot(
            model_name=parsed_stats.model_name,
            usage=parsed_stats.usage,
            llm_calls=parsed_stats.llm_calls,
            tool_calls=parsed_stats.tool_calls,
            total_cost=None,
        )
        trajectory_raw = self._read_text(trajectory_file) if trajectory_file.exists() else None
        trajectory_content = runtime_trajectory.build_trajectory_from_raw(
            raw_text=trajectory_raw,
            output=output,
            source=str(trajectory_file),
        )
        return RunParseResult(
            response=parsed_stats.response or runtime_parsing.last_stdout_line(output),
            models_usage=snapshot.models_usage if snapshot is not None else {},
            llm_calls=snapshot.llm_calls if snapshot is not None else None,
            tool_calls=snapshot.tool_calls if snapshot is not None else None,
            trajectory_content=trajectory_content,
        )

    def _extract_trajectory_stats(
        self,
        payload: Optional[Dict[str, Any]],
    ) -> ParsedStats:
        model_name = req_str(payload, "$.model")

        llm_call_values = select_values(payload, "$.llm_interactions[*]")
        llm_calls = len(llm_call_values) if llm_call_values is not None else None

        usage_values = select_values(payload, "$.llm_interactions[*].response.usage")
        parsed_usages = [
            parsed
            for parsed in (
                parse_usage_by_model(value, "input_output")
                for value in (usage_values or [])
                if isinstance(value, dict)
            )
            if parsed is not None
        ]
        usage = sum_usage_entries(parsed_usages)

        tool_call_values = select_values(payload, "$.agent_steps[*].tool_calls[*]")
        tool_calls = len(tool_call_values) if tool_call_values is not None else None

        response = next(
            (
                text
                for text in (
                    runtime_parsing.last_nonempty_text(select_values(payload, path))
                    for path in (
                        "$.final_result",
                        "$.llm_interactions[*].response.content",
                    )
                )
                if text is not None
            ),
            None,
        )
        return ParsedStats(
            model_name=model_name,
            usage=usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            response=response,
        )

    def _resolve_runtime_settings(
        self,
        *,
        model_override: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        return (
            runtime_env.resolve_openai_api_key("TRAE_AGENT_API_KEY"),
            runtime_env.resolve_openai_base_url("TRAE_AGENT_API_BASE"),
            runtime_env.resolve_openai_model("TRAE_AGENT_MODEL", model_override=model_override),
        )
