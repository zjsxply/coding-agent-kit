from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent, InstallStrategy, RunCommandTemplate
from ..models import RunResult
from ..stats_extract import parse_usage_by_model, req_str, select_values
from ..utils import format_trace_text


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
        api_key = self._resolve_openai_api_key("TRAE_AGENT_API_KEY")
        api_base = self._resolve_openai_base_url("TRAE_AGENT_API_BASE")
        model = self._resolve_openai_model("TRAE_AGENT_MODEL")
        if not api_key or not api_base or not model:
            return None

        def yaml_quote(value: Optional[str]) -> str:
            return json.dumps(value)

        config = (
            "agents:\n"
            "  trae_agent:\n"
            "    enable_lakeview: false\n"
            "    model: trae_agent_model\n"
            "    max_steps: 200\n"
            "    tools:\n"
            "      - bash\n"
            "      - str_replace_based_edit_tool\n"
            "      - sequentialthinking\n"
            "      - task_done\n"
            "model_providers:\n"
            "  custom:\n"
            f"    api_key: {yaml_quote(api_key)}\n"
            "    provider: openai\n"
            f"    base_url: {yaml_quote(api_base)}\n"
            "models:\n"
            "  trae_agent_model:\n"
            "    model_provider: custom\n"
            f"    model: {yaml_quote(model)}\n"
        )
        path = Path.home() / ".config" / "trae" / "config.yaml"
        self._write_text(path, config)
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
        api_key = self._resolve_openai_api_key("TRAE_AGENT_API_KEY")
        api_base = self._resolve_openai_base_url("TRAE_AGENT_API_BASE")
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
            trajectory_file = self.workdir / "trae_trajectory.json"
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
        model = self._resolve_openai_model("TRAE_AGENT_MODEL", model_override=model_override)
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=prompt,
            model=model,
            extra_args=extra_args,
        )
        result = self._run(cmd, env, base_env=base_env)
        output = result.output

        trajectory_payload = self._load_json_dict(trajectory_file)
        model_name, usage, llm_calls, tool_calls, response = self._extract_trajectory_stats(trajectory_payload)
        snapshot = self._build_single_model_stats_snapshot(
            model_name=model_name,
            usage=usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=None,
        )

        trajectory_raw = self._read_text(trajectory_file) if trajectory_file.exists() else None
        if trajectory_raw and trajectory_raw.strip():
            trajectory_content = format_trace_text(trajectory_raw, source=str(trajectory_file))
        else:
            trajectory_content = format_trace_text(output, source=str(trajectory_file))
        return self.finalize_run(
            command_result=result,
            response=response or self._last_stdout_line(output),
            models_usage=snapshot.models_usage if snapshot is not None else {},
            llm_calls=snapshot.llm_calls if snapshot is not None else None,
            tool_calls=snapshot.tool_calls if snapshot is not None else None,
            trajectory_content=trajectory_content,
        )

    def _extract_trajectory_stats(
        self, payload: Optional[Dict[str, Any]]
    ) -> tuple[Optional[str], Optional[Dict[str, int]], Optional[int], Optional[int], Optional[str]]:
        model_name = req_str(payload, "$.model")

        llm_calls = self._count_selected(payload, "$.llm_interactions[*]")

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
        usage = self._sum_usage_entries(parsed_usages)

        tool_calls = self._count_selected(payload, "$.agent_steps[*].tool_calls[*]")

        response = self._first_selected_text(
            payload,
            (
                "$.final_result",
                "$.llm_interactions[*].response.content",
            ),
        )
        return model_name, usage, llm_calls, tool_calls, response
