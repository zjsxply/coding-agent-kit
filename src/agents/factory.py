from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import CodingAgent, CommandResult
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class FactoryAgent(CodingAgent):
    name = "factory"
    display_name = "Factory Droid CLI"
    binary = "droid"
    supports_images = True
    supports_videos = False

    _VERSION_RE = re.compile(r"^[A-Za-z0-9._-]+$")
    _BYOK_DISPLAY_NAME = "CAKIT BYOK"
    _BYOK_PROVIDER_VALUES = {
        "openai",
        "anthropic",
        "generic-chat-completion-api",
    }

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        del scope
        if version and version.strip():
            result = self._install_specific_version(version.strip())
        else:
            result = self._run(["bash", "-lc", "curl -fsSL https://app.factory.ai/cli | sh"])
        ok = result.exit_code == 0
        return InstallResult(
            agent=self.name,
            version=self.get_version() if ok else None,
            ok=ok,
            details=result.output,
            config_path=None,
        )

    def configure(self) -> Optional[str]:
        return None

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        del videos
        images = images or []
        run_prompt = prompt
        if images:
            run_prompt, _, _ = self._build_natural_media_prompt(
                prompt,
                images=images,
                videos=None,
                tool_name="Read",
            )

        selected_model = self._normalize_text(model_override) or self._normalize_text(os.environ.get("CAKIT_FACTORY_MODEL"))
        selected_model, byok_error = self._resolve_model_for_run(selected_model)
        if byok_error is not None:
            return self._build_error_run_result(message=byok_error, cakit_exit_code=1)

        env: Dict[str, str] = {
            "FACTORY_API_KEY": os.environ.get("FACTORY_API_KEY"),
            "FACTORY_API_BASE_URL": os.environ.get("FACTORY_API_BASE_URL"),
            "FACTORY_TOKEN": os.environ.get("FACTORY_TOKEN"),
            "FACTORY_LOG_FILE": os.environ.get("FACTORY_LOG_FILE"),
            "FACTORY_DISABLE_KEYRING": os.environ.get("FACTORY_DISABLE_KEYRING"),
        }

        cmd = [
            "droid",
            "exec",
            "--output-format",
            "json",
            "--cwd",
            str(self.workdir),
        ]
        if selected_model:
            cmd.extend(["--model", selected_model])
        if reasoning_effort:
            cmd.extend(["--reasoning-effort", reasoning_effort])
        cmd.append(run_prompt)

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        payload = self._parse_result_payload(output)
        session_id = self._extract_session_id(payload)
        usage = self._extract_usage(payload)
        model_name = self._extract_model_name(payload, session_id)

        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))

        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=self._ensure_models_usage({}, usage, model_name) if usage is not None and model_name else {},
            tool_calls=self._extract_tool_calls(session_id),
            llm_calls=self._extract_llm_calls(payload),
            total_cost=self._extract_total_cost(payload),
            telemetry_log=self._extract_telemetry_log(env),
            response=self._extract_response(payload, output),
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        return self._version_first_line(["droid", "--version"])

    def _parse_result_payload(self, output: str) -> Optional[Dict[str, Any]]:
        stdout = self._stdout_only(output).strip()
        if not stdout:
            return None
        last_value = self._extract_last_json_value(stdout)
        if not isinstance(last_value, dict):
            return None
        if last_value.get("type") != "result":
            return None
        return last_value

    def _extract_response(self, payload: Optional[Dict[str, Any]], output: str) -> Optional[str]:
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, str):
                cleaned = result.strip()
                if cleaned:
                    return cleaned
        stdout = self._stdout_only(output)
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if lines:
            return lines[-1]
        return None

    def _extract_usage(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        if not isinstance(payload, dict):
            return None
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None
        input_tokens = self._as_int(usage.get("input_tokens"))
        output_tokens = self._as_int(usage.get("output_tokens"))
        cache_read = self._as_int(usage.get("cache_read_input_tokens"))
        cache_creation = self._as_int(usage.get("cache_creation_input_tokens"))
        if None in {input_tokens, output_tokens, cache_read, cache_creation}:
            return None
        prompt_tokens = input_tokens + cache_read + cache_creation
        completion_tokens = output_tokens
        total_tokens = prompt_tokens + completion_tokens
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _extract_llm_calls(self, payload: Optional[Dict[str, Any]]) -> Optional[int]:
        if not isinstance(payload, dict):
            return None
        num_turns = self._as_int(payload.get("num_turns"))
        return num_turns

    def _extract_total_cost(self, payload: Optional[Dict[str, Any]]) -> Optional[float]:
        if not isinstance(payload, dict):
            return None
        for key in ("total_cost", "cost"):
            value = payload.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return float(value)
        return None

    def _extract_session_id(self, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        session_id = payload.get("session_id")
        if not isinstance(session_id, str):
            return None
        cleaned = session_id.strip()
        if not cleaned:
            return None
        return cleaned

    def _extract_model_name(self, payload: Optional[Dict[str, Any]], session_id: Optional[str]) -> Optional[str]:
        if isinstance(payload, dict):
            model = payload.get("model")
            if isinstance(model, str) and model.strip():
                return model.strip()
        settings_path = self._find_session_settings_file(session_id)
        if settings_path is None:
            return None
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        model = data.get("model")
        if not isinstance(model, str):
            return None
        cleaned = model.strip()
        if not cleaned:
            return None
        return cleaned

    def _extract_tool_calls(self, session_id: Optional[str]) -> Optional[int]:
        session_path = self._find_session_transcript_file(session_id)
        if session_path is None:
            return None
        count = 0
        tool_call_ids: set[str] = set()
        try:
            for raw_line in session_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    return None
                for item in self._iter_dicts(payload):
                    event_type = item.get("type")
                    if event_type == "tool_call":
                        event_id = item.get("id")
                        if isinstance(event_id, str) and event_id.strip():
                            tool_call_ids.add(event_id.strip())
                        else:
                            count += 1
                        continue
                    hook_event_name = item.get("hook_event_name")
                    if hook_event_name == "PreToolUse":
                        tool_name = item.get("tool_name")
                        if not isinstance(tool_name, str) or not tool_name.strip():
                            return None
                        count += 1
        except Exception:
            return None
        if tool_call_ids:
            count += len(tool_call_ids)
        return count

    def _extract_telemetry_log(self, env: Dict[str, str]) -> Optional[str]:
        explicit = env.get("FACTORY_LOG_FILE")
        if isinstance(explicit, str):
            cleaned = explicit.strip()
            if cleaned:
                return cleaned
        return None

    def _resolve_model_for_run(self, selected_model: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        model_name = self._normalize_text(selected_model)
        byok_api_key = self._resolve_openai_api_key("CAKIT_FACTORY_BYOK_API_KEY")
        byok_base_url = self._resolve_openai_base_url("CAKIT_FACTORY_BYOK_BASE_URL")
        byok_provider = self._normalize_text(os.environ.get("CAKIT_FACTORY_BYOK_PROVIDER"))

        byok_requested = any(value is not None for value in (byok_api_key, byok_base_url, byok_provider))
        if not byok_requested:
            return model_name, None

        if model_name is None:
            model_name = self._normalize_text(os.environ.get("OPENAI_DEFAULT_MODEL"))

        missing: list[tuple[str, str]] = []
        if byok_api_key is None:
            missing.append(("CAKIT_FACTORY_BYOK_API_KEY", "OPENAI_API_KEY"))
        if byok_base_url is None:
            missing.append(("CAKIT_FACTORY_BYOK_BASE_URL", "OPENAI_BASE_URL"))
        if model_name is None:
            missing.append(("CAKIT_FACTORY_MODEL", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return None, self._missing_env_with_fallback_message(missing)

        provider = self._resolve_byok_provider(base_url=byok_base_url, provider=byok_provider)
        if provider is None:
            return (
                None,
                "invalid CAKIT_FACTORY_BYOK_PROVIDER: expected one of "
                "openai, anthropic, generic-chat-completion-api",
            )

        custom_model_name = self._upsert_byok_model(
            model_name=model_name,
            api_key=byok_api_key,
            base_url=byok_base_url,
            provider=provider,
        )
        if custom_model_name is None:
            return None, "failed to write Factory BYOK settings at ~/.factory/settings.json"
        return custom_model_name, None

    def _resolve_byok_provider(self, *, base_url: str, provider: Optional[str]) -> Optional[str]:
        normalized_provider = self._normalize_text(provider)
        if normalized_provider is not None:
            if normalized_provider in self._BYOK_PROVIDER_VALUES:
                return normalized_provider
            return None
        lowered_base_url = base_url.lower()
        if "api.anthropic.com" in lowered_base_url:
            return "anthropic"
        if "api.openai.com" in lowered_base_url:
            return "openai"
        return "generic-chat-completion-api"

    def _upsert_byok_model(
        self,
        *,
        model_name: str,
        api_key: str,
        base_url: str,
        provider: str,
    ) -> Optional[str]:
        settings_path = Path.home() / ".factory" / "settings.json"
        settings: Dict[str, Any] = {}
        if settings_path.exists():
            try:
                payload = json.loads(settings_path.read_text(encoding="utf-8"))
            except Exception:
                return None
            if not isinstance(payload, dict):
                return None
            settings = dict(payload)

        custom_models_value = settings.get("customModels")
        if custom_models_value is None:
            custom_models: list[Any] = []
        elif isinstance(custom_models_value, list):
            custom_models = list(custom_models_value)
        else:
            return None

        retained_custom_models: list[Any] = []
        for item in custom_models:
            if not isinstance(item, dict):
                retained_custom_models.append(item)
                continue
            display_name = self._normalize_text(item.get("displayName"))
            if display_name == self._BYOK_DISPLAY_NAME:
                continue
            retained_custom_models.append(item)

        retained_custom_models.append(
            {
                "model": model_name,
                "displayName": self._BYOK_DISPLAY_NAME,
                "baseUrl": base_url,
                "apiKey": api_key,
                "provider": provider,
            }
        )
        settings["customModels"] = retained_custom_models

        settings_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix="settings.",
            suffix=".json",
            dir=str(settings_path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(settings, ensure_ascii=True, indent=2))
                handle.write("\n")
            os.replace(temp_path, settings_path)
        except Exception:
            try:
                os.unlink(temp_path)
            except Exception:
                pass
            return None

        display_name_slug = self._BYOK_DISPLAY_NAME.replace(" ", "-")
        index = len(retained_custom_models) - 1
        return f"custom:{display_name_slug}-{index}"

    @staticmethod
    def _iter_dicts(obj: Any):
        if isinstance(obj, dict):
            yield obj
            for value in obj.values():
                yield from FactoryAgent._iter_dicts(value)
            return
        if isinstance(obj, list):
            for value in obj:
                yield from FactoryAgent._iter_dicts(value)

    def _find_session_settings_file(self, session_id: Optional[str]) -> Optional[Path]:
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        root = Path.home() / ".factory" / "sessions"
        if not root.exists():
            return None
        matches = sorted(root.glob(f"**/{session_id.strip()}.settings.json"))
        if len(matches) != 1:
            return None
        return matches[0]

    def _find_session_transcript_file(self, session_id: Optional[str]) -> Optional[Path]:
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        root = Path.home() / ".factory" / "sessions"
        if not root.exists():
            return None
        matches = sorted(root.glob(f"**/{session_id.strip()}.jsonl"))
        if len(matches) != 1:
            return None
        return matches[0]

    def _install_specific_version(self, version: str) -> CommandResult:
        start = time.monotonic()
        logs: list[str] = []
        staging_root: Optional[Path] = None
        try:
            normalized = version.strip()
            if not normalized or not self._VERSION_RE.fullmatch(normalized):
                raise RuntimeError("invalid Factory version format")

            os_name = self._map_os(platform.system())
            arch = self._map_arch(platform.machine())
            if os_name is None or arch is None:
                raise RuntimeError(
                    f"unsupported platform for factory version install: {platform.system()}/{platform.machine()}"
                )

            droid_arch = arch
            if arch == "x64" and not self._has_avx2(os_name):
                droid_arch = f"{arch}-baseline"

            base_url = "https://downloads.factory.ai"
            droid_url = f"{base_url}/factory-cli/releases/{normalized}/{os_name}/{droid_arch}/droid"
            droid_sha_url = f"{droid_url}.sha256"
            rg_url = f"{base_url}/ripgrep/{os_name}/{arch}/rg"
            rg_sha_url = f"{rg_url}.sha256"

            staging_root = Path(tempfile.mkdtemp(prefix="cakit-factory-"))
            droid_binary = staging_root / "droid"
            rg_binary = staging_root / "rg"

            self._download_with_checksum(
                target_path=droid_binary,
                binary_url=droid_url,
                checksum_url=droid_sha_url,
            )
            self._download_with_checksum(
                target_path=rg_binary,
                binary_url=rg_url,
                checksum_url=rg_sha_url,
            )

            droid_target = Path.home() / ".local" / "bin" / "droid"
            rg_target = Path.home() / ".factory" / "bin" / "rg"
            droid_target.parent.mkdir(parents=True, exist_ok=True)
            rg_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(droid_binary, droid_target)
            shutil.copy2(rg_binary, rg_target)
            os.chmod(droid_target, 0o755)
            os.chmod(rg_target, 0o755)

            logs.append(f"installed droid {normalized} to {droid_target}")
            logs.append(f"installed rg to {rg_target}")
            exit_code = 0
        except Exception as exc:
            logs.append(str(exc))
            exit_code = 1
        finally:
            if staging_root is not None:
                shutil.rmtree(staging_root, ignore_errors=True)

        duration = time.monotonic() - start
        return CommandResult(
            exit_code=exit_code,
            stdout="\n".join(logs),
            stderr="",
            duration_seconds=duration,
        )

    @staticmethod
    def _map_os(system_name: str) -> Optional[str]:
        if system_name == "Linux":
            return "linux"
        if system_name == "Darwin":
            return "darwin"
        return None

    @staticmethod
    def _map_arch(machine: str) -> Optional[str]:
        lowered = machine.lower()
        if lowered in {"x86_64", "amd64"}:
            return "x64"
        if lowered in {"arm64", "aarch64"}:
            return "arm64"
        return None

    @staticmethod
    def _has_avx2(os_name: str) -> bool:
        if os_name == "linux":
            cpuinfo = Path("/proc/cpuinfo")
            if cpuinfo.exists():
                try:
                    return "avx2" in cpuinfo.read_text(encoding="utf-8", errors="ignore").lower()
                except Exception:
                    return False
        if os_name == "darwin":
            try:
                result = os.popen("sysctl -a 2>/dev/null").read().lower()
            except Exception:
                return False
            return "avx2" in result
        return False

    def _download_with_checksum(self, *, target_path: Path, binary_url: str, checksum_url: str) -> None:
        with urllib.request.urlopen(binary_url, timeout=30) as response:
            payload = response.read()
        target_path.write_bytes(payload)

        with urllib.request.urlopen(checksum_url, timeout=30) as response:
            checksum_text = response.read().decode("utf-8", errors="ignore").strip()
        expected_checksum = checksum_text.split()[0].strip().lower()
        if not expected_checksum:
            raise RuntimeError(f"empty checksum for {binary_url}")

        actual_checksum = hashlib.sha256(payload).hexdigest().lower()
        if actual_checksum != expected_checksum:
            raise RuntimeError(f"checksum verification failed for {binary_url}")
