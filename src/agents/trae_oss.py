from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodeAgent
from ..models import InstallResult, RunResult


class TraeOssAgent(CodeAgent):
    name = "trae-oss"
    display_name = "Trae Agent (OSS)"
    binary = "trae-cli"

    def install(self, *, scope: str = "user") -> InstallResult:
        commit = os.environ.get("TRAE_AGENT_COMMIT")
        url = "git+https://github.com/bytedance/trae-agent.git"
        if commit:
            url = f"{url}@{commit}"
        result = self._run(["python", "-m", "pip", "install", "--no-cache-dir", url])
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
        model = os.environ.get("TRAE_AGENT_MODEL") or "gpt-4.1"

        def yaml_quote(value: Optional[str]) -> str:
            return json.dumps(value or "")

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
            "    max_tokens: 4096\n"
            "    temperature: 0.2\n"
            "    top_p: 1.0\n"
            "    top_k: 0\n"
            "    parallel_tool_calls: false\n"
            "    max_retries: 3\n"
        )
        path = Path.home() / ".config" / "trae" / "config.yaml"
        self._write_text(path, config)
        return str(path)

    def run(self, prompt: str, images: Optional[list[Path]] = None) -> RunResult:
        images = images or []
        if images:
            message = "image input is not supported for trae-oss in cakit run."
            output_path = self._write_output(self.name, message)
            return RunResult(
                agent=self.name,
                agent_version=self.get_version(),
                runtime_seconds=0.0,
                models_usage={},
                tool_calls=None,
                response=message,
                exit_code=2,
                output_path=str(output_path),
                raw_output=message,
            )
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
        result = self._run(cmd, env)
        output = result.output
        usage, tool_calls = self._parse_trajectory(trajectory_file)
        output_path = self._write_output(self.name, output)
        model_name = os.environ.get("TRAE_AGENT_MODEL") or "gpt-4.1"
        models_usage = self._ensure_models_usage({}, usage, model_name)
        response = self._extract_response(output, trajectory_file)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            response=response,
            exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["trae-cli", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _parse_trajectory(self, path: Path) -> Tuple[Optional[Dict[str, int]], Optional[int]]:
        if not path.exists():
            return None, None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None, None
        usage = self._find_usage(data)
        tool_calls = self._count_actions(data)
        return usage, tool_calls

    def _extract_response(self, output: str, trajectory_file: Path) -> Optional[str]:
        response = self._extract_response_from_trajectory(trajectory_file)
        if response:
            return response
        if output:
            stdout = output
            marker = "----- STDERR -----"
            if marker in stdout:
                stdout = stdout.split(marker, 1)[0]
            lines = [line.strip() for line in stdout.splitlines() if line.strip()]
            if lines:
                return lines[-1]
        return None

    def _extract_response_from_trajectory(self, path: Path) -> Optional[str]:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

        def scan_steps(items: Any) -> Optional[str]:
            if not isinstance(items, list):
                return None
            for item in reversed(items):
                if not isinstance(item, dict):
                    continue
                for key in ("final_response", "final_answer", "answer", "output", "response"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                if item.get("role") == "assistant":
                    value = item.get("content") or item.get("message") or item.get("text")
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            return None

        for key in ("trajectory", "steps", "messages", "actions"):
            candidate = scan_steps(data.get(key))
            if candidate:
                return candidate
        return None

    def _find_usage(self, payload: Any) -> Optional[Dict[str, int]]:
        if not isinstance(payload, dict):
            return None
        if "usage" in payload and isinstance(payload["usage"], dict):
            return self._normalize_usage(payload["usage"])
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
            if key in payload:
                return self._normalize_usage(payload)
        for value in payload.values():
            if isinstance(value, dict):
                nested = self._find_usage(value)
                if nested:
                    return nested
            if isinstance(value, list):
                for item in value:
                    nested = self._find_usage(item)
                    if nested:
                        return nested
        return None

    def _normalize_usage(self, raw: Dict[str, Any]) -> Dict[str, int]:
        prompt = self._as_int(raw.get("prompt_tokens"))
        completion = self._as_int(raw.get("completion_tokens"))
        total = self._as_int(raw.get("total_tokens"))
        if prompt is None and "input_tokens" in raw:
            prompt = self._as_int(raw.get("input_tokens"))
        if completion is None and "output_tokens" in raw:
            completion = self._as_int(raw.get("output_tokens"))
        if total is None:
            total = (prompt or 0) + (completion or 0)
        return {
            "prompt_tokens": prompt or 0,
            "completion_tokens": completion or 0,
            "total_tokens": total or 0,
        }

    def _count_actions(self, data: Dict[str, Any]) -> Optional[int]:
        for key in ("trajectory", "steps", "actions"):
            value = data.get(key)
            if isinstance(value, list):
                return sum(1 for item in value if isinstance(item, dict) and ("action" in item or "tool" in item))
        return None

    def _usage_totals(self, usage: Optional[Dict[str, int]]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        if not usage:
            return None, None, None
        return (
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
        )

    @staticmethod
    def _as_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except Exception:
            return None
