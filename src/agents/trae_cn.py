from __future__ import annotations

import json
import os
import platform
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .base import (
    CodingAgent,
    CommandResult,
    InstallStrategy,
    RunCommandTemplate,
    VersionCommandTemplate,
)
from ..models import RunResult
from ..stats_extract import last_value, parse_usage_by_model, req_str, select_values


class TraeCnAgent(CodingAgent):
    name = "trae-cn"
    display_name = "TRAE CLI (trae.cn)"
    binary = "traecli"
    install_strategy = InstallStrategy(kind="custom")
    run_template = RunCommandTemplate(
        base_args=("--print", "--json", "--yolo"),
        prompt_mode="arg",
        prompt_flag=None,
        model_flag=None,
        media_injection="none",
    )
    version_template = VersionCommandTemplate(
        args=("traecli", "--version"),
        parse_mode="regex_first_line",
        regex=r"(?i)version\s+([A-Za-z0-9._-]+)$",
    )
    _LATEST_VERSION_URL = "https://lf-cdn.trae.com.cn/obj/trae-com-cn/trae-cli/trae-cli_latest_version.txt"
    _DOWNLOAD_URL_TEMPLATE = (
        "https://lf-cdn.trae.com.cn/obj/trae-com-cn/trae-cli/trae-cli_{version}_{os_name}_{arch}.tar.gz"
    )

    def _install_with_custom_strategy(
        self,
        *,
        strategy: InstallStrategy,
        scope: str,
        version: Optional[str],
    ) -> CommandResult:
        started = time.monotonic()
        resolved_version, detail = self._resolve_install_version(version)
        if not resolved_version:
            return CommandResult(
                exit_code=1,
                stdout=detail or "",
                stderr="failed to resolve trae-cn install version",
                duration_seconds=time.monotonic() - started,
            )
        raw_os = platform.system().strip().lower()
        if raw_os == "linux":
            os_name = "linux"
        elif raw_os == "darwin":
            os_name = "darwin"
        else:
            os_name = None
        raw_arch = platform.machine().strip().lower()
        if raw_arch == "x86_64":
            arch = "amd64"
        elif raw_arch in {"aarch64", "arm64"}:
            arch = "arm64"
        else:
            arch = None
        if os_name is None or arch is None:
            return CommandResult(
                exit_code=1,
                stdout=detail or "",
                stderr="unsupported platform for trae-cn install",
                duration_seconds=time.monotonic() - started,
            )
        archive_version = resolved_version[1:] if resolved_version.startswith("v") else resolved_version
        download_url = self._DOWNLOAD_URL_TEMPLATE.format(version=archive_version, os_name=os_name, arch=arch)
        install_root = Path.home() / ".local" / "share" / "cakit" / "trae-cn" / resolved_version
        bin_dir = Path.home() / ".local" / "bin"
        bin_path = install_root / "trae-cli"
        detail_parts = [detail] if detail else []

        with tempfile.TemporaryDirectory(prefix="cakit-trae-cn-") as temp_dir:
            tmp_archive = Path(temp_dir) / f"trae-cli_{archive_version}_{os_name}_{arch}.tar.gz"
            download = self._run(["curl", "-fsSL", "-o", str(tmp_archive), download_url])
            detail_parts.append(download.output)
            if download.exit_code != 0:
                return CommandResult(
                    exit_code=1,
                    stdout="\n".join(part for part in detail_parts if part),
                    stderr="failed to download trae-cn binary archive",
                    duration_seconds=time.monotonic() - started,
                )

            install_root.mkdir(parents=True, exist_ok=True)
            extract = self._run(["tar", "-xzf", str(tmp_archive), "-C", str(install_root)])
            detail_parts.append(extract.output)
            if extract.exit_code != 0 or not bin_path.exists():
                return CommandResult(
                    exit_code=1,
                    stdout="\n".join(part for part in detail_parts if part),
                    stderr="failed to extract trae-cn archive",
                    duration_seconds=time.monotonic() - started,
                )

        bin_dir.mkdir(parents=True, exist_ok=True)
        symlink_path = bin_dir / "traecli"
        if symlink_path.exists() or symlink_path.is_symlink():
            symlink_path.unlink()
        symlink_path.symlink_to(bin_path)
        return CommandResult(
            exit_code=0,
            stdout="\n".join(part for part in detail_parts if part),
            stderr="",
            duration_seconds=time.monotonic() - started,
        )

    def configure(self) -> Optional[str]:
        api_key = self._resolve_openai_api_key("CAKIT_TRAE_CN_API_KEY")
        base_url = self._resolve_openai_base_url("CAKIT_TRAE_CN_BASE_URL")
        model = self._resolve_openai_model("CAKIT_TRAE_CN_MODEL")
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
        template = self.run_template
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=prompt,
        )
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        payload = self._parse_output_json_object(output)
        response, usage, model_name, llm_calls, tool_calls = self._extract_payload_stats(payload)
        snapshot = self._build_single_model_stats_snapshot(
            model_name=model_name,
            usage=usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=None,
        )
        return self.finalize_run(
            command_result=result,
            response=response or self._last_stdout_line(output),
            models_usage=snapshot.models_usage if snapshot is not None else {},
            llm_calls=snapshot.llm_calls if snapshot is not None else None,
            tool_calls=snapshot.tool_calls if snapshot is not None else None,
        )

    def _extract_payload_stats(
        self, payload: Optional[Dict[str, Any]]
    ) -> tuple[Optional[str], Optional[Dict[str, int]], Optional[str], Optional[int], Optional[int]]:
        response = self._last_selected_text(
            payload,
            '$.agent_states[*].messages[?(@.role == "assistant")].content',
        )
        if response is None:
            response = req_str(payload, "$.error")

        usage = None
        top_level_usage = last_value(payload, "$.token_usage")
        if isinstance(top_level_usage, dict):
            usage = parse_usage_by_model(top_level_usage, "prompt_completion")
        if usage is None:
            assistant_usages = select_values(payload, '$.agent_states[*].messages[?(@.role == "assistant")].usage')
            for raw_usage in reversed(assistant_usages or []):
                if not isinstance(raw_usage, dict):
                    continue
                parsed_usage = parse_usage_by_model(raw_usage, "prompt_completion")
                if parsed_usage is not None:
                    usage = parsed_usage
                    break

        model_name = req_str(payload, "$.model")
        if model_name is None:
            instruction_contents = select_values(payload, "$.agent_states[*].instruction[*].content")
            for content in instruction_contents or []:
                if not isinstance(content, str):
                    continue
                match = re.search(r"underlying model is ([^\\.]+)\\.", content)
                if not match:
                    continue
                candidate = match.group(1).strip()
                if candidate:
                    model_name = candidate
                    break

        llm_calls = self._count_selected(payload, '$.agent_states[*].messages[?(@.role == "assistant")]')
        tool_calls = self._count_selected(payload, "$.agent_states[*].messages[*].tool_calls[*]")

        return response, usage, model_name, llm_calls, tool_calls

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

    def _config_root(self) -> Path:
        return Path.home() / ".config" / "cakit" / "trae-cn"

    def _config_path(self) -> Path:
        return self._config_root() / "trae_cli" / "trae_cli.yaml"

    def _write_runtime_config(self, model_override: Optional[str]) -> None:
        api_key = self._resolve_openai_api_key("CAKIT_TRAE_CN_API_KEY")
        base_url = self._resolve_openai_base_url("CAKIT_TRAE_CN_BASE_URL")
        model = self._resolve_openai_model("CAKIT_TRAE_CN_MODEL", model_override=model_override)
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
