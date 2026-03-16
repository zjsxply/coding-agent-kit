from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib import request as urlrequest

from ..agent_runtime import env as runtime_env
from ..agent_runtime import install_version as runtime_install
from ..agent_runtime import parsing as runtime_parsing
from ..agent_runtime import trajectory as runtime_trajectory
from .base import CodingAgent, InstallStrategy, VersionCommandTemplate
from ..models import InstallResult, RunResult
from ..stats_extract import (
    merge_model_usage,
    opt_float,
    req_int,
    req_str,
    select_values,
)


class AiderAgent(CodingAgent):
    name = "aider"
    display_name = "Aider"
    binary = "aider"
    supports_images = True
    supports_videos = False
    required_runtimes = ("python-build",)
    install_strategy = InstallStrategy(
        kind="uv_tool",
        package="aider-chat",
        version_style="pep440",
        python_version="3.12",
        force=True,
    )
    _ALPINE_TREE_SITTER_WORKAROUND_VERSION = "0.13.0"
    version_template = VersionCommandTemplate(
        args=("aider", "--version"),
        parse_mode="regex_first_line",
        regex=r"^(?:aider\s+)?([A-Za-z0-9._-]+)$",
    )
    _OUTPUT_META_PREFIXES = (
        "Aider v",
        "Model:",
        "Main model:",
        "Weak model:",
        "Git repo:",
        "Repo-map:",
        "Added ",
        "https://aider.chat/HISTORY.html#release-notes",
    )
    _OUTPUT_SEPARATOR_LINES = {"--------------", "------------"}

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        result = super().install(scope=scope, version=version)
        if result.ok or not self._should_retry_alpine_tree_sitter_install(result=result, version=version):
            return result
        return self._install_with_alpine_tree_sitter_workaround(version=version)

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        images = images or []
        settings, env_error = self._resolve_runtime_settings(model_override=model_override)
        if env_error is not None:
            return self._build_error_run_result(message=env_error, cakit_exit_code=1)

        run_dir = self._make_temp_dir(prefix="cakit-aider-", keep=True)
        analytics_log = run_dir / "analytics.jsonl"
        input_history = run_dir / "input.history"
        chat_history = run_dir / "chat.history.md"
        llm_history = run_dir / "llm.history.log"
        model_metadata_path = self._write_run_model_metadata(
            run_dir=run_dir,
            model=settings["model"],
            images=images,
        )

        cmd = [
            "aider",
            "--message",
            prompt,
            "--model",
            settings["model"],
            "--edit-format",
            "ask",
            "--no-git",
            "--yes-always",
            "--no-show-model-warnings",
            "--no-show-release-notes",
            "--no-check-update",
            "--no-fancy-input",
            "--no-suggest-shell-commands",
            "--no-pretty",
            "--no-stream",
            "--analytics-log",
            str(analytics_log),
            "--no-analytics",
            "--input-history-file",
            str(input_history),
            "--chat-history-file",
            str(chat_history),
            "--llm-history-file",
            str(llm_history),
        ]
        if model_metadata_path is not None:
            cmd.extend(["--model-metadata-file", str(model_metadata_path)])
        if reasoning_effort:
            cmd.extend(["--reasoning-effort", reasoning_effort])
        cmd.extend(str(image) for image in images)

        env: Dict[str, str] = {
            "AIDER_OPENAI_API_KEY": settings["api_key"],
        }
        if settings.get("api_base"):
            env["AIDER_OPENAI_API_BASE"] = settings["api_base"]

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        analytics_payloads: Optional[list[Dict[str, Any]]] = None
        if analytics_log.exists():
            analytics_text = self._read_text(analytics_log)
            if analytics_text:
                loaded_payloads = runtime_parsing.load_output_json_payloads(
                    analytics_text,
                    stdout_only_output=False,
                )
                if loaded_payloads:
                    analytics_payloads = loaded_payloads
        models_usage, llm_calls, tool_calls, total_cost = self._extract_analytics_stats(
            payload_rows=analytics_payloads,
        )
        response = self._extract_response_from_output(output)
        trajectory_content = runtime_trajectory.build_trajectory_content(
            output=output,
            source=str(run_dir),
            attachments=[
                ("ANALYTICS LOG", analytics_log),
                ("CHAT HISTORY", chat_history),
                ("LLM HISTORY", llm_history),
            ],
        )
        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=total_cost,
            telemetry_log=str(analytics_log) if analytics_log.exists() else None,
            trajectory_content=trajectory_content,
        )

    def _resolve_runtime_settings(
        self, *, model_override: Optional[str]
    ) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
        resolved, error = runtime_env.resolve_openai_env(
            api_key_env="AIDER_OPENAI_API_KEY",
            model_env="AIDER_MODEL",
            base_url_env="AIDER_OPENAI_BASE_URL",
            model_override=model_override,
            normalize_text=runtime_parsing.normalize_text,
        )
        if error is not None:
            return None, error
        model_raw = resolved.get("model")
        model = (
            runtime_env.normalize_litellm_model(
                model_raw,
                output_format="slash",
            )
            if isinstance(model_raw, str)
            else None
        )
        if model is None:
            return None, runtime_env.missing_env_with_fallback_message([("AIDER_MODEL", "OPENAI_DEFAULT_MODEL")])

        return {
            "api_key": str(resolved.get("api_key")),
            "api_base": str(resolved.get("base_url") or ""),
            "model": model,
        }, None

    def _should_retry_alpine_tree_sitter_install(
        self,
        *,
        result: InstallResult,
        version: Optional[str],
    ) -> bool:
        if version is None or shutil.which("apk") is None:
            return False
        details = runtime_parsing.normalize_text(result.details)
        if details is None:
            return False
        return (
            "tree-sitter-language-pack==0.9.0" in details
            and "musllinux" in details
            and "not compatible" in details
        )

    def _install_with_alpine_tree_sitter_workaround(self, *, version: Optional[str]) -> InstallResult:
        if version is None:
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details="aider Alpine compatibility workaround requires an explicit version",
                config_path=None,
            )
        requirements = self._build_alpine_tree_sitter_workaround_requirements(version=version)
        if requirements is None:
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details=f"aider {version} does not expose the expected tree-sitter dependency set for Alpine",
                config_path=None,
            )
        if runtime_install.resolve_uv_binary() is None and not runtime_install.ensure_uv(self._run):
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details="uv is required for the Alpine aider compatibility install path",
                config_path=None,
            )
        uv_binary = runtime_install.resolve_uv_binary() or "uv"
        python_binary = runtime_install.resolve_python_executable() or "python3"
        install_root = self._aider_install_root()
        version_key = version.replace("/", "-")
        venv_dir = install_root / "tools" / f"aider-{version_key}-alpine"
        requirements_path = install_root / "cache" / f"aider-{version_key}-alpine.requirements.txt"
        if venv_dir.exists():
            shutil.rmtree(venv_dir, ignore_errors=True)
        self._write_text(requirements_path, "\n".join(requirements) + "\n")

        command_outputs: list[str] = []
        for command in (
            [uv_binary, "venv", "--python", python_binary, str(venv_dir)],
            [uv_binary, "pip", "install", "--python", str(venv_dir / "bin" / "python"), "-r", str(requirements_path)],
            [
                uv_binary,
                "pip",
                "install",
                "--python",
                str(venv_dir / "bin" / "python"),
                "--no-deps",
                f"aider-chat=={version}",
            ],
        ):
            result = self._run(command)
            if result.output:
                command_outputs.append(result.output)
            if result.exit_code != 0:
                details = "\n\n".join(command_outputs).strip() or result.output
                return InstallResult(
                    agent=self.name,
                    version=None,
                    ok=False,
                    details=details,
                    config_path=None,
                )

        binary_path = venv_dir / "bin" / "aider"
        if not binary_path.exists():
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details=f"aider compatibility install did not create {binary_path}",
                config_path=None,
            )
        link_error = self._link_compatibility_aider_binary(binary_path)
        if link_error is not None:
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details=link_error,
                config_path=None,
            )
        installed_version = self.get_version()
        if not self._installed_version_matches_requested(requested_version=version, observed_version=installed_version):
            details = self._build_install_verification_message(
                requested_version=version,
                observed_version=installed_version,
            )
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details=details,
                config_path=None,
            )
        return InstallResult(
            agent=self.name,
            version=installed_version,
            ok=True,
            details=None,
            config_path=self.configure(),
        )

    def _build_alpine_tree_sitter_workaround_requirements(self, *, version: str) -> Optional[list[str]]:
        metadata_url = f"https://pypi.org/pypi/aider-chat/{version}/json"
        with urlrequest.urlopen(metadata_url, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        requires_dist = payload.get("info", {}).get("requires_dist")
        if not isinstance(requires_dist, list):
            return None

        requirements: list[str] = []
        saw_problematic_tree_sitter = False
        for raw_requirement in requires_dist:
            if not isinstance(raw_requirement, str):
                continue
            requirement = raw_requirement.strip()
            if not requirement or "extra ==" in requirement:
                continue
            if requirement.startswith("tree-sitter"):
                if requirement == "tree-sitter-language-pack==0.9.0":
                    saw_problematic_tree_sitter = True
                continue
            requirements.append(requirement)
        if not saw_problematic_tree_sitter:
            return None
        # Alpine/musl cannot use the pinned 0.9.0 wheel set, so install a compatible bundle instead.
        requirements.append(f"tree-sitter-language-pack=={self._ALPINE_TREE_SITTER_WORKAROUND_VERSION}")
        return requirements

    def _aider_install_root(self) -> Path:
        install_home = os.environ.get("CAKIT_INSTALL_HOME")
        candidates = [Path(install_home).expanduser()] if install_home else []
        candidates.extend(
            [
                Path("/opt") / "cakit",
                Path.home() / ".local" / "share" / "cakit",
                Path("/tmp") / "cakit",
            ]
        )
        return self._resolve_writable_dir(*candidates, purpose="Aider install")

    def _aider_bin_dir(self) -> Path:
        uv_tool_bin = os.environ.get("UV_TOOL_BIN_DIR")
        xdg_bin_home = os.environ.get("XDG_BIN_HOME")
        candidates = []
        if uv_tool_bin:
            candidates.append(Path(uv_tool_bin).expanduser())
        if xdg_bin_home:
            candidates.append(Path(xdg_bin_home).expanduser())
        candidates.extend([Path("/usr/local/bin"), Path.home() / ".local" / "bin", Path("/tmp") / "cakit" / "bin"])
        return self._resolve_writable_dir(*candidates, purpose="Aider bin")

    def _link_compatibility_aider_binary(self, binary_path: Path) -> Optional[str]:
        bin_dir = self._aider_bin_dir()
        target = bin_dir / "aider"
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink()
        target.symlink_to(binary_path)
        return None

    def _write_run_model_metadata(
        self,
        *,
        run_dir: Path,
        model: str,
        images: list[Path],
    ) -> Optional[Path]:
        metadata = self._build_model_metadata(model=model, images=images)
        if metadata is None:
            return None
        path = run_dir / "model.metadata.json"
        self._write_text(path, json.dumps(metadata, ensure_ascii=True, indent=2))
        return path

    def _build_model_metadata(
        self,
        *,
        model: str,
        images: list[Path],
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        if not images:
            return None
        if not model.startswith("openai/kimi-"):
            return None
        return {
            model: {
                "litellm_provider": "openai",
                "mode": "chat",
                "supports_vision": True,
            }
        }

    def _extract_analytics_stats(
        self,
        *,
        payload_rows: Optional[list[Dict[str, Any]]],
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int], Optional[float]]:
        if not payload_rows:
            return {}, None, None, None

        message_send_properties = [
            item
            for item in (select_values(payload_rows, '$[?(@.event == "message_send")].properties') or [])
            if isinstance(item, dict)
        ]
        llm_calls = len(message_send_properties) if message_send_properties else None
        models_usage: Dict[str, Dict[str, int]] = {}
        total_cost: Optional[float] = None

        for properties in message_send_properties:
            model_name = req_str(properties, "$.main_model")
            prompt_tokens = req_int(properties, "$.prompt_tokens")
            completion_tokens = req_int(properties, "$.completion_tokens")
            total_tokens = req_int(properties, "$.total_tokens")
            if model_name is None or prompt_tokens is None or completion_tokens is None or total_tokens is None:
                continue
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }

            merge_model_usage(models_usage, model_name, usage)

            candidate_total_cost = opt_float(properties, "$.total_cost")
            if candidate_total_cost is not None:
                total_cost = candidate_total_cost

        event_values = select_values(payload_rows, "$[*].event")
        tool_calls = (
            sum(1 for event in event_values if isinstance(event, str) and event.startswith("command_"))
            if event_values is not None
            else None
        )
        return models_usage, llm_calls, tool_calls, total_cost

    def _extract_response_from_output(self, output: str) -> Optional[str]:
        stdout = runtime_parsing.stdout_only(output)
        if not stdout.strip():
            return None

        entries: list[Dict[str, str]] = []
        current_section = "body"
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if line in self._OUTPUT_SEPARATOR_LINES:
                continue
            if line == "► **THINKING**":
                current_section = "thinking"
                continue
            if line == "► **ANSWER**":
                current_section = "answer"
                continue
            if not line:
                continue
            if line.startswith("Tokens:") or line.startswith("Cost:"):
                break
            if line.startswith(self._OUTPUT_META_PREFIXES):
                continue
            if current_section == "thinking":
                continue
            section = "answer" if current_section == "answer" else "body"
            entries.append({"section": section, "text": line})
        if not entries:
            return None

        answer_parts = [
            text
            for text in (
                runtime_parsing.normalize_text(item)
                for item in (select_values(entries, '$[?(@.section == "answer")].text') or [])
            )
            if text is not None
        ]
        answer = "\n".join(answer_parts) if answer_parts else None
        if answer:
            return answer
        body_parts = [
            text
            for text in (
                runtime_parsing.normalize_text(item)
                for item in (select_values(entries, '$[?(@.section == "body")].text') or [])
            )
            if text is not None
        ]
        if not body_parts:
            return None
        return "\n".join(body_parts)
