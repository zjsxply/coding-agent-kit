from __future__ import annotations

import abc
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from ..agent_runtime import command_exec as runtime_command
from ..agent_runtime import install_version as runtime_install
from ..agent_runtime import media as runtime_media
from ..agent_runtime import parsing as runtime_parsing
from ..agent_runtime import trajectory as runtime_trajectory
from ..models import InstallResult, RunResult
from ..stats_extract import StatsSnapshot, build_stats_snapshot, last_value

__all__ = [
    "AgentCapabilityError",
    "AgentConfigError",
    "AgentError",
    "CodingAgent",
    "CommandResult",
    "InstallStrategy",
    "ParsedStats",
    "RunCommandTemplate",
    "VersionCommandTemplate",
    "RunParseResult",
    "RunPlan",
]


class AgentError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        cakit_exit_code: int = 1,
        command_exit_code: Optional[int] = None,
        raw_output: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.cakit_exit_code = cakit_exit_code
        self.command_exit_code = command_exit_code
        self.raw_output = raw_output


class AgentConfigError(AgentError):
    def __init__(self, message: str, *, raw_output: Optional[str] = None) -> None:
        super().__init__(message, cakit_exit_code=1, raw_output=raw_output)


class AgentCapabilityError(AgentError):
    def __init__(self, message: str, *, raw_output: Optional[str] = None) -> None:
        super().__init__(message, cakit_exit_code=2, raw_output=raw_output)


@dataclass(frozen=True)
class ParsedStats:
    model_name: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    llm_calls: Optional[int] = None
    tool_calls: Optional[int] = None
    total_cost: Optional[float] = None
    response: Optional[str] = None


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


@dataclass(frozen=True)
class RunPlan:
    command: list[str]
    parse_output: Callable[[str, Any], "RunParseResult"]
    env: Optional[Dict[str, str]] = None
    input_text: Optional[str] = None
    timeout: Optional[int] = None
    unset_env: Optional[Iterable[str]] = None
    fallback_response: Optional[Callable[[str], Optional[str]]] = None
    post_finalize: Optional[Callable[[RunResult, "RunParseResult", Any], Optional[RunResult]]] = None


@dataclass(frozen=True)
class RunParseResult:
    response: Optional[str] = None
    models_usage: Dict[str, Dict[str, int]] = field(default_factory=dict)
    llm_calls: Optional[int] = None
    tool_calls: Optional[int] = None
    total_cost: Optional[float] = None
    telemetry_log: Optional[str] = None
    raw_output: Optional[str] = None
    runtime_seconds: Optional[float] = None
    trajectory_content: Optional[str] = None
    trajectory_source: Optional[str] = None


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

    def __init__(self, *, workdir: Optional[Path] = None) -> None:
        self.workdir = (workdir or Path.cwd()).expanduser().resolve()
        self._path_prefix_cache_key: Optional[str] = None
        self._path_prefix_cache: tuple[str, ...] = ()
        self._staged_media_dirs: set[Path] = set()
        self._ephemeral_temp_dirs: set[Path] = set()

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> "InstallResult":
        strategy = self.install_strategy
        if strategy is None:
            raise NotImplementedError(f"{self.__class__.__name__} must define install() or install_strategy")

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
            package_spec = runtime_install.build_install_package_spec(
                strategy.package,
                version,
                style=strategy.version_style,
            )
            result = runtime_install.uv_tool_install(
                package_spec=package_spec,
                python_version=strategy.python_version,
                force=strategy.force,
                with_packages=[pkg for pkg in strategy.with_packages if pkg],
                fallback_no_cache_dir=strategy.fallback_no_cache_dir,
                run=self._run,
                ensure_uv_fn=lambda: runtime_install.ensure_uv(self._run),
                pip_install_fn=lambda packages, no_cache: runtime_install.pip_install(
                    packages=packages,
                    no_cache_dir=no_cache,
                    run=self._run,
                ),
            )
        elif strategy.kind == "uv_pip":
            if not strategy.package:
                raise ValueError("install strategy kind=uv_pip requires package")
            package_spec = runtime_install.build_install_package_spec(
                strategy.package,
                version,
                style=strategy.version_style,
            )
            packages = [package_spec, *[pkg for pkg in strategy.with_packages if pkg]]
            result = runtime_install.uv_pip_install(
                packages=packages,
                no_cache_dir=strategy.no_cache_dir,
                run=self._run,
                ensure_uv_fn=lambda: runtime_install.ensure_uv(self._run),
                pip_install_fn=lambda packages, no_cache: runtime_install.pip_install(
                    packages=packages,
                    no_cache_dir=no_cache,
                    run=self._run,
                ),
            )
        elif strategy.kind == "shell":
            result = runtime_install.shell_install(
                shell_command=strategy.shell_command,
                shell_versioned_command=strategy.shell_versioned_command,
                shell_version_env=strategy.shell_version_env,
                version=version,
                version_normalizer=strategy.version_normalizer,
                run=self._run,
            )
        elif strategy.kind == "custom":
            result = self._install_with_custom_strategy(strategy=strategy, scope=scope, version=version)
        else:
            raise ValueError(f"unsupported install strategy: {strategy.kind}")
        if not isinstance(result, CommandResult):
            result = CommandResult(
                exit_code=getattr(result, "exit_code", 1),
                stdout=getattr(result, "stdout", ""),
                stderr=getattr(result, "stderr", ""),
                duration_seconds=getattr(result, "duration_seconds", 0.0),
            )

        config_path = self.configure()
        ok = result.exit_code == 0
        details = None if ok else result.output
        if ok and strategy.require_config and config_path is None:
            ok = False
            message = strategy.configure_failure_message or f"{self.name} configure failed"
            output = result.output
            details = f"{output}\n{message}" if output else message
        return InstallResult(
            agent=self.name,
            version=self.get_version() if ok else None,
            ok=ok,
            details=details,
            config_path=config_path,
        )

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
        """Template method with unified run flow and optional agent-level adapters."""
        result: RunResult
        try:
            rejected = self._reject_unsupported_media(images=images, videos=videos)
            if rejected:
                return rejected
            run_plan = self._build_run_plan(
                prompt,
                images=images,
                videos=videos,
                reasoning_effort=reasoning_effort,
                model_override=model_override,
                base_env=base_env,
            )
            if run_plan is not None:
                command_result = self._run(
                    run_plan.command,
                    env=run_plan.env,
                    input_text=run_plan.input_text,
                    timeout=run_plan.timeout,
                    unset_env=run_plan.unset_env,
                    base_env=base_env,
                )
                output = command_result.output
                parsed = run_plan.parse_output(output, command_result)
                response = parsed.response
                if response is None and run_plan.fallback_response is not None:
                    response = run_plan.fallback_response(output)
                if response is None:
                    response = runtime_parsing.last_stdout_line(output)
                result = self.finalize_run(
                    command_result=command_result,
                    response=response,
                    models_usage=parsed.models_usage,
                    llm_calls=parsed.llm_calls,
                    tool_calls=parsed.tool_calls,
                    total_cost=parsed.total_cost,
                    telemetry_log=parsed.telemetry_log,
                    raw_output=parsed.raw_output,
                    runtime_seconds=parsed.runtime_seconds,
                    trajectory_content=parsed.trajectory_content,
                    trajectory_source=parsed.trajectory_source,
                )
                if run_plan.post_finalize is not None:
                    post_processed = run_plan.post_finalize(result, parsed, command_result)
                    if post_processed is not None:
                        result = post_processed
            else:
                result = self._run_impl(
                    prompt,
                    images=images,
                    videos=videos,
                    reasoning_effort=reasoning_effort,
                    model_override=model_override,
                    base_env=base_env,
                )
        except AgentError as error:
            result = self._build_error_run_result(
                message=error.message,
                cakit_exit_code=error.cakit_exit_code,
                command_exit_code=error.command_exit_code,
                raw_output=error.raw_output,
            )
        finally:
            runtime_media.cleanup_staged_media(self._staged_media_dirs)
            runtime_command.cleanup_dirs(self._ephemeral_temp_dirs)
        return self._postprocess_run_result(result)

    def _build_run_plan(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> Optional[RunPlan]:
        return None

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> "RunResult":
        """Agent-specific run implementation when no pipeline spec is provided."""
        return self._build_error_run_result(
            message=f"{self.display_name} run implementation is not configured.",
            cakit_exit_code=1,
        )

    def _build_templated_run_plan(
        self,
        *,
        parse_output,
        post_finalize=None,
        prompt: str,
        model: Optional[str] = None,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        env: Optional[Dict[str, str]] = None,
        input_text: Optional[str] = None,
        timeout: Optional[int] = None,
        unset_env: Optional[Iterable[str]] = None,
        extra_args: Optional[Iterable[str]] = None,
        fallback_response=None,
        template: Optional[RunCommandTemplate] = None,
    ) -> RunPlan:
        selected_template = template or self.run_template
        if selected_template is None:
            raise ValueError(f"{self.display_name} does not declare run_template")
        cmd, _ = self._build_templated_command(
            template=selected_template,
            prompt=prompt,
            model=model,
            images=images,
            videos=videos,
            extra_args=extra_args,
        )

        return RunPlan(
            command=cmd,
            parse_output=parse_output,
            env=env,
            input_text=input_text,
            timeout=timeout,
            unset_env=unset_env,
            fallback_response=fallback_response,
            post_finalize=post_finalize,
        )

    def get_version(self) -> Optional[str]:
        template = self.version_template
        if template is None:
            binary = self.binary or self.name
            result = self._run([binary, "--version"])
            if result.exit_code != 0:
                return None
            return runtime_parsing.first_nonempty_line(result.output)

        env: Optional[Dict[str, str]] = None
        if template.env_mode == "runtime_assets":
            env = self._runtime_asset_env(create_if_missing=False)
            if env is None:
                return None
        elif template.env_mode != "default":
            return None

        result = self._run(template.args, env=env)
        if result.exit_code != 0:
            return None
        return runtime_install.parse_version_output(
            parse_mode=template.parse_mode,
            output=result.output,
            prefix=template.prefix,
            regex=template.regex,
            regex_group=template.regex_group,
            json_key=template.json_key,
            json_path=template.json_path,
            first_nonempty_line=runtime_parsing.first_nonempty_line,
            parse_json=runtime_parsing.parse_json,
            select_last_value=last_value,
        )

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
        strategy = self.install_strategy
        if strategy is None:
            return ()
        runtime_by_install_kind = {
            "npm": ("node",),
            "uv_tool": ("uv",),
            "uv_pip": ("uv",),
        }
        return runtime_by_install_kind.get(strategy.kind, ())

    def _runtime_asset_env(
        self,
        *,
        create_if_missing: bool = True,
    ) -> Optional[Dict[str, str]]:
        return None

    def _run(
        self,
        args: Iterable[str],
        env: Optional[Dict[str, str]] = None,
        input_text: Optional[str] = None,
        timeout: Optional[int] = None,
        unset_env: Optional[Iterable[str]] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> CommandResult:
        exit_code, captured_stdout, captured_stderr, duration = runtime_command.run_command(
            args=args,
            workdir=self.workdir,
            env=env,
            input_text=input_text,
            timeout=timeout,
            unset_env=unset_env,
            base_env=base_env,
            path_prefixes=self._runtime_path_prefixes(),
        )
        return CommandResult(
            exit_code=exit_code,
            stdout=captured_stdout,
            stderr=captured_stderr,
            duration_seconds=duration,
        )

    def is_installed(self) -> bool:
        if not self.binary:
            return True
        return (
            runtime_command.resolve_binary(
                agent_name=self.name,
                binary=self.binary,
                npm_prefix=self._npm_prefix(),
                env_source=os.environ,
            )
            is not None
        )

    def _npm_prefix(self) -> Path:
        prefix = os.environ.get("CAKIT_NPM_PREFIX")
        if prefix:
            return Path(prefix).expanduser()
        return Path.home() / ".npm-global"

    def _runtime_path_prefixes(self) -> tuple[str, ...]:
        cache_key = os.environ.get("CAKIT_NPM_PREFIX", "")
        if self._path_prefix_cache_key == cache_key:
            return self._path_prefix_cache
        self._path_prefix_cache = runtime_command.build_runtime_path_prefixes(cache_key)
        self._path_prefix_cache_key = cache_key
        return self._path_prefix_cache

    def _make_temp_dir(self, *, prefix: str, keep: bool = False) -> Path:
        return runtime_command.make_temp_dir(
            prefix=prefix,
            keep=keep,
            env_source=os.environ,
            ephemeral_dirs=self._ephemeral_temp_dirs,
        )

    def _install_with_npm(
        self,
        *,
        package: str,
        scope: str,
        version: Optional[str],
        require_config: bool = False,
        configure_failure_message: Optional[str] = None,
    ) -> "InstallResult":
        package_spec = runtime_install.build_install_package_spec(package, version, style="npm")
        if scope == "global":
            result = self._run(["npm", "install", "-g", package_spec])
        else:
            prefix = self._npm_prefix()
            prefix.mkdir(parents=True, exist_ok=True)
            result = self._run(["npm", "install", "-g", "--prefix", str(prefix), package_spec])
        config_path = self.configure()
        ok = result.exit_code == 0
        details = None if ok else result.output
        if ok and require_config and config_path is None:
            ok = False
            message = configure_failure_message or f"{self.name} configure failed"
            output = result.output
            details = f"{output}\n{message}" if output else message
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
        output_path = self._write_output_artifact(self.name, message, suffix=".log")
        trajectory_path = self._write_output_artifact(
            self.name,
            runtime_trajectory.format_trace_text(message, source=str(output_path)),
            suffix=".trajectory.log",
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

    @staticmethod
    def _normalize_stats_snapshot(
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

    def _postprocess_run_result(self, result: RunResult) -> RunResult:
        snapshot = self._normalize_stats_snapshot(
            models_usage=result.models_usage or {},
            llm_calls=result.llm_calls,
            tool_calls=result.tool_calls,
            total_cost=result.total_cost,
        )
        result.models_usage = snapshot.models_usage
        result.llm_calls = snapshot.llm_calls
        result.tool_calls = snapshot.tool_calls
        result.total_cost = snapshot.total_cost
        if result.cakit_exit_code is None and result.command_exit_code is not None:
            result.cakit_exit_code = self._resolve_strict_run_exit_code(
                command_exit_code=result.command_exit_code,
                models_usage=result.models_usage or {},
                llm_calls=result.llm_calls,
                tool_calls=result.tool_calls,
                response=result.response,
            )
        return result

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
        output_path = self._write_output_artifact(self.name, output, suffix=".log")
        trace_content = trajectory_content
        if trace_content is None:
            source = trajectory_source or str(output_path)
            trace_content = runtime_trajectory.format_trace_text(output, source=source)
        trajectory_path = (
            self._write_output_artifact(self.name, trace_content, suffix=".trajectory.log")
            if trace_content
            else None
        )
        return RunResult(
            agent=self.name,
            agent_version=agent_version if agent_version is not None else self.get_version(),
            runtime_seconds=command_result.duration_seconds if runtime_seconds is None else runtime_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            total_cost=total_cost,
            telemetry_log=telemetry_log,
            response=response,
            cakit_exit_code=None,
            command_exit_code=command_result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

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
            run_prompt, _ = self._build_symbolic_media_prompt(
                prompt,
                [*image_list, *video_list],
            )
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

    def _build_natural_media_prompt(
        self,
        prompt: str,
        *,
        images: Optional[list[Path]],
        videos: Optional[list[Path]],
        tool_name: str,
    ) -> tuple[str, list[Path], list[Path]]:
        image_paths = images or []
        video_paths = videos or []
        all_media_paths = [*image_paths, *video_paths]
        if not all_media_paths:
            return prompt, [], []
        try:
            staged_paths = runtime_media.stage_media_files(
                all_media_paths,
                staged_media_dirs=self._staged_media_dirs,
                stage_root=self.workdir / ".cakit-media",
            )
        except runtime_media.MediaStageError as exc:
            self._raise_config_error(str(exc))
        split_index = len(image_paths)
        resolved_images = staged_paths[:split_index]
        resolved_videos = staged_paths[split_index:]

        lines: list[str] = [
            "You are provided with these local media files.",
            (
                f"Use the {tool_name} tool to inspect each file before answering when that tool is available. "
                "If it is unavailable, or if its output is insufficient, use the appropriate available tools to "
                "inspect the files directly, extract any visible text, and continue without asking for more setup."
            ),
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

    def _build_native_media_prompt(
        self,
        prompt: str,
        *,
        images: Optional[list[Path]],
        videos: Optional[list[Path]],
        tool_name: str,
    ) -> tuple[str, list[Path], list[Path]]:
        image_paths = images or []
        video_paths = videos or []
        all_media_paths = [*image_paths, *video_paths]
        if not all_media_paths:
            return prompt, [], []
        try:
            staged_paths = runtime_media.stage_media_files(
                all_media_paths,
                staged_media_dirs=self._staged_media_dirs,
                stage_root=self.workdir / ".cakit-media",
            )
        except runtime_media.MediaStageError as exc:
            self._raise_config_error(str(exc))
        split_index = len(image_paths)
        resolved_images = staged_paths[:split_index]
        resolved_videos = staged_paths[split_index:]

        lines: list[str] = [
            "You are provided with these local media files.",
            (
                f"Use the {tool_name} tool or the model's native multimodal capability to inspect each file."
            ),
            (
                "Rely only on native multimodal support. If the current model/tooling cannot natively inspect "
                "the provided media, report that limitation instead of using OCR, ffmpeg, python, shell commands, "
                "or other non-native fallbacks."
            ),
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

        try:
            staged_paths = runtime_media.stage_media_files(
                media_paths,
                staged_media_dirs=self._staged_media_dirs,
                stage_root=self.workdir / ".cakit-media",
            )
        except runtime_media.MediaStageError as exc:
            self._raise_config_error(str(exc))
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

    def _resolve_writable_dir(self, *candidates: Path, purpose: str) -> Path:
        for directory in candidates:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                probe = directory / f".write-test-{os.getpid()}-{time.time_ns()}"
                probe.write_text("", encoding="utf-8")
                try:
                    probe.unlink()
                except FileNotFoundError:
                    pass
                return directory
            except OSError:
                continue
        raise AgentError(f"unable to create writable {purpose} directory")

    def _output_dir(self) -> Path:
        root = os.environ.get("CAKIT_OUTPUT_DIR")
        candidates = [Path(root)] if root else [Path.home() / ".cache" / "cakit", Path("/tmp") / "cakit"]
        return self._resolve_writable_dir(*candidates, purpose="cakit output")

    def _write_output_artifact(self, agent: str, content: str, *, suffix: str) -> Path:
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
        path = self._output_dir() / f"{agent}-{stamp}{suffix}"
        path.write_text(content, encoding="utf-8")
        return path

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
        self._raise_capability_error(message)
        return None

    @staticmethod
    def _raise_config_error(message: str) -> None:
        raise AgentConfigError(message)

    @staticmethod
    def _raise_capability_error(message: str) -> None:
        raise AgentCapabilityError(message)

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
