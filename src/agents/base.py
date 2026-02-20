from __future__ import annotations

import abc
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ..models import InstallResult, RunResult
from ..stats_extract import (
    LlmCall,
    StatsArtifacts,
    StatsSnapshot,
    ToolCall,
    UsagePattern,
    build_stats_snapshot,
    build_stats_snapshot_from_events,
    extract_gemini_style_stats,
    extract_json_result_stats,
    extract_jsonl_stats,
    extract_opencode_session_export_stats,
    last_value,
    merge_model_usage,
    merge_stats_snapshots,
    opt_float,
    parse_usage_by_model,
    req_int,
    req_str,
    select_values,
    sum_int,
)
from ..utils import format_trace_text, load_json_payloads

__all__ = [
    "CodingAgent",
    "CommandResult",
    "InstallStrategy",
    "RunCommandTemplate",
    "VersionCommandTemplate",
    "StatsArtifacts",
    "StatsSnapshot",
    "LlmCall",
    "ToolCall",
    "UsagePattern",
    "extract_gemini_style_stats",
    "extract_json_result_stats",
    "extract_jsonl_stats",
    "extract_opencode_session_export_stats",
    "last_value",
    "opt_float",
    "parse_usage_by_model",
    "req_int",
    "req_str",
    "select_values",
    "sum_int",
]


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def output(self) -> str:
        if self.stdout and self.stderr:
            return f"{self.stdout}\n\n----- STDERR -----\n{self.stderr}"
        return self.stdout or self.stderr


@dataclass(frozen=True)
class InstallStrategy:
    kind: str
    package: Optional[str] = None
    require_config: bool = False
    configure_failure_message: Optional[str] = None
    version_style: str = "npm"
    python_version: Optional[str] = None
    force: bool = False
    with_packages: tuple[str, ...] = ()
    fallback_no_cache_dir: bool = False
    no_cache_dir: bool = False
    shell_command: Optional[str] = None
    shell_versioned_command: Optional[str] = None
    shell_version_env: Optional[str] = None
    version_normalizer: str = "identity"


@dataclass(frozen=True)
class VersionCommandTemplate:
    args: tuple[str, ...]
    parse_mode: str = "first_line"
    prefix: Optional[str] = None
    regex: Optional[str] = None
    regex_group: int = 1
    json_key: Optional[str] = None
    json_path: Optional[str] = None
    env_mode: str = "default"  # default | runtime_assets


@dataclass(frozen=True)
class RunCommandTemplate:
    base_args: tuple[str, ...] = ()
    prompt_mode: str = "flag"  # flag | arg
    prompt_flag: Optional[str] = "-p"
    model_flag: Optional[str] = "--model"
    media_injection: str = "none"  # none | natural | symbolic
    media_tool_name: str = "Read"


class CodingAgent(abc.ABC):
    name: str
    display_name: str
    binary: Optional[str] = None
    supports_images: bool = False
    supports_videos: bool = False
    required_runtimes: tuple[str, ...] = ()
    install_strategy: Optional[InstallStrategy] = None
    run_template: Optional[RunCommandTemplate] = None
    version_template: Optional[VersionCommandTemplate] = None
    _LITELLM_PROVIDER_IDS: frozenset[str] = frozenset(
        {
            "anthropic",
            "azure_ai",
            "azure_openai",
            "bedrock",
            "bedrock_converse",
            "cohere",
            "deepseek",
            "fireworks",
            "google_anthropic_vertex",
            "google_genai",
            "google_vertexai",
            "groq",
            "huggingface",
            "ibm",
            "mistralai",
            "nvidia",
            "ollama",
            "openai",
            "perplexity",
            "together",
            "upstage",
            "xai",
        }
    )

    def __init__(self, *, workdir: Optional[Path] = None) -> None:
        self.workdir = (workdir or Path.cwd()).expanduser().resolve()
        self._path_prefix_cache_key: Optional[str] = None
        self._path_prefix_cache: tuple[str, ...] = ()
        self._staged_media_dirs: set[Path] = set()
        self._ephemeral_temp_dirs: set[Path] = set()

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> "InstallResult":
        strategy = self._resolve_install_strategy()
        if strategy is None:
            raise NotImplementedError(f"{self.__class__.__name__} must define install() or install_strategy")
        return self._install_with_strategy(strategy=strategy, scope=scope, version=version)

    def configure(self) -> Optional[str]:
        return None

    def run(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> "RunResult":
        """Template method: handles shared pre-checks, then delegates to _run_impl."""
        rejected = self._reject_unsupported_media(images=images, videos=videos)
        if rejected:
            return rejected
        try:
            result = self._run_impl(
                prompt,
                images=images,
                videos=videos,
                reasoning_effort=reasoning_effort,
                model_override=model_override,
                base_env=base_env,
            )
        finally:
            self._cleanup_staged_media()
            self._cleanup_temp_dirs()
        normalized_stats = self._normalize_stats_snapshot(
            models_usage=result.models_usage or {},
            llm_calls=result.llm_calls,
            tool_calls=result.tool_calls,
            total_cost=result.total_cost,
        )
        result.models_usage = normalized_stats.models_usage
        result.llm_calls = normalized_stats.llm_calls
        result.tool_calls = normalized_stats.tool_calls
        result.total_cost = normalized_stats.total_cost
        if result.cakit_exit_code is None and result.command_exit_code is not None:
            result.cakit_exit_code = self._resolve_strict_run_exit_code(
                command_exit_code=result.command_exit_code,
                models_usage=result.models_usage or {},
                llm_calls=result.llm_calls,
                tool_calls=result.tool_calls,
                response=result.response,
            )
        return result

    @abc.abstractmethod
    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> "RunResult":
        """Agent-specific run implementation. Assumes shared checks already ran."""
        raise NotImplementedError

    def get_version(self) -> Optional[str]:
        template = self.version_template
        if template is None:
            binary = self.binary or self.name
            result = self._run([binary, "--version"])
            if result.exit_code != 0:
                return None
            return self._first_nonempty_line(result.output)

        env: Optional[Dict[str, str]] = None
        if template.env_mode == "runtime_assets":
            runtime_env_builder = getattr(self, "_runtime_asset_env", None)
            if not callable(runtime_env_builder):
                return None
            try:
                env = runtime_env_builder(create_if_missing=False)
            except TypeError:
                env = runtime_env_builder()
        elif template.env_mode != "default":
            return None

        result = self._run(template.args, env=env)
        if result.exit_code != 0:
            return None
        return self._parse_version_output(template=template, output=result.output)

    def _resolve_install_strategy(self) -> Optional[InstallStrategy]:
        return self.install_strategy

    def runtime_dependencies(self) -> tuple[str, ...]:
        declared = tuple(
            dict.fromkeys(
                runtime.strip().lower()
                for runtime in self.required_runtimes
                if isinstance(runtime, str) and runtime.strip()
            )
        )
        if declared:
            return declared
        strategy = self._resolve_install_strategy()
        if strategy is None:
            return ()
        runtime_by_install_kind = {
            "npm": ("node",),
            "uv_tool": ("uv",),
            "uv_pip": ("uv",),
        }
        return runtime_by_install_kind.get(strategy.kind, ())

    def _run(
        self,
        args: Iterable[str],
        env: Optional[Dict[str, str]] = None,
        input_text: Optional[str] = None,
        timeout: Optional[int] = None,
        unset_env: Optional[Iterable[str]] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> CommandResult:
        merged_env = dict(base_env) if base_env is not None else os.environ.copy()
        if unset_env:
            for key in unset_env:
                merged_env.pop(key, None)
        if env:
            merged_env.update({k: v for k, v in env.items() if v is not None})
        extra_paths = list(self._runtime_path_prefixes())
        if extra_paths:
            current_path = merged_env.get("PATH", "")
            merged_env["PATH"] = os.pathsep.join(extra_paths + ([current_path] if current_path else []))
        start = time.monotonic()
        command_args = list(args)
        try:
            with (
                tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout_file,
                tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file,
            ):
                result = subprocess.run(
                    command_args,
                    cwd=str(self.workdir),
                    env=merged_env,
                    input=input_text,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=True,
                    timeout=timeout,
                )
                stdout_file.seek(0)
                stderr_file.seek(0)
                captured_stdout = stdout_file.read() or ""
                captured_stderr = stderr_file.read() or ""
        except FileNotFoundError as exc:
            duration = time.monotonic() - start
            return CommandResult(
                exit_code=127,
                stdout="",
                stderr=str(exc),
                duration_seconds=duration,
            )
        duration = time.monotonic() - start
        return CommandResult(
            exit_code=result.returncode,
            stdout=captured_stdout,
            stderr=captured_stderr,
            duration_seconds=duration,
        )

    def is_installed(self) -> bool:
        if not self.binary:
            return True
        return self._resolve_binary() is not None

    def _npm_prefix(self) -> Path:
        prefix = os.environ.get("CAKIT_NPM_PREFIX")
        if prefix:
            return Path(prefix).expanduser()
        return Path.home() / ".npm-global"

    def _runtime_path_prefixes(self) -> tuple[str, ...]:
        cache_key = os.environ.get("CAKIT_NPM_PREFIX", "")
        if self._path_prefix_cache_key == cache_key:
            return self._path_prefix_cache
        npm_prefix = Path(cache_key).expanduser() if cache_key else Path.home() / ".npm-global"
        self._path_prefix_cache = tuple(
            dict.fromkeys(
                (
                    str(npm_prefix / "bin"),
                    str(Path.home() / ".npm" / "bin"),
                    str(Path.home() / ".local" / "bin"),
                )
            )
        )
        self._path_prefix_cache_key = cache_key
        return self._path_prefix_cache

    def _install_with_npm(
        self,
        *,
        package: str,
        scope: str,
        version: Optional[str],
        require_config: bool = False,
        configure_failure_message: Optional[str] = None,
    ) -> "InstallResult":
        package_spec = self._build_install_package_spec(package, version, style="npm")
        if scope == "global":
            result = self._run(["npm", "install", "-g", package_spec])
        else:
            prefix = self._npm_prefix()
            prefix.mkdir(parents=True, exist_ok=True)
            result = self._run(["npm", "install", "-g", "--prefix", str(prefix), package_spec])
        config_path = self.configure()
        ok = result.exit_code == 0
        details = result.output
        if ok and require_config and config_path is None:
            ok = False
            message = configure_failure_message or f"{self.name} configure failed"
            details = f"{details}\n{message}" if details else message
        return InstallResult(
            agent=self.name,
            version=self.get_version() if ok else None,
            ok=ok,
            details=details,
            config_path=config_path,
        )

    def _install_with_strategy(
        self,
        *,
        strategy: InstallStrategy,
        scope: str,
        version: Optional[str],
    ) -> "InstallResult":
        if strategy.kind == "npm":
            if not strategy.package:
                raise ValueError("install strategy kind=npm requires package")
            return self._install_with_npm(
                package=strategy.package,
                scope=scope,
                version=version,
                require_config=strategy.require_config,
                configure_failure_message=strategy.configure_failure_message,
            )

        if strategy.kind == "uv_tool":
            if not strategy.package:
                raise ValueError("install strategy kind=uv_tool requires package")
            package_spec = self._build_install_package_spec(
                strategy.package,
                version,
                style=strategy.version_style,
            )
            result = self._uv_tool_install(
                package_spec,
                python_version=strategy.python_version,
                force=strategy.force,
                with_packages=[pkg for pkg in strategy.with_packages if pkg],
                fallback_no_cache_dir=strategy.fallback_no_cache_dir,
            )
        elif strategy.kind == "uv_pip":
            if not strategy.package:
                raise ValueError("install strategy kind=uv_pip requires package")
            package_spec = self._build_install_package_spec(
                strategy.package,
                version,
                style=strategy.version_style,
            )
            packages = [package_spec, *[pkg for pkg in strategy.with_packages if pkg]]
            result = self._uv_pip_install(packages, no_cache_dir=strategy.no_cache_dir)
        elif strategy.kind == "shell":
            result = self._shell_install(
                shell_command=strategy.shell_command,
                shell_versioned_command=strategy.shell_versioned_command,
                shell_version_env=strategy.shell_version_env,
                version=version,
                version_normalizer=strategy.version_normalizer,
            )
        elif strategy.kind == "custom":
            result = self._install_with_custom_strategy(strategy=strategy, scope=scope, version=version)
        else:
            raise ValueError(f"unsupported install strategy: {strategy.kind}")
        config_path = self.configure()
        ok = result.exit_code == 0
        details = result.output
        if ok and strategy.require_config and config_path is None:
            ok = False
            message = strategy.configure_failure_message or f"{self.name} configure failed"
            details = f"{details}\n{message}" if details else message
        return InstallResult(
            agent=self.name,
            version=self.get_version() if ok else None,
            ok=ok,
            details=details,
            config_path=config_path,
        )

    def _install_with_custom_strategy(
        self,
        *,
        strategy: InstallStrategy,
        scope: str,
        version: Optional[str],
    ) -> CommandResult:
        return CommandResult(
            exit_code=1,
            stdout="",
            stderr=f"{self.name} custom install strategy is not implemented",
            duration_seconds=0.0,
        )

    def _shell_install(
        self,
        *,
        shell_command: Optional[str],
        shell_versioned_command: Optional[str],
        shell_version_env: Optional[str],
        version: Optional[str],
        version_normalizer: str,
    ) -> CommandResult:
        if not version:
            normalized_version = None
        else:
            normalized_candidate = version.strip()
            if not normalized_candidate:
                normalized_version = None
            elif version_normalizer == "identity":
                normalized_version = normalized_candidate
            elif version_normalizer == "prefix_v":
                normalized_version = (
                    normalized_candidate
                    if normalized_candidate.startswith("v")
                    else f"v{normalized_candidate}"
                )
            else:
                raise ValueError(f"unsupported install version normalizer: {version_normalizer}")
        command = shell_command
        if normalized_version and shell_versioned_command:
            command = shell_versioned_command
        if not command:
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr="shell install strategy missing command template",
                duration_seconds=0.0,
            )

        if normalized_version:
            quoted_version = shlex.quote(normalized_version)
            command = command.format(
                version=normalized_version,
                version_quoted=quoted_version,
            )
            if shell_version_env:
                command = f"{shell_version_env}={quoted_version} {command}"
        return self._run(["bash", "-lc", command])

    @staticmethod
    def _build_install_package_spec(package: str, version: Optional[str], *, style: str) -> str:
        if not version:
            return package
        normalized = version.strip()
        if not normalized:
            return package
        if style == "npm":
            if normalized.startswith("@"):
                return f"{package}{normalized}"
            return f"{package}@{normalized}"
        if style == "pep440":
            if normalized.startswith("=="):
                return f"{package}{normalized}"
            return f"{package}=={normalized}"
        if style == "git_ref":
            if normalized.startswith("@"):
                return f"{package}{normalized}"
            return f"{package}@{normalized}"
        raise ValueError(f"unsupported version style: {style}")

    def _resolve_binary(self) -> Optional[str]:
        if not self.binary:
            return None
        env_keys = (
            f"{self.name.upper()}_BIN",
            f"{self.binary.upper()}_BIN",
        )
        for key in env_keys:
            value = os.environ.get(key)
            if value:
                candidate = Path(value).expanduser()
                if candidate.exists():
                    return str(candidate)
        path = shutil.which(self.binary)
        if path:
            return path
        for folder in (self._npm_prefix() / "bin", Path.home() / ".npm" / "bin", Path.home() / ".local" / "bin"):
            candidate = folder / self.binary
            if candidate.exists():
                return str(candidate)
        return None

    @staticmethod
    def _first_nonempty_line(text: Optional[str]) -> Optional[str]:
        if not isinstance(text, str):
            return None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line:
                return line
        return None

    def _build_error_run_result(
        self,
        *,
        message: str,
        cakit_exit_code: int = 1,
        agent_version: Optional[str] = None,
        command_exit_code: Optional[int] = None,
        raw_output: Optional[str] = None,
        runtime_seconds: float = 0.0,
    ) -> "RunResult":
        output_path = self._write_output(self.name, message)

        trajectory_path = self._write_trajectory(
            self.name,
            format_trace_text(message, source=str(output_path)),
        )
        return RunResult(
            agent=self.name,
            agent_version=agent_version if agent_version is not None else self.get_version(),
            runtime_seconds=runtime_seconds,
            models_usage={},
            tool_calls=None,
            llm_calls=None,
            total_cost=None,
            telemetry_log=None,
            response=message,
            cakit_exit_code=cakit_exit_code,
            command_exit_code=command_exit_code,
            output_path=str(output_path),
            raw_output=raw_output if raw_output is not None else message,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def finalize_run(
        self,
        *,
        command_result: CommandResult,
        response: Optional[str],
        models_usage: Dict[str, Dict[str, int]],
        llm_calls: Optional[int],
        tool_calls: Optional[int],
        total_cost: Optional[float] = None,
        telemetry_log: Optional[str] = None,
        agent_version: Optional[str] = None,
        raw_output: Optional[str] = None,
        runtime_seconds: Optional[float] = None,
        trajectory_content: Optional[str] = None,
        trajectory_source: Optional[str] = None,
    ) -> "RunResult":
        output = raw_output if raw_output is not None else command_result.output
        snapshot = self._normalize_stats_snapshot(
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=total_cost,
        )
        output_path = self._write_output(self.name, output)
        trace_content = trajectory_content
        if trace_content is None:
            source = trajectory_source or str(output_path)
            trace_content = format_trace_text(output, source=source)
        trajectory_path = self._write_trajectory(self.name, trace_content)
        cakit_exit_code = self._resolve_strict_run_exit_code(
            command_exit_code=command_result.exit_code,
            models_usage=snapshot.models_usage,
            llm_calls=snapshot.llm_calls,
            tool_calls=snapshot.tool_calls,
            response=response,
        )
        return RunResult(
            agent=self.name,
            agent_version=agent_version if agent_version is not None else self.get_version(),
            runtime_seconds=command_result.duration_seconds if runtime_seconds is None else runtime_seconds,
            models_usage=snapshot.models_usage,
            tool_calls=snapshot.tool_calls,
            llm_calls=snapshot.llm_calls,
            total_cost=snapshot.total_cost,
            telemetry_log=telemetry_log,
            response=response,
            cakit_exit_code=cakit_exit_code,
            command_exit_code=command_result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def _parse_version_output(
        self,
        *,
        template: VersionCommandTemplate,
        output: str,
    ) -> Optional[str]:
        text = output.strip()
        if not text:
            return None

        if template.parse_mode == "text":
            return text

        if template.parse_mode == "json_key":
            return self._parse_version_from_json_path(text, key=template.json_key)

        if template.parse_mode == "json_path":
            return self._parse_version_from_json_path(text, path=template.json_path)

        first_line = self._first_nonempty_line(text)
        if first_line is None:
            return None

        if template.parse_mode == "first_line":
            return first_line

        if template.parse_mode == "prefixed_second_token":
            prefix = template.prefix
            if not prefix:
                return None
            parts = first_line.split()
            if len(parts) < 2:
                return None
            if not parts[0].lower().startswith(prefix.lower()):
                return None
            value = parts[1].strip()
            return value or None

        if template.parse_mode == "prefixed_remainder":
            prefix = template.prefix
            if not prefix:
                return None
            if not first_line.lower().startswith(prefix.lower()):
                return None
            remainder = first_line[len(prefix) :].strip()
            return remainder or None

        if template.parse_mode == "regex_first_line":
            pattern = template.regex
            if not pattern:
                return None
            match = re.search(pattern, first_line)
            if not match:
                return None
            value = match.group(template.regex_group)
            if not isinstance(value, str):
                return None
            cleaned = value.strip()
            return cleaned or None

        return None

    def _parse_version_from_json_path(
        self,
        text: str,
        *,
        path: Optional[str] = None,
        key: Optional[str] = None,
    ) -> Optional[str]:
        payload = self._parse_json(text)
        if payload is None:
            return None

        normalized_path = path.strip() if isinstance(path, str) else ""
        if normalized_path:
            resolved_path = normalized_path if normalized_path.startswith("$") else None
        else:
            normalized_key = key.strip() if isinstance(key, str) else ""
            resolved_path = f"$[{json.dumps(normalized_key, ensure_ascii=True)}]" if normalized_key else None
        if not resolved_path:
            return None
        value = last_value(payload, resolved_path)
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        stringified = str(value).strip()
        return stringified or None

    def _ensure_uv(self) -> bool:
        if shutil.which("uv") is not None:
            return True
        if not sys.platform.startswith("linux"):
            return False
        if shutil.which("curl") is None:
            return False
        install = self._run(["bash", "-lc", "curl -LsSf https://astral.sh/uv/install.sh | sh"])
        if install.exit_code != 0:
            return False
        return shutil.which("uv") is not None or (Path.home() / ".local" / "bin" / "uv").exists()

    def _pip_install(
        self,
        packages: list[str],
        *,
        no_cache_dir: bool = False,
    ) -> CommandResult:
        cmd = ["python", "-m", "pip", "install"]
        if no_cache_dir:
            cmd.append("--no-cache-dir")
        cmd.extend(packages)
        return self._run(cmd)

    def _uv_tool_install(
        self,
        package_spec: str,
        *,
        python_version: Optional[str] = None,
        force: bool = False,
        with_packages: Optional[list[str]] = None,
        fallback_no_cache_dir: bool = False,
    ) -> CommandResult:
        extras = [pkg for pkg in (with_packages or []) if pkg]
        if self._ensure_uv():
            cmd = ["uv", "tool", "install"]
            if force:
                cmd.append("--force")
            if python_version:
                cmd.extend(["--python", python_version])
            for pkg in extras:
                cmd.extend(["--with", pkg])
            cmd.append(package_spec)
            return self._run(cmd)
        return self._pip_install(
            [package_spec, *extras],
            no_cache_dir=fallback_no_cache_dir,
        )

    def _uv_pip_install(
        self,
        packages: list[str],
        *,
        no_cache_dir: bool = False,
    ) -> CommandResult:
        if self._ensure_uv():
            cmd = ["uv", "pip", "install"]
            if no_cache_dir:
                cmd.append("--no-cache-dir")
            cmd.extend(packages)
            return self._run(cmd)
        return self._pip_install(packages, no_cache_dir=no_cache_dir)

    def _ensure_models_usage(
        self,
        models_usage: Dict[str, Dict[str, int]],
        usage: Optional[Dict[str, int]],
        default_model: Optional[str] = None,
    ) -> Dict[str, Dict[str, int]]:
        if models_usage:
            return models_usage
        normalized_usage = (
            parse_usage_by_model(usage, "prompt_completion") if isinstance(usage, dict) else None
        )
        if normalized_usage is None:
            return {}
        name = self._normalize_text(default_model) or "unknown"
        return {
            name: normalized_usage
        }

    def _merge_model_usage(
        self,
        models_usage: Dict[str, Dict[str, int]],
        model_name: str,
        usage: Dict[str, int],
    ) -> None:
        merge_model_usage(models_usage, model_name, usage)

    def _sum_usage_entries(
        self,
        usages: Iterable[Optional[Dict[str, int]]],
    ) -> Optional[Dict[str, int]]:
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        count = 0
        for usage in usages:
            parsed_usage = parse_usage_by_model(usage, "prompt_completion") if isinstance(usage, dict) else None
            if parsed_usage is None:
                continue
            prompt_tokens += parsed_usage["prompt_tokens"]
            completion_tokens += parsed_usage["completion_tokens"]
            total_tokens += parsed_usage["total_tokens"]
            count += 1
        if count < 1:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _normalize_stats_snapshot(
        self,
        *,
        models_usage: Any,
        llm_calls: Any,
        tool_calls: Any,
        total_cost: Any = None,
    ) -> StatsSnapshot:
        snapshot = build_stats_snapshot(
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=total_cost,
        )
        if snapshot is None:
            return StatsSnapshot(models_usage={}, llm_calls=None, tool_calls=None, total_cost=None)
        return snapshot

    def _build_stats_snapshot(
        self,
        *,
        models_usage: Any,
        llm_calls: Any,
        tool_calls: Any,
        total_cost: Any = None,
    ) -> Optional[StatsSnapshot]:
        return build_stats_snapshot(
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=total_cost,
        )

    def _build_stats_snapshot_from_events(
        self,
        *,
        llm_events: Iterable[LlmCall],
        tool_events: Iterable[ToolCall],
        total_cost: Optional[float] = None,
    ) -> Optional[StatsSnapshot]:
        return build_stats_snapshot_from_events(
            llm_events=llm_events,
            tool_events=tool_events,
            total_cost=total_cost,
        )

    def _build_single_model_stats_snapshot(
        self,
        *,
        model_name: Optional[str],
        usage: Optional[Dict[str, int]],
        llm_calls: Optional[int],
        tool_calls: Optional[int],
        total_cost: Optional[float] = None,
    ) -> Optional[StatsSnapshot]:
        models_usage: Dict[str, Dict[str, int]] = {}
        normalized_model_name = self._normalize_text(model_name)
        parsed_usage = parse_usage_by_model(usage, "prompt_completion") if isinstance(usage, dict) else None
        if normalized_model_name is not None and parsed_usage is not None:
            models_usage = {
                normalized_model_name: parsed_usage
            }

        normalized_llm_calls = self._as_int(llm_calls)
        normalized_tool_calls = self._as_int(tool_calls)

        return self._build_stats_snapshot(
            models_usage=models_usage,
            llm_calls=normalized_llm_calls,
            tool_calls=normalized_tool_calls,
            total_cost=total_cost,
        )

    def _build_stats_artifacts(
        self,
        *,
        raw_output: str = "",
        json_payload: Optional[Any] = None,
        jsonl_payloads: Optional[list[Dict[str, Any]]] = None,
        result_payload: Optional[Dict[str, Any]] = None,
        session_payload: Optional[Dict[str, Any]] = None,
    ) -> StatsArtifacts:
        return StatsArtifacts(
            raw_output=raw_output,
            json_payload=json_payload,
            jsonl_payloads=tuple(jsonl_payloads or ()),
            result_payload=result_payload,
            session_payload=session_payload,
        )

    def _merge_stats_snapshots(
        self,
        *,
        snapshots: Iterable[Optional[StatsSnapshot]],
        strategy: str = "aggregate",
    ) -> StatsSnapshot:
        return merge_stats_snapshots(snapshots, strategy=strategy)

    def _build_templated_command(
        self,
        *,
        template: RunCommandTemplate,
        prompt: str,
        model: Optional[str] = None,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        extra_args: Optional[Iterable[str]] = None,
    ) -> tuple[list[str], str]:
        run_prompt = prompt
        image_list = images or []
        video_list = videos or []
        if template.media_injection == "natural":
            run_prompt, _, _ = self._build_natural_media_prompt(
                prompt,
                images=image_list,
                videos=video_list,
                tool_name=template.media_tool_name,
            )
        elif template.media_injection == "symbolic":
            run_prompt, _ = self._build_symbolic_media_prompt(prompt, [*image_list, *video_list])
        elif template.media_injection != "none":
            raise ValueError(f"unsupported media_injection: {template.media_injection}")

        binary = self.binary or self.name
        cmd = [binary, *template.base_args]
        if model and template.model_flag:
            cmd.extend([template.model_flag, model])
        if extra_args:
            cmd.extend([arg for arg in extra_args if arg])

        if template.prompt_mode == "flag":
            if not template.prompt_flag:
                raise ValueError("prompt_flag is required when prompt_mode=flag")
            cmd.extend([template.prompt_flag, run_prompt])
        elif template.prompt_mode == "arg":
            cmd.append(run_prompt)
        else:
            raise ValueError(f"unsupported prompt_mode: {template.prompt_mode}")
        return cmd, run_prompt

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _read_text(self, path: Path) -> Optional[str]:
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def _read_text_lossy(self, path: Path) -> Optional[str]:
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8", errors="ignore")

    def _output_dir(self) -> Path:
        root = os.environ.get("CAKIT_OUTPUT_DIR")
        output_dir = Path(root) if root else Path.home() / ".cache" / "cakit"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _write_output_artifact(self, agent: str, content: str, *, suffix: str) -> Path:
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
        path = self._output_dir() / f"{agent}-{stamp}{suffix}"
        path.write_text(content, encoding="utf-8")
        return path

    def _write_output(self, agent: str, output: str) -> Path:
        return self._write_output_artifact(agent, output, suffix=".log")

    def _write_trajectory(self, agent: str, content: Optional[str]) -> Optional[Path]:
        if not content:
            return None
        return self._write_output_artifact(agent, content, suffix=".trajectory.log")

    def _reject_unsupported_media(
        self,
        *,
        images: Optional[list[Path]],
        videos: Optional[list[Path]],
    ) -> Optional["RunResult"]:
        image_list = images or []
        video_list = videos or []
        unsupported: list[str] = []
        if image_list and not self.supports_images:
            unsupported.append("image")
        if video_list and not self.supports_videos:
            unsupported.append("video")
        if not unsupported:
            return None
        if len(unsupported) == 1:
            subject = f"{unsupported[0]} input"
        else:
            subject = f"{unsupported[0]} and {unsupported[1]} input"
        message = f"{subject} is not supported by {self.display_name} CLI."
        return self._build_error_run_result(message=message, cakit_exit_code=2)

    @staticmethod
    def _stdout_only(output: str) -> str:
        marker = "----- STDERR -----"
        if marker in output:
            return output.split(marker, 1)[0]
        return output

    def _last_stdout_line(self, output: str, *, skip_prefixes: tuple[str, ...] = ()) -> Optional[str]:
        lines = [line.strip() for line in self._stdout_only(output).splitlines() if line.strip()]
        if skip_prefixes:
            lines = [line for line in lines if not any(line.startswith(prefix) for prefix in skip_prefixes)]
        if not lines:
            return None
        return lines[-1]

    @staticmethod
    def _last_nonempty_text(values: Optional[list[Any]]) -> Optional[str]:
        if values is None:
            return None
        for value in reversed(values):
            if not isinstance(value, str):
                continue
            cleaned = value.strip()
            if cleaned:
                return cleaned
        return None

    def _selected_texts(self, value: Any, path: str) -> list[str]:
        return [
            text
            for text in (self._normalize_text(item) for item in (select_values(value, path) or []))
            if text is not None
        ]

    def _selected_dicts(self, value: Any, path: str) -> list[Dict[str, Any]]:
        return [item for item in (select_values(value, path) or []) if isinstance(item, dict)]

    def _count_selected(self, value: Any, path: str) -> Optional[int]:
        selected = select_values(value, path)
        if selected is None:
            return None
        return len(selected)

    def _count_selected_total(self, value: Any, paths: Iterable[str]) -> Optional[int]:
        total = 0
        has_value = False
        for path in paths:
            selected = select_values(value, path)
            if selected is None:
                continue
            has_value = True
            total += len(selected)
        if not has_value:
            return None
        return total

    def _last_selected_text(self, value: Any, path: str) -> Optional[str]:
        return self._last_nonempty_text(select_values(value, path))

    def _joined_selected_text(self, value: Any, path: str, *, separator: str = "\n") -> Optional[str]:
        parts = self._selected_texts(value, path)
        if not parts:
            return None
        return separator.join(parts)

    def _first_selected_text(self, value: Any, paths: Iterable[str]) -> Optional[str]:
        for path in paths:
            text = self._last_selected_text(value, path)
            if text is not None:
                return text
        return None

    def _extract_content_text(self, content: Any, *, allow_scalars: bool = False) -> Optional[str]:
        if isinstance(content, str):
            cleaned = content.strip()
            return cleaned or None
        if not isinstance(content, list):
            return None
        text = self._joined_selected_text(content, '$[?(@.type == "text")].text')
        if text is not None:
            return text
        if not allow_scalars:
            return None
        return self._joined_selected_text(content, "$[*]")

    def _extract_content_texts(self, value: Any, path: str, *, allow_scalars: bool = False) -> list[str]:
        extracted: list[str] = []
        for content in select_values(value, path) or []:
            text = self._extract_content_text(content, allow_scalars=allow_scalars)
            if text is not None:
                extracted.append(text)
        return extracted

    @staticmethod
    def _parse_json(text: str) -> Optional[Any]:
        try:
            return json.loads(text)
        except Exception:
            return None

    @staticmethod
    def _parse_json_dict(text: str) -> Optional[Dict[str, Any]]:
        parsed = CodingAgent._parse_json(text)
        if not isinstance(parsed, dict):
            return None
        return parsed

    def _load_json(self, path: Path) -> Optional[Any]:
        text = self._read_text(path)
        if text is None:
            return None
        return self._parse_json(text)

    def _load_json_dict(self, path: Path) -> Optional[Dict[str, Any]]:
        text = self._read_text(path)
        if text is None:
            return None
        return self._parse_json_dict(text)

    def _run_json_dict_command(
        self,
        args: Iterable[str],
        *,
        env: Optional[Dict[str, str]] = None,
        base_env: Optional[Dict[str, str]] = None,
        stdout_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        result = self._run(args, env=env, base_env=base_env)
        if result.exit_code != 0:
            return None
        text = self._stdout_only(result.output) if stdout_only else result.stdout
        return self._parse_json_dict(text.strip())

    def _load_output_json_payloads(self, output: str, *, stdout_only: bool = True) -> list[Dict[str, Any]]:
        text = self._stdout_only(output) if stdout_only else output
        return load_json_payloads(text)

    def _extract_last_json_value(self, text: str) -> Optional[Any]:
        decoder = json.JSONDecoder()
        last_value: Optional[Any] = None
        index = 0
        while index < len(text):
            char = text[index]
            if char not in {"{", "["}:
                index += 1
                continue
            try:
                value, end = decoder.raw_decode(text, index)
            except Exception:
                index += 1
                continue
            if isinstance(value, (dict, list)):
                last_value = value
            index = end
        return last_value

    def _parse_output_json(self, output: str) -> Optional[Any]:
        stdout = self._stdout_only(output).strip()
        if not stdout:
            return None
        return self._extract_last_json_value(stdout)

    def _parse_output_json_object(self, output: str) -> Optional[Dict[str, Any]]:
        parsed = self._parse_output_json(output)
        if not isinstance(parsed, dict):
            return None
        return parsed

    @staticmethod
    def _as_int(value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _normalize_text(value: Optional[str]) -> Optional[str]:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        return cleaned

    @staticmethod
    def _normalize_provider_model(
        model: str,
        *,
        default_provider: str,
        colon_as_provider: bool = True,
    ) -> str:
        normalized = model.strip()
        if "/" in normalized:
            return normalized
        if colon_as_provider and ":" in normalized:
            provider, model_name = normalized.split(":", 1)
            provider = provider.strip()
            model_name = model_name.strip()
            if provider and model_name:
                return f"{provider}/{model_name}"
        return f"{default_provider}/{normalized}"

    @staticmethod
    def _normalize_litellm_model(
        model: str,
        *,
        default_provider: str = "openai",
        output_format: str = "colon",
    ) -> str:
        if output_format == "slash":
            return CodingAgent._normalize_provider_model(
                model,
                default_provider=default_provider,
            )
        if output_format != "colon":
            raise ValueError(f"unsupported LiteLLM model output format: {output_format}")
        normalized = model.strip()
        if ":" in normalized:
            return normalized
        if "/" in normalized:
            provider, model_name = normalized.split("/", 1)
            if provider in CodingAgent._LITELLM_PROVIDER_IDS and model_name:
                return f"{provider}:{model_name}"
        return f"{default_provider}:{normalized}"

    def _resolve_litellm_model(
        self,
        env_key: str,
        *,
        model_override: Optional[str] = None,
        source_env: Optional[Dict[str, str]] = None,
        default_provider: str = "openai",
        output_format: str = "slash",
    ) -> Optional[str]:
        model = self._resolve_openai_model(
            env_key,
            model_override=model_override,
            source_env=source_env,
        )
        if model is None:
            return None
        return self._normalize_litellm_model(
            model,
            default_provider=default_provider,
            output_format=output_format,
        )

    def _normalize_model(
        self,
        model: Optional[str],
        *,
        provider: Optional[str],
        colon_as_provider: bool = True,
    ) -> Optional[str]:
        normalized = self._normalize_text(model)
        if normalized is None:
            return None

        if "/" in normalized:
            provider_id, model_id = normalized.split("/", 1)
        elif colon_as_provider and ":" in normalized:
            provider_id, model_id = normalized.split(":", 1)
        else:
            normalized_provider = self._normalize_text(provider)
            if normalized_provider is None:
                return None
            provider_id, model_id = normalized_provider, normalized

        provider_id = provider_id.strip()
        model_id = model_id.strip()
        if not provider_id or not model_id:
            return None
        return f"{provider_id}/{model_id}"

    def _extract_model_id(
        self,
        model: Optional[str],
        *,
        colon_as_provider: bool = True,
    ) -> Optional[str]:
        normalized = self._normalize_text(model)
        if normalized is None:
            return None
        if "/" in normalized:
            _, model_id = normalized.split("/", 1)
            return self._normalize_text(model_id)
        if colon_as_provider and ":" in normalized:
            _, model_id = normalized.split(":", 1)
            return self._normalize_text(model_id)
        return normalized

    @staticmethod
    def _missing_env_message(missing: list[str]) -> Optional[str]:
        if not missing:
            return None
        return f"missing required environment variable(s): {', '.join(missing)}"

    def _resolve_openai_api_key(
        self,
        env_key: str,
        *,
        source_env: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        env_source = source_env if source_env is not None else os.environ
        return self._normalize_text(env_source.get(env_key)) or self._normalize_text(env_source.get("OPENAI_API_KEY"))

    def _resolve_openai_base_url(
        self,
        env_key: str,
        *,
        source_env: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        env_source = source_env if source_env is not None else os.environ
        return self._normalize_text(env_source.get(env_key)) or self._normalize_text(env_source.get("OPENAI_BASE_URL"))

    def _resolve_openai_model(
        self,
        env_key: str,
        *,
        model_override: Optional[str] = None,
        source_env: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        env_source = source_env if source_env is not None else os.environ
        return (
            self._normalize_text(model_override)
            or self._normalize_text(env_source.get(env_key))
            or self._normalize_text(env_source.get("OPENAI_DEFAULT_MODEL"))
        )

    @staticmethod
    def _missing_env_with_fallback_message(missing: list[tuple[str, str]]) -> Optional[str]:
        if not missing:
            return None
        formatted: list[str] = []
        for primary, fallback in missing:
            if primary == fallback:
                formatted.append(primary)
            else:
                formatted.append(f"{primary} (or {fallback})")
        return f"missing required environment variable(s): {', '.join(formatted)}"

    @staticmethod
    def _resolve_strict_run_exit_code(
        *,
        command_exit_code: int,
        models_usage: Dict[str, Dict[str, int]],
        llm_calls: Optional[int],
        tool_calls: Optional[int],
        response: Optional[str],
    ) -> int:
        if command_exit_code != 0:
            return command_exit_code
        if not models_usage:
            return 1
        if llm_calls is None or llm_calls < 1:
            return 1
        if tool_calls is None or tool_calls < 0:
            return 1
        if not isinstance(response, str) or not response.strip():
            return 1
        return 0

    def _stage_media_files(self, media_paths: list[Path]) -> list[Path]:
        staged: list[Path] = []
        run_stage = f"{os.getpid()}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
        stage_dir = Path("/tmp") / "cakit-media" / run_stage
        stage_dir.mkdir(parents=True, exist_ok=True)
        self._staged_media_dirs.add(stage_dir)
        for index, media_path in enumerate(media_paths):
            src = media_path.expanduser().resolve()
            suffix = src.suffix
            stem = src.stem or "media"
            safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in stem)
            target = stage_dir / f"{index:02d}-{safe_stem}{suffix}"
            try:
                if src != target:
                    shutil.copy2(src, target)
                staged.append(target)
            except Exception:
                staged.append(src)
        return staged

    def _cleanup_staged_media(self) -> None:
        if not self._staged_media_dirs:
            return
        stage_dirs = tuple(self._staged_media_dirs)
        self._staged_media_dirs.clear()
        for stage_dir in stage_dirs:
            shutil.rmtree(stage_dir, ignore_errors=True)

    @staticmethod
    def _keep_temp_dirs() -> bool:
        raw_value = os.environ.get("CAKIT_KEEP_TEMP_DIRS")
        if not isinstance(raw_value, str):
            return False
        return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}

    def _make_temp_dir(self, *, prefix: str, keep: bool = False) -> Path:
        path = Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))
        if not keep and not self._keep_temp_dirs():
            self._ephemeral_temp_dirs.add(path)
        return path

    def _cleanup_temp_dirs(self) -> None:
        if not self._ephemeral_temp_dirs:
            return
        temp_dirs = tuple(self._ephemeral_temp_dirs)
        self._ephemeral_temp_dirs.clear()
        for temp_dir in temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _build_natural_media_prompt(
        self,
        prompt: str,
        *,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        tool_name: str,
    ) -> tuple[str, list[Path], list[Path]]:
        resolved_images = [path.expanduser().resolve() for path in (images or [])]
        resolved_videos = [path.expanduser().resolve() for path in (videos or [])]
        if not resolved_images and not resolved_videos:
            return prompt, [], []

        lines: list[str] = [
            "You are provided with these local media files.",
            f"Use the {tool_name} tool to open each file before answering.",
        ]
        if resolved_images:
            lines.append("")
            lines.append("Images:")
            for image_path in resolved_images:
                lines.append(f"- {image_path}")
        if resolved_videos:
            lines.append("")
            lines.append("Videos:")
            for video_path in resolved_videos:
                lines.append(f"- {video_path}")
        lines.append("")
        lines.append("User request:")
        lines.append(prompt)
        return "\n".join(lines), resolved_images, resolved_videos

    def _build_symbolic_media_prompt(
        self,
        prompt: str,
        media_paths: list[Path],
    ) -> tuple[str, list[Path]]:
        if not media_paths:
            return prompt, []
        staged_paths = self._stage_media_files(media_paths)
        refs: list[str] = []
        for staged in staged_paths:
            try:
                rel_path = staged.relative_to(self.workdir).as_posix()
            except Exception:
                rel_path = staged.as_posix()
            refs.append(f"@{{{rel_path}}}")
        lines: list[str] = []
        lines.extend(refs)
        lines.append("")
        lines.append(prompt)
        return "\n".join(lines), staged_paths
