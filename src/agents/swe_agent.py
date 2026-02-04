from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodeAgent
from ..models import InstallResult, RunResult


class SweAgent(CodeAgent):
    name = "swe-agent"
    display_name = "SWE-agent"
    binary = "sweagent"

    def install(self, *, scope: str = "user") -> InstallResult:
        version = self._resolve_version()
        url = f"https://github.com/SWE-agent/SWE-agent/archive/refs/tags/{version}.tar.gz"
        result = self._run(["python", "-m", "pip", "install", "--no-cache-dir", url])
        config_path = self.configure()
        ok = result.exit_code == 0
        return InstallResult(
            agent=self.name,
            version=version,
            ok=ok,
            details=result.output,
            config_path=config_path,
        )

    def configure(self) -> Optional[str]:
        config = (
            "agent:\n"
            "  templates:\n"
            "    system_template: |-\n"
            "      You are a helpful assistant that can interact with a computer to solve tasks.\n"
            "    instance_template: |-\n"
            "      {{problem_statement}}\n"
        )
        path = Path.home() / ".config" / "sweagent" / "config.yaml"
        self._write_text(path, config)
        return str(path)

    def run(self, prompt: str, images: Optional[list[Path]] = None) -> RunResult:
        images = images or []
        if images:
            message = "image input is not supported for swe-agent in cakit run."
            output_path = self._write_output(self.name, message)
            return RunResult(
                agent=self.name,
                agent_version=self.get_version(),
                runtime_seconds=0.0,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                models_usage={},
                tool_calls=None,
                exit_code=2,
                output_path=str(output_path),
                raw_output=message,
            )
        env = {
            "SWE_AGENT_API_KEY": os.environ.get("SWE_AGENT_API_KEY"),
            "SWE_AGENT_API_BASE": os.environ.get("SWE_AGENT_API_BASE"),
            "OPENAI_API_KEY": os.environ.get("SWE_AGENT_API_KEY"),
            "OPENAI_API_BASE": os.environ.get("SWE_AGENT_API_BASE"),
            "OPENAI_BASE_URL": os.environ.get("SWE_AGENT_API_BASE"),
        }
        model = os.environ.get("SWE_AGENT_MODEL")
        output_dir = Path(tempfile.mkdtemp(prefix="cakit-sweagent-"))
        cmd = [
            "sweagent",
            "run",
            "--env.repo.type=local",
            f"--env.repo.path={self.workdir}",
            "--problem_statement.text",
            prompt,
            f"--output_dir={output_dir}",
        ]
        if model:
            cmd.extend(["--agent.model.name", model])
        result = self._run(cmd, env)
        output = result.output
        if result.exit_code != 0 and "--output_dir" in output and "unrecognized" in output:
            cmd = [
                "sweagent",
                "run",
                "--env.repo.type=local",
                f"--env.repo.path={self.workdir}",
                "--problem_statement.text",
                prompt,
            ]
            if model:
                cmd.extend(["--agent.model.name", model])
            result = self._run(cmd, env)
            output = result.output
        usage = self._extract_usage_from_output(output)
        tool_calls = self._count_tool_calls_from_text(output)
        trajectory_usage, trajectory_tool_calls = self._parse_trajectory(output_dir)
        if trajectory_usage:
            usage = trajectory_usage
        if trajectory_tool_calls is not None:
            tool_calls = trajectory_tool_calls
        output_path = self._write_output(self.name, output)
        prompt_tokens, completion_tokens, total_tokens = self._usage_totals(usage)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            models_usage={},
            tool_calls=tool_calls,
            exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["sweagent", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _resolve_version(self) -> str:
        configured = os.environ.get("SWE_AGENT_VERSION")
        if configured:
            return configured
        url = "https://api.github.com/repos/SWE-agent/SWE-agent/releases/latest"
        request = urllib.request.Request(url, headers=self._github_headers())
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        tag = (payload.get("tag_name") or "").strip()
        if not tag:
            raise RuntimeError("Failed to resolve latest SWE-agent release tag from GitHub.")
        os.environ["SWE_AGENT_VERSION"] = tag
        return tag

    def _github_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _extract_usage_from_output(self, output: str) -> Optional[Dict[str, int]]:
        if not output:
            return None
        pattern = (
            r"usage=Usage\\(.*?completion_tokens=(\\d+).*?prompt_tokens=(\\d+).*?total_tokens=(\\d+)"
        )
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        found = False
        for match in re.finditer(pattern, output, re.DOTALL):
            completion = self._as_int(match.group(1)) or 0
            prompt = self._as_int(match.group(2)) or 0
            total = self._as_int(match.group(3))
            found = True
            totals["prompt_tokens"] += prompt
            totals["completion_tokens"] += completion
            totals["total_tokens"] += total if total is not None else prompt + completion
        if found:
            return totals
        return None

    def _count_tool_calls_from_text(self, output: str) -> Optional[int]:
        if not output:
            return None
        return len(re.findall(r"\bAction:|\bTool", output))

    def _parse_trajectory(self, output_dir: Path) -> Tuple[Optional[Dict[str, int]], Optional[int]]:
        if not output_dir.exists():
            return None, None
        traj_files = list(output_dir.rglob("*.traj")) + list(output_dir.rglob("*.json"))
        if not traj_files:
            return None, None
        traj_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for path in traj_files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            usage = self._find_usage(data)
            tool_calls = self._count_actions_in_trajectory(data)
            return usage, tool_calls
        return None, None

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

    def _count_actions_in_trajectory(self, data: Dict[str, Any]) -> Optional[int]:
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
