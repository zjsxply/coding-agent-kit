from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .base import (
    CodingAgent,
    InstallStrategy,
    ParsedStats,
    RunCommandTemplate,
    RunParseResult,
    RunPlan,
)
from ..agent_runtime import command_exec as runtime_command
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

    def _config_path(self) -> Path:
        config_dir = self._resolve_writable_dir(
            Path.home() / ".config" / "trae",
            Path("/tmp") / "cakit" / "trae-oss-config",
            purpose="Trae OSS config",
        )
        return config_dir / "config.yaml"

    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        result = self._run(["trae-cli", "--version"])
        return result.exit_code == 0 and bool(result.output.strip())

    def get_version(self) -> Optional[str]:
        receipt_path = self._uv_receipt_path()
        if receipt_path is None:
            return None
        receipt_text = runtime_parsing.normalize_text(self._read_text(receipt_path))
        if receipt_text is None:
            return None
        match = re.search(r'git\s*=\s*"([^"]+)"', receipt_text)
        if match is None:
            return None
        query = parse_qs(urlparse(match.group(1)).query)
        revisions = query.get("rev")
        if not revisions:
            return None
        return runtime_parsing.normalize_text(revisions[-1])

    def configure(self) -> Optional[str]:
        api_key, api_base, model = self._resolve_runtime_settings()
        if not api_key or not api_base or not model:
            return None
        provider = self._resolve_model_provider(api_base)

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
                    "provider": provider,
                    "base_url": api_base,
                }
            },
            "models": {
                "trae_agent_model": {
                    "model_provider": "custom",
                    "model": model,
                    "max_tokens": 4096,
                    "temperature": 0.5,
                    "top_p": 1.0,
                    "top_k": 0,
                    "parallel_tool_calls": False,
                    "max_retries": 10,
                }
            },
        }
        path = self._config_path()
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
        if api_key and api_base and model:
            config_path = Path(self.configure() or self._config_path())
        else:
            config_path = self._config_path()
        env = {
            "TRAE_AGENT_API_KEY": api_key,
            "TRAE_AGENT_BASE_URL": api_base,
            "OPENAI_API_KEY": api_key,
            "OPENAI_BASE_URL": api_base,
        }
        provider = self._resolve_model_provider(api_base)
        if provider == "doubao":
            env["DOUBAO_API_KEY"] = api_key
            env["DOUBAO_BASE_URL"] = api_base
        elif provider == "openrouter":
            env["OPENROUTER_API_KEY"] = api_key
            env["OPENROUTER_BASE_URL"] = api_base
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
            response=parsed_stats.response or "",
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

        tool_call_entries = select_values(payload, "$.agent_steps[*].tool_calls")
        if tool_call_entries is None:
            tool_calls = None
        else:
            tool_calls = sum(len(entry) for entry in tool_call_entries if isinstance(entry, list))

        response = next(
            (
                text
                for text in (
                    runtime_parsing.last_nonempty_text(select_values(payload, path))
                    for path in (
                        "$.final_result",
                        "$.agent_steps[*].llm_response.content",
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
            runtime_env.resolve_openai_base_url("TRAE_AGENT_BASE_URL"),
            runtime_env.resolve_openai_model("TRAE_AGENT_MODEL", model_override=model_override),
        )

    def _resolve_model_provider(self, api_base: Optional[str]) -> str:
        configured = runtime_parsing.normalize_text(os.environ.get("CAKIT_TRAE_AGENT_PROVIDER"))
        if configured is not None:
            return configured
        if not api_base:
            return "openai"
        host = urlparse(api_base).netloc.lower()
        if host.endswith("openrouter.ai"):
            return "openrouter"
        if host.endswith("api.openai.com"):
            return "openai"
        return "doubao"

    def _uv_receipt_path(self) -> Optional[Path]:
        binary_path = runtime_command.resolve_binary(
            agent_name=self.name,
            binary=self.binary,
            npm_prefix=self._npm_prefix(),
            env_source=os.environ,
        )
        if binary_path is None:
            return None
        resolved_binary = Path(binary_path).expanduser().resolve()
        if resolved_binary.parent.name != "bin":
            return None
        return resolved_binary.parent.parent / "uv-receipt.toml"
