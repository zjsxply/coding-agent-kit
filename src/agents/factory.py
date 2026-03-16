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

from ..agent_runtime import env as runtime_env
from ..agent_runtime import parsing as runtime_parsing
from .base import CodingAgent, CommandResult, InstallStrategy, RunCommandTemplate, RunParseResult, RunPlan
from ..stats_extract import (
    last_value,
    opt_float,
    parse_usage_by_model,
    req_int,
    req_str,
    select_values,
)

class FactoryAgent(CodingAgent):
    name = "factory"
    display_name = "Factory Droid CLI"
    binary = "droid"
    supports_images = True
    supports_videos = False
    required_runtimes = ("node", "bash", "curl")
    install_strategy = InstallStrategy(kind="custom")
    run_template = RunCommandTemplate(
        base_args=("exec", "--output-format", "json"),
        prompt_mode="arg",
        prompt_flag=None,
        model_flag="--model",
        media_injection="none",
    )

    _VERSION_RE = re.compile(r"^[A-Za-z0-9._-]+$")
    _ALPINE_GLIBC_RELEASE = "2.35-r1"
    _ALPINE_GLIBC_KEY_URL = "https://alpine-pkgs.sgerrand.com/sgerrand.rsa.pub"
    _ALPINE_GLIBC_APK_URL = (
        f"https://github.com/sgerrand/alpine-pkg-glibc/releases/download/{_ALPINE_GLIBC_RELEASE}/"
        f"glibc-{_ALPINE_GLIBC_RELEASE}.apk"
    )
    _ALPINE_GLIBC_LOADER_SOURCE = "/usr/glibc-compat/lib64/ld-linux-x86-64.so.2"
    _ALPINE_GLIBC_LOADER_TARGET = "/lib64/ld-linux-x86-64.so.2"
    _BYOK_DISPLAY_NAME = "CAKIT BYOK"
    _BYOK_PROVIDER_VALUES = {
        "openai",
        "anthropic",
        "generic-chat-completion-api",
    }

    def _install_with_custom_strategy(
        self,
        *,
        scope: str,
        version: Optional[str],
    ) -> CommandResult:
        compatibility_result = self._ensure_alpine_glibc_compat()
        if compatibility_result is not None and compatibility_result.exit_code != 0:
            return compatibility_result
        if version and version.strip():
            install_result = self._install_specific_version(version.strip())
        else:
            install_result = self._run(["bash", "-lc", "curl -fsSL https://app.factory.ai/cli | sh"])
        return self._merge_install_command_results(compatibility_result, install_result)

    def _ensure_alpine_glibc_compat(self) -> Optional[CommandResult]:
        if not self._should_install_alpine_glibc_compat():
            return None

        started = time.monotonic()
        logs: list[str] = []
        staging_root: Optional[Path] = None
        try:
            apk_binary = shutil.which("apk")
            if apk_binary is None:
                raise RuntimeError("apk is required to install Factory glibc compatibility on Alpine")

            sudo_prefix: list[str] = []
            if os.geteuid() != 0:
                sudo_binary = shutil.which("sudo")
                if sudo_binary is None:
                    raise RuntimeError("factory on Alpine requires root or sudo to install glibc compatibility")
                sudo_prefix = [sudo_binary]

            loader_source = Path(self._ALPINE_GLIBC_LOADER_SOURCE)
            loader_target = Path(self._ALPINE_GLIBC_LOADER_TARGET)
            if loader_source.is_file() and loader_target.exists():
                return CommandResult(
                    exit_code=0,
                    stdout="using existing Factory Alpine glibc compatibility runtime",
                    stderr="",
                    duration_seconds=time.monotonic() - started,
                )

            if not loader_source.is_file():
                logs.append("installing Alpine glibc compatibility for Factory")
                install_ca_result = self._run([*sudo_prefix, apk_binary, "add", "--no-cache", "ca-certificates"])
                if install_ca_result.exit_code != 0:
                    raise RuntimeError(install_ca_result.output or "failed to install ca-certificates with apk")

                staging_root = Path(tempfile.mkdtemp(prefix="cakit-factory-glibc-"))
                key_path = staging_root / "sgerrand.rsa.pub"
                apk_path = staging_root / f"glibc-{self._ALPINE_GLIBC_RELEASE}.apk"
                self._download_url_to_path(url=self._ALPINE_GLIBC_KEY_URL, target_path=key_path)
                self._download_url_to_path(url=self._ALPINE_GLIBC_APK_URL, target_path=apk_path)

                mkdir_keys_result = self._run([*sudo_prefix, "mkdir", "-p", "/etc/apk/keys"])
                if mkdir_keys_result.exit_code != 0:
                    raise RuntimeError(mkdir_keys_result.output or "failed to create /etc/apk/keys")
                install_key_result = self._run([*sudo_prefix, "cp", str(key_path), "/etc/apk/keys/sgerrand.rsa.pub"])
                if install_key_result.exit_code != 0:
                    raise RuntimeError(install_key_result.output or "failed to install sgerrand apk signing key")
                install_glibc_result = self._run([*sudo_prefix, apk_binary, "add", "--no-cache", str(apk_path)])
                if install_glibc_result.exit_code != 0:
                    raise RuntimeError(install_glibc_result.output or "failed to install Alpine glibc compatibility")

            mkdir_loader_result = self._run([*sudo_prefix, "mkdir", "-p", str(loader_target.parent)])
            if mkdir_loader_result.exit_code != 0:
                raise RuntimeError(mkdir_loader_result.output or "failed to create /lib64")
            link_loader_result = self._run(
                [
                    *sudo_prefix,
                    "ln",
                    "-sf",
                    self._ALPINE_GLIBC_LOADER_SOURCE,
                    self._ALPINE_GLIBC_LOADER_TARGET,
                ]
            )
            if link_loader_result.exit_code != 0:
                raise RuntimeError(link_loader_result.output or "failed to link Factory Alpine glibc loader")
            logs.append("configured Factory Alpine glibc loader at /lib64/ld-linux-x86-64.so.2")
            exit_code = 0
        except Exception as exc:
            logs.append(str(exc))
            exit_code = 1
        finally:
            if staging_root is not None:
                shutil.rmtree(staging_root, ignore_errors=True)

        return CommandResult(
            exit_code=exit_code,
            stdout="\n".join(logs),
            stderr="",
            duration_seconds=time.monotonic() - started,
        )

    @staticmethod
    def _should_install_alpine_glibc_compat() -> bool:
        return (
            platform.system() == "Linux"
            and Path("/etc/alpine-release").exists()
            and platform.machine().strip().lower() in {"x86_64", "amd64"}
        )

    @staticmethod
    def _merge_install_command_results(
        compatibility_result: Optional[CommandResult],
        install_result: CommandResult,
    ) -> CommandResult:
        if compatibility_result is None or not compatibility_result.output.strip():
            return install_result
        if not install_result.output.strip():
            return CommandResult(
                exit_code=install_result.exit_code,
                stdout=compatibility_result.output,
                stderr="",
                duration_seconds=compatibility_result.duration_seconds + install_result.duration_seconds,
            )
        return CommandResult(
            exit_code=install_result.exit_code,
            stdout=f"{compatibility_result.output}\n{install_result.output}",
            stderr="",
            duration_seconds=compatibility_result.duration_seconds + install_result.duration_seconds,
        )

    @staticmethod
    def _download_url_to_path(*, url: str, target_path: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "cakit"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
        target_path.write_bytes(payload)

    def _build_run_plan(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> Optional[RunPlan]:
        images = images or []
        run_prompt = prompt
        if images:
            run_prompt, _, _ = self._build_natural_media_prompt(
                prompt,
                images=images,
                videos=None,
                tool_name="Read",
            )

        selected_model = runtime_parsing.normalize_text(model_override) or runtime_parsing.normalize_text(os.environ.get("CAKIT_FACTORY_MODEL"))
        selected_model, byok_error = self._resolve_model_for_run(selected_model)
        if byok_error is not None:
            self._raise_config_error(byok_error)

        env: Dict[str, str] = {
            "FACTORY_API_KEY": os.environ.get("FACTORY_API_KEY"),
            "FACTORY_API_BASE_URL": os.environ.get("FACTORY_BASE_URL"),
            "FACTORY_TOKEN": os.environ.get("FACTORY_TOKEN"),
            "FACTORY_LOG_FILE": os.environ.get("FACTORY_LOG_FILE"),
            "FACTORY_DISABLE_KEYRING": os.environ.get("FACTORY_DISABLE_KEYRING"),
        }

        template = self.run_template
        extra_args = [
            "--cwd",
            str(self.workdir),
        ]
        if reasoning_effort:
            extra_args.extend(["--reasoning-effort", reasoning_effort])
        explicit_log = env.get("FACTORY_LOG_FILE")
        telemetry_log = explicit_log.strip() if isinstance(explicit_log, str) and explicit_log.strip() else None
        return self._build_templated_run_plan(
            prompt=run_prompt,
            model=selected_model,
            env=env,
            extra_args=extra_args,
            template=template,
            parse_output=lambda output, command_result: self._parse_pipeline_output(
                output,
                telemetry_log=telemetry_log,
            ),
        )

    def _parse_pipeline_output(self, output: str, *, telemetry_log: Optional[str]) -> RunParseResult:
        parsed_output = runtime_parsing.parse_output_json_object(output)
        result_payload = parsed_output if req_str(parsed_output, "$.type") == "result" else None
        session_id = req_str(result_payload, "$.session_id")

        settings_payload: Optional[Dict[str, Any]] = None
        transcript_payloads: Optional[list[Dict[str, Any]]] = None
        if isinstance(session_id, str) and session_id.strip():
            session_root = Path.home() / ".factory" / "sessions"
            if session_root.exists():
                normalized_session_id = session_id.strip()

                settings_matches = sorted(session_root.glob(f"**/{normalized_session_id}.settings.json"))
                if len(settings_matches) == 1:
                    settings_payload = runtime_parsing.load_json_dict(settings_matches[0])

                transcript_matches = sorted(session_root.glob(f"**/{normalized_session_id}.jsonl"))
                if len(transcript_matches) == 1:
                    payloads: list[Dict[str, Any]] = []
                    transcript_text = self._read_text(transcript_matches[0])
                    if transcript_text is None:
                        transcript_payloads = None
                    else:
                        try:
                            for raw_line in transcript_text.splitlines():
                                line = raw_line.strip()
                                if not line:
                                    continue
                                payload = runtime_parsing.parse_json_dict(line)
                                if payload is None:
                                    continue
                                if isinstance(payload, dict):
                                    payloads.append(payload)
                        except Exception:
                            transcript_payloads = None
                        else:
                            transcript_payloads = payloads

        models_usage, llm_calls, tool_calls, total_cost = self._extract_run_stats(
            result_payload=result_payload,
            settings_payload=settings_payload,
            transcript_payloads=transcript_payloads,
        )

        response = req_str(result_payload, "$.result") or runtime_parsing.last_stdout_line(output)
        return RunParseResult(
            response=response,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=total_cost,
            telemetry_log=telemetry_log,
        )

    def _extract_run_stats(
        self,
        *,
        result_payload: Optional[Dict[str, Any]],
        settings_payload: Optional[Dict[str, Any]],
        transcript_payloads: Optional[list[Dict[str, Any]]],
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int], Optional[float]]:
        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls: Optional[int] = None
        total_cost: Optional[float] = None
        if isinstance(result_payload, dict):
            usage_raw = last_value(result_payload, "$.usage")
            usage = parse_usage_by_model(usage_raw, "factory") if isinstance(usage_raw, dict) else None
            model_name = req_str(settings_payload, "$.model")
            if usage is not None and model_name is not None:
                models_usage[model_name] = usage
            llm_calls = req_int(result_payload, "$.num_turns")
            total_cost = opt_float(result_payload, "$.total_cost")
        tool_calls = self._extract_tool_calls(transcript_payloads)
        return models_usage, llm_calls, tool_calls, total_cost

    def _extract_tool_calls(self, transcript_payloads: Optional[list[Dict[str, Any]]]) -> Optional[int]:
        if not transcript_payloads:
            return None
        payloads = [payload for payload in transcript_payloads if isinstance(payload, dict)]
        if len(payloads) != len(transcript_payloads):
            return None

        tool_call_events = [
            event
            for event in (select_values(payloads, '$[?(@.type == "tool_call")]') or [])
            if isinstance(event, dict)
        ]
        if tool_call_events:
            normalized_ids: set[str] = set()
            for event_id in select_values(tool_call_events, "$[*].id") or []:
                if not isinstance(event_id, str):
                    return None
                normalized_id = event_id.strip()
                if not normalized_id:
                    return None
                normalized_ids.add(normalized_id)
            idless_values = select_values(tool_call_events, '$[?(@.id == null)]')
            idless_count = len(idless_values) if idless_values is not None else 0
            return len(normalized_ids) + idless_count

        pre_tool_use_events = [
            event
            for event in (select_values(payloads, '$[?(@.hook_event_name == "PreToolUse")]') or [])
            if isinstance(event, dict)
        ]
        if not pre_tool_use_events:
            return 0
        tool_names = select_values(pre_tool_use_events, "$[*].tool_name")
        if tool_names is None:
            return None
        count = 0
        for tool_name in tool_names:
            if not isinstance(tool_name, str):
                return None
            normalized_name = tool_name.strip()
            if not normalized_name:
                return None
            count += 1
        return count

    def _resolve_model_for_run(self, selected_model: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        model_name = runtime_parsing.normalize_text(selected_model)
        byok_api_key = runtime_env.resolve_openai_api_key("CAKIT_FACTORY_BYOK_API_KEY")
        byok_base_url = runtime_env.resolve_openai_base_url("CAKIT_FACTORY_BYOK_BASE_URL")
        byok_provider = runtime_parsing.normalize_text(os.environ.get("CAKIT_FACTORY_BYOK_PROVIDER"))

        byok_requested = any(value is not None for value in (byok_api_key, byok_base_url, byok_provider))
        if not byok_requested:
            return model_name, None

        if model_name is None:
            model_name = runtime_parsing.normalize_text(os.environ.get("OPENAI_DEFAULT_MODEL"))

        missing: list[tuple[str, str]] = []
        if byok_api_key is None:
            missing.append(("CAKIT_FACTORY_BYOK_API_KEY", "OPENAI_API_KEY"))
        if byok_base_url is None:
            missing.append(("CAKIT_FACTORY_BYOK_BASE_URL", "OPENAI_BASE_URL"))
        if model_name is None:
            missing.append(("CAKIT_FACTORY_MODEL", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return None, runtime_env.missing_env_with_fallback_message(missing)

        provider = byok_provider
        if provider is not None:
            if provider not in self._BYOK_PROVIDER_VALUES:
                provider = None
        else:
            lowered_base_url = byok_base_url.lower()
            if "api.anthropic.com" in lowered_base_url:
                provider = "anthropic"
            elif "api.openai.com" in lowered_base_url:
                provider = "openai"
            else:
                provider = "generic-chat-completion-api"
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

    def _upsert_byok_model(
        self,
        *,
        model_name: str,
        api_key: str,
        base_url: str,
        provider: str,
    ) -> Optional[str]:
        settings_path = Path.home() / ".factory" / "settings.json"
        if not settings_path.exists():
            settings: Dict[str, Any] = {}
        else:
            loaded_settings = runtime_parsing.load_json(settings_path)
            if not isinstance(loaded_settings, dict):
                return None
            settings = dict(loaded_settings)

        custom_models_value = settings.get("customModels")
        if custom_models_value is None:
            custom_models = []
        elif isinstance(custom_models_value, list):
            custom_models = list(custom_models_value)
        else:
            return None

        retained_custom_models: list[Any] = []
        for item in custom_models:
            if not isinstance(item, dict):
                retained_custom_models.append(item)
                continue
            display_name = runtime_parsing.normalize_text(item.get("displayName"))
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

    def _install_specific_version(self, version: str) -> CommandResult:
        start = time.monotonic()
        logs: list[str] = []
        staging_root: Optional[Path] = None
        try:
            normalized = version.strip()
            if not normalized or not self._VERSION_RE.fullmatch(normalized):
                raise RuntimeError("invalid Factory version format")

            system_name = platform.system()
            if system_name == "Linux":
                os_name = "linux"
            elif system_name == "Darwin":
                os_name = "darwin"
            else:
                os_name = None

            machine = platform.machine().lower()
            if machine in {"x86_64", "amd64"}:
                arch = "x64"
            elif machine in {"arm64", "aarch64"}:
                arch = "arm64"
            else:
                arch = None

            if os_name is None or arch is None:
                raise RuntimeError(
                    f"unsupported platform for factory version install: {platform.system()}/{platform.machine()}"
                )

            droid_arch = arch
            if arch == "x64":
                has_avx2 = False
                if os_name == "linux":
                    cpuinfo = Path("/proc/cpuinfo")
                    if cpuinfo.exists():
                        cpuinfo_text = self._read_text_lossy(cpuinfo)
                        if cpuinfo_text is not None:
                            has_avx2 = "avx2" in cpuinfo_text.lower()
                elif os_name == "darwin":
                    try:
                        has_avx2 = "avx2" in os.popen("sysctl -a 2>/dev/null").read().lower()
                    except Exception:
                        has_avx2 = False
                if not has_avx2:
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
