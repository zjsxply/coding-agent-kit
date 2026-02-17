from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class TraeCnAgent(CodingAgent):
    name = "trae-cn"
    display_name = "TRAE CLI (trae.cn)"
    binary = "traecli"
    _LATEST_VERSION_URL = "https://lf-cdn.trae.com.cn/obj/trae-com-cn/trae-cli/trae-cli_latest_version.txt"
    _DOWNLOAD_URL_TEMPLATE = (
        "https://lf-cdn.trae.com.cn/obj/trae-com-cn/trae-cli/trae-cli_{version}_{os_name}_{arch}.tar.gz"
    )

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        resolved_version, detail = self._resolve_install_version(version)
        if not resolved_version:
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details=detail,
                config_path=None,
            )
        os_name = self._detect_os_name()
        arch = self._detect_arch()
        if os_name is None or arch is None:
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details="unsupported platform for trae-cn install",
                config_path=None,
            )
        archive_version = resolved_version[1:] if resolved_version.startswith("v") else resolved_version
        download_url = self._DOWNLOAD_URL_TEMPLATE.format(version=archive_version, os_name=os_name, arch=arch)
        tmp_archive = Path("/tmp") / f"trae-cli_{archive_version}_{os_name}_{arch}.tar.gz"
        install_root = Path.home() / ".local" / "share" / "cakit" / "trae-cn" / resolved_version
        bin_dir = Path.home() / ".local" / "bin"
        bin_path = install_root / "trae-cli"
        detail_parts = [detail] if detail else []

        download = self._run(["curl", "-fsSL", "-o", str(tmp_archive), download_url])
        detail_parts.append(download.output)
        if download.exit_code != 0:
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details="\n".join(part for part in detail_parts if part),
                config_path=None,
            )

        install_root.mkdir(parents=True, exist_ok=True)
        extract = self._run(["tar", "-xzf", str(tmp_archive), "-C", str(install_root)])
        detail_parts.append(extract.output)
        if extract.exit_code != 0 or not bin_path.exists():
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details="\n".join(part for part in detail_parts if part),
                config_path=None,
            )

        bin_dir.mkdir(parents=True, exist_ok=True)
        symlink_path = bin_dir / "traecli"
        if symlink_path.exists() or symlink_path.is_symlink():
            symlink_path.unlink()
        symlink_path.symlink_to(bin_path)
        config_path = self.configure()
        return InstallResult(
            agent=self.name,
            version=self.get_version(),
            ok=True,
            details="\n".join(part for part in detail_parts if part),
            config_path=config_path,
        )

    def configure(self) -> Optional[str]:
        api_key = os.environ.get("CAKIT_TRAE_CN_API_KEY")
        base_url = os.environ.get("CAKIT_TRAE_CN_BASE_URL")
        model = os.environ.get("CAKIT_TRAE_CN_MODEL")
        model_name = os.environ.get("CAKIT_TRAE_CN_MODEL_NAME")
        by_azure_raw = os.environ.get("CAKIT_TRAE_CN_BY_AZURE")
        by_azure = bool(by_azure_raw and by_azure_raw.strip().lower() in {"1", "true", "yes", "on"})
        config = self._build_config_text(
            api_key=api_key,
            base_url=base_url,
            model=model,
            model_name=model_name,
            by_azure=by_azure,
        )
        if config is None:
            return None
        path = self._config_path()
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
        self._write_runtime_config(model_override)
        xdg_config_home = str(self._config_root())
        env = {
            "XDG_CONFIG_HOME": xdg_config_home,
        }
        cmd = [
            "traecli",
            "--print",
            "--json",
            "--yolo",
            prompt,
        ]
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        payload = self._extract_payload(output)
        usage = self._extract_usage(payload)
        model_name = self._extract_model_name(payload)
        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(
            self.name, format_trace_text(output, source=str(output_path))
        )
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=self._ensure_models_usage({}, usage, model_name),
            tool_calls=self._extract_tool_calls(payload),
            llm_calls=self._extract_llm_calls(payload),
            response=self._extract_response(payload, output),
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        first = self._version_first_line(["traecli", "--version"])
        if not first:
            return None
        match = re.search(r"version\s+([A-Za-z0-9._-]+)$", first)
        if match:
            return match.group(1)
        return None

    def _extract_payload(self, output: str) -> Optional[Dict[str, Any]]:
        parsed = self._extract_last_json_value(self._stdout_only(output))
        if isinstance(parsed, dict):
            return parsed
        return None

    def _extract_response(self, payload: Optional[Dict[str, Any]], output: str) -> Optional[str]:
        if isinstance(payload, dict):
            states = payload.get("agent_states")
            if isinstance(states, list):
                for state in reversed(states):
                    if not isinstance(state, dict):
                        continue
                    messages = state.get("messages")
                    if not isinstance(messages, list):
                        continue
                    for message in reversed(messages):
                        if not isinstance(message, dict):
                            continue
                        if message.get("role") != "assistant":
                            continue
                        content = message.get("content")
                        if isinstance(content, str) and content.strip():
                            return content.strip()
            error_text = payload.get("error")
            if isinstance(error_text, str) and error_text.strip():
                return error_text.strip()
        stdout = self._stdout_only(output)
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if lines:
            return lines[-1]
        return None

    def _extract_usage(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        if not isinstance(payload, dict):
            return None
        usage = payload.get("token_usage")
        if not isinstance(usage, dict):
            usage = self._extract_assistant_usage(payload)
        if not isinstance(usage, dict):
            return None
        prompt_tokens = self._as_int(usage.get("prompt_tokens"))
        completion_tokens = self._as_int(usage.get("completion_tokens"))
        total_tokens = self._as_int(usage.get("total_tokens"))
        if prompt_tokens is None or completion_tokens is None or total_tokens is None:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _extract_tool_calls(self, payload: Optional[Dict[str, Any]]) -> Optional[int]:
        if not isinstance(payload, dict):
            return None
        states = payload.get("agent_states")
        if not isinstance(states, list):
            return None
        total = 0
        for state in states:
            if not isinstance(state, dict):
                return None
            messages = state.get("messages")
            if not isinstance(messages, list):
                return None
            for message in messages:
                if not isinstance(message, dict):
                    return None
                tool_calls = message.get("tool_calls")
                if tool_calls is None:
                    continue
                if not isinstance(tool_calls, list):
                    return None
                total += len(tool_calls)
        return total

    def _extract_llm_calls(self, payload: Optional[Dict[str, Any]]) -> Optional[int]:
        if not isinstance(payload, dict):
            return None
        states = payload.get("agent_states")
        if not isinstance(states, list):
            return None
        total = 0
        for state in states:
            if not isinstance(state, dict):
                return None
            messages = state.get("messages")
            if not isinstance(messages, list):
                return None
            for message in messages:
                if not isinstance(message, dict):
                    return None
                if message.get("role") == "assistant":
                    total += 1
        return total

    def _extract_model_name(self, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        model = payload.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
        states = payload.get("agent_states")
        if not isinstance(states, list):
            return None
        for state in states:
            if not isinstance(state, dict):
                return None
            instruction = state.get("instruction")
            if not isinstance(instruction, list):
                continue
            for item in instruction:
                if not isinstance(item, dict):
                    return None
                content = item.get("content")
                if not isinstance(content, str):
                    continue
                match = re.search(r"underlying model is ([^\\.]+)\\.", content)
                if match:
                    candidate = match.group(1).strip()
                    if candidate:
                        return candidate
        return None

    def _extract_assistant_usage(self, payload: Dict[str, Any]) -> Optional[Dict[str, int]]:
        states = payload.get("agent_states")
        if not isinstance(states, list):
            return None
        for state in states:
            if not isinstance(state, dict):
                return None
            messages = state.get("messages")
            if not isinstance(messages, list):
                continue
            for message in reversed(messages):
                if not isinstance(message, dict):
                    return None
                if message.get("role") != "assistant":
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                prompt_tokens = self._as_int(usage.get("prompt_tokens"))
                completion_tokens = self._as_int(usage.get("completion_tokens"))
                total_tokens = self._as_int(usage.get("total_tokens"))
                if prompt_tokens is None or completion_tokens is None or total_tokens is None:
                    return None
                return {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                }
        return None

    def _resolve_install_version(self, version: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        if version and version.strip():
            normalized = version.strip()
            if not normalized.startswith("v"):
                normalized = f"v{normalized}"
            return normalized, None
        result = self._run(["curl", "-fsSL", self._LATEST_VERSION_URL])
        if result.exit_code != 0:
            return None, result.output
        latest = result.output.strip()
        if not latest:
            return None, "failed to resolve trae-cn latest version"
        if not latest.startswith("v"):
            latest = f"v{latest}"
        return latest, result.output

    @staticmethod
    def _detect_os_name() -> Optional[str]:
        raw = platform.system().strip().lower()
        if raw == "linux":
            return "linux"
        if raw == "darwin":
            return "darwin"
        return None

    @staticmethod
    def _detect_arch() -> Optional[str]:
        raw = platform.machine().strip().lower()
        if raw == "x86_64":
            return "amd64"
        if raw in {"aarch64", "arm64"}:
            return "arm64"
        return None

    def _config_root(self) -> Path:
        return Path.home() / ".config" / "cakit" / "trae-cn"

    def _config_path(self) -> Path:
        return self._config_root() / "trae_cli" / "trae_cli.yaml"

    def _write_runtime_config(self, model_override: Optional[str]) -> None:
        api_key = os.environ.get("CAKIT_TRAE_CN_API_KEY")
        base_url = os.environ.get("CAKIT_TRAE_CN_BASE_URL")
        model = model_override or os.environ.get("CAKIT_TRAE_CN_MODEL")
        model_name = os.environ.get("CAKIT_TRAE_CN_MODEL_NAME")
        by_azure_raw = os.environ.get("CAKIT_TRAE_CN_BY_AZURE")
        by_azure = bool(by_azure_raw and by_azure_raw.strip().lower() in {"1", "true", "yes", "on"})
        config = self._build_config_text(
            api_key=api_key,
            base_url=base_url,
            model=model,
            model_name=model_name,
            by_azure=by_azure,
        )
        if config is None:
            return
        self._write_text(self._config_path(), config)

    def _build_config_text(
        self,
        *,
        api_key: Optional[str],
        base_url: Optional[str],
        model: Optional[str],
        model_name: Optional[str],
        by_azure: bool,
    ) -> Optional[str]:
        required = [api_key, base_url, model]
        if any(not value for value in required):
            return None
        selected_name = model_name if isinstance(model_name, str) and model_name.strip() else "cakit-openai"
        return (
            "model:\n"
            f"  name: {json.dumps(selected_name)}\n"
            "models:\n"
            f"  - name: {json.dumps(selected_name)}\n"
            "    open_ai:\n"
            f"      base_url: {json.dumps(base_url)}\n"
            f"      api_key: {json.dumps(api_key)}\n"
            f"      model: {json.dumps(model)}\n"
            f"      by_azure: {'true' if by_azure else 'false'}\n"
        )
