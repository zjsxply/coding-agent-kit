from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class TraeOssAgent(CodingAgent):
    name = "trae-oss"
    display_name = "Trae Agent (OSS)"
    binary = "trae-cli"

    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        result = self._run(["trae-cli", "--version"])
        return result.exit_code == 0 and bool(result.output.strip())

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        del scope
        commit = version.strip() if version and version.strip() else os.environ.get("CAKIT_TRAE_OSS_COMMIT")
        url = "git+https://github.com/bytedance/trae-agent.git"
        if commit:
            url = f"{url}@{commit}"
        result = self._uv_tool_install(
            url,
            python_version="3.12",
            force=True,
            with_packages=["docker", "pexpect", "unidiff"],
            fallback_no_cache_dir=True,
        )
        config_path = self.configure()
        ok = result.exit_code == 0
        return InstallResult(
            agent=self.name,
            version=self.get_version(),
            ok=ok,
            details=result.output,
            config_path=config_path,
        )

    def configure(self) -> Optional[str]:
        api_key = os.environ.get("TRAE_AGENT_API_KEY")
        api_base = os.environ.get("TRAE_AGENT_API_BASE")
        model = os.environ.get("TRAE_AGENT_MODEL")
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
        env = {
            "TRAE_AGENT_API_KEY": os.environ.get("TRAE_AGENT_API_KEY"),
            "TRAE_AGENT_API_BASE": os.environ.get("TRAE_AGENT_API_BASE"),
            "OPENAI_API_KEY": os.environ.get("TRAE_AGENT_API_KEY"),
            "OPENAI_API_BASE": os.environ.get("TRAE_AGENT_API_BASE"),
            "OPENAI_BASE_URL": os.environ.get("TRAE_AGENT_API_BASE"),
        }
        traj_env = os.environ.get("CAKIT_TRAE_TRAJECTORY")
        if traj_env:
            trajectory_file = Path(traj_env).expanduser()
        else:
            trajectory_file = self.workdir / "trae_trajectory.json"
        cmd = [
            "trae-cli",
            "run",
            prompt,
            "--working-dir",
            str(self.workdir),
            "--trajectory-file",
            str(trajectory_file),
        ]
        config_path = Path.home() / ".config" / "trae" / "config.yaml"
        if config_path.exists():
            cmd.extend(["--config-file", str(config_path)])
        model = model_override or os.environ.get("TRAE_AGENT_MODEL")
        if isinstance(model, str):
            model = model.strip() or None
        if model:
            cmd.extend(["--model", model])
        result = self._run(cmd, env, base_env=base_env)
        output = result.output

        trajectory_payload = self._load_trajectory_payload(trajectory_file)
        usage = self._extract_usage_from_trajectory(trajectory_payload)
        model_name = self._extract_model_name_from_trajectory(trajectory_payload)

        output_path = self._write_output(self.name, output)
        trajectory_content = self._format_trajectory_trace(
            trajectory_file=trajectory_file,
            raw_output=output,
            output_path=output_path,
        )
        trajectory_path = self._write_trajectory(self.name, trajectory_content)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=self._ensure_models_usage({}, usage, model_name),
            tool_calls=self._extract_tool_calls_from_trajectory(trajectory_payload),
            llm_calls=self._extract_llm_calls_from_trajectory(trajectory_payload),
            response=self._extract_response(output, trajectory_payload),
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        return self._version_text(["trae-cli", "--version"])

    def _load_trajectory_payload(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if isinstance(data, dict):
            return data
        return None

    def _extract_usage_from_trajectory(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        interactions = self._extract_llm_interactions(payload)
        if interactions is None:
            return None
        prompt_tokens = 0
        completion_tokens = 0
        for interaction in interactions:
            response = interaction.get("response")
            if not isinstance(response, dict):
                return None
            usage = response.get("usage")
            if not isinstance(usage, dict):
                return None
            input_tokens = self._as_int(usage.get("input_tokens"))
            output_tokens = self._as_int(usage.get("output_tokens"))
            if input_tokens is None or output_tokens is None:
                return None
            prompt_tokens += input_tokens
            completion_tokens += output_tokens
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def _extract_llm_calls_from_trajectory(self, payload: Optional[Dict[str, Any]]) -> Optional[int]:
        interactions = self._extract_llm_interactions(payload)
        if interactions is None:
            return None
        return len(interactions)

    def _extract_tool_calls_from_trajectory(self, payload: Optional[Dict[str, Any]]) -> Optional[int]:
        if not isinstance(payload, dict):
            return None
        agent_steps = payload.get("agent_steps")
        if not isinstance(agent_steps, list):
            return None
        total = 0
        for step in agent_steps:
            if not isinstance(step, dict):
                return None
            tool_calls = step.get("tool_calls")
            if tool_calls is None:
                continue
            if not isinstance(tool_calls, list):
                return None
            total += len(tool_calls)
        return total

    def _extract_llm_interactions(self, payload: Optional[Dict[str, Any]]) -> Optional[list[Dict[str, Any]]]:
        if not isinstance(payload, dict):
            return None
        llm_interactions = payload.get("llm_interactions")
        if not isinstance(llm_interactions, list) or not llm_interactions:
            return None
        interactions: list[Dict[str, Any]] = []
        for item in llm_interactions:
            if not isinstance(item, dict):
                return None
            interactions.append(item)
        return interactions

    def _extract_response(self, output: str, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        response = self._extract_response_from_trajectory(payload)
        if response:
            return response
        stdout = self._stdout_only(output)
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if lines:
            return lines[-1]
        return None

    def _extract_response_from_trajectory(self, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(payload, dict):
            return None

        final_result = payload.get("final_result")
        if isinstance(final_result, str):
            cleaned = final_result.strip()
            if cleaned:
                return cleaned

        interactions = payload.get("llm_interactions")
        if not isinstance(interactions, list):
            return None
        for interaction in reversed(interactions):
            if not isinstance(interaction, dict):
                continue
            response = interaction.get("response")
            if not isinstance(response, dict):
                continue
            content = response.get("content")
            if not isinstance(content, str):
                continue
            cleaned = content.strip()
            if cleaned:
                return cleaned
        return None

    def _extract_model_name_from_trajectory(self, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        model_name = payload.get("model")
        if not isinstance(model_name, str):
            return None
        cleaned = model_name.strip()
        if not cleaned:
            return None
        return cleaned

    def _format_trajectory_trace(self, *, trajectory_file: Path, raw_output: str, output_path: Path) -> str:
        if trajectory_file.exists():
            try:
                trajectory_raw = trajectory_file.read_text(encoding="utf-8")
            except Exception:
                trajectory_raw = ""
            if trajectory_raw.strip():
                return format_trace_text(trajectory_raw, source=str(trajectory_file))
        return format_trace_text(raw_output, source=str(output_path))
