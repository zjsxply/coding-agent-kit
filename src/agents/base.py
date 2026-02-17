from __future__ import annotations

import abc
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ..models import InstallResult, RunResult
from ..utils import format_trace_text


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


class CodingAgent(abc.ABC):
    name: str
    display_name: str
    binary: Optional[str] = None
    supports_images: bool = False
    supports_videos: bool = False

    def __init__(self, *, workdir: Optional[Path] = None) -> None:
        self.workdir = (workdir or Path.cwd()).expanduser().resolve()

    @abc.abstractmethod
    def install(self, *, scope: str = "user", version: Optional[str] = None) -> "InstallResult":
        raise NotImplementedError

    @abc.abstractmethod
    def configure(self) -> Optional[str]:
        raise NotImplementedError

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
        result = self._run_impl(
            prompt,
            images=images,
            videos=videos,
            reasoning_effort=reasoning_effort,
            model_override=model_override,
            base_env=base_env,
        )
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

    @abc.abstractmethod
    def get_version(self) -> Optional[str]:
        raise NotImplementedError

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
        extra_paths = self._extra_path_entries()
        if extra_paths:
            current_path = merged_env.get("PATH", "")
            merged_env["PATH"] = os.pathsep.join(extra_paths + ([current_path] if current_path else []))
        start = time.monotonic()
        try:
            result = subprocess.run(
                list(args),
                cwd=str(self.workdir),
                env=merged_env,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
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
            stdout=result.stdout or "",
            stderr=result.stderr or "",
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

    def _npm_install(self, package: str, scope: str, version: Optional[str] = None) -> CommandResult:
        package_spec = package
        if version:
            normalized = version.strip()
            if normalized:
                if normalized.startswith("@"):
                    package_spec = f"{package}{normalized}"
                else:
                    package_spec = f"{package}@{normalized}"
        if scope == "global":
            return self._run(["npm", "install", "-g", package_spec])
        prefix = self._npm_prefix()
        prefix.mkdir(parents=True, exist_ok=True)
        return self._run(["npm", "install", "-g", "--prefix", str(prefix), package_spec])

    def _install_with_npm(
        self,
        *,
        package: str,
        scope: str,
        version: Optional[str],
        require_config: bool = False,
        configure_failure_message: Optional[str] = None,
    ) -> "InstallResult":
        result = self._npm_install(package, scope, version=version)
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

    def _extra_path_entries(self) -> list[str]:
        candidates = [
            self._npm_prefix() / "bin",
            Path.home() / ".npm" / "bin",
            Path.home() / ".local" / "bin",
        ]
        entries = []
        for path in candidates:
            if path.exists():
                entries.append(str(path))
        return entries

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

    def _version_text(
        self,
        args: Iterable[str],
        *,
        env: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        result = self._run(args, env=env)
        if result.exit_code != 0:
            return None
        text = result.output.strip()
        if not text:
            return None
        return text

    def _version_first_line(
        self,
        args: Iterable[str],
        *,
        env: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        text = self._version_text(args, env=env)
        return self._first_nonempty_line(text)

    @staticmethod
    def _first_nonempty_line(text: Optional[str]) -> Optional[str]:
        if not isinstance(text, str):
            return None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line:
                return line
        return None

    @staticmethod
    def _second_token_if_prefixed(first_line: Optional[str], *, prefix: str) -> Optional[str]:
        if not isinstance(first_line, str):
            return None
        parts = first_line.split()
        if len(parts) < 2:
            return None
        if not parts[0].lower().startswith(prefix.lower()):
            return None
        value = parts[1].strip()
        if not value:
            return None
        return value

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
        if not usage:
            return {}
        name = default_model or "unknown"
        prompt = usage.get("prompt_tokens") or 0
        completion = usage.get("completion_tokens") or 0
        total = usage.get("total_tokens")
        if total is None:
            total = prompt + completion
        return {
            name: {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
            }
        }

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _read_text(self, path: Path) -> Optional[str]:
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def _write_output(self, agent: str, output: str) -> Path:
        root = os.environ.get("CAKIT_OUTPUT_DIR")
        if root:
            output_dir = Path(root)
        else:
            output_dir = Path.home() / ".cache" / "cakit"
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
        path = output_dir / f"{agent}-{stamp}.log"
        path.write_text(output, encoding="utf-8")
        return path

    def _write_trajectory(self, agent: str, content: Optional[str]) -> Optional[Path]:
        if not content:
            return None
        root = os.environ.get("CAKIT_OUTPUT_DIR")
        if root:
            output_dir = Path(root)
        else:
            output_dir = Path.home() / ".cache" / "cakit"
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
        path = output_dir / f"{agent}-{stamp}.trajectory.log"
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
        return self._build_error_run_result(message=message, cakit_exit_code=2)

    @staticmethod
    def _stdout_only(output: str) -> str:
        marker = "----- STDERR -----"
        if marker in output:
            return output.split(marker, 1)[0]
        return output

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
    def _missing_env_message(missing: list[str]) -> Optional[str]:
        if not missing:
            return None
        return f"missing required environment variable(s): {', '.join(missing)}"

    def _extract_gemini_style_stats(
        self,
        payload: Optional[Dict[str, Any]],
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int]]:
        if not isinstance(payload, dict):
            return {}, None, None

        stats = payload.get("stats")
        if not isinstance(stats, dict):
            return {}, None, None

        models = stats.get("models")
        if not isinstance(models, dict) or not models:
            return {}, None, None

        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls = 0
        for model_name, model_stats in models.items():
            if not isinstance(model_name, str) or not model_name.strip():
                return {}, None, None
            usage, model_calls = self._extract_gemini_style_model_usage(model_stats)
            if usage is None or model_calls is None:
                return {}, None, None
            models_usage[model_name] = usage
            llm_calls += model_calls

        tools = stats.get("tools")
        if not isinstance(tools, dict):
            return {}, None, None
        tool_calls = self._as_int(tools.get("totalCalls"))
        if tool_calls is None:
            return {}, None, None

        return models_usage, llm_calls, tool_calls

    def _extract_gemini_style_model_usage(self, model_stats: Any) -> tuple[Optional[Dict[str, int]], Optional[int]]:
        if not isinstance(model_stats, dict):
            return None, None
        usage = self._extract_gemini_style_tokens(model_stats.get("tokens"))
        llm_calls = self._extract_gemini_style_total_requests(model_stats.get("api"))
        if usage is None or llm_calls is None:
            return None, None
        return usage, llm_calls

    def _extract_gemini_style_tokens(self, tokens: Any) -> Optional[Dict[str, int]]:
        if not isinstance(tokens, dict):
            return None
        prompt = self._as_int(tokens.get("prompt"))
        completion = self._as_int(tokens.get("candidates"))
        total = self._as_int(tokens.get("total"))
        if prompt is None or completion is None or total is None:
            return None
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }

    def _extract_gemini_style_total_requests(self, api: Any) -> Optional[int]:
        if not isinstance(api, dict):
            return None
        return self._as_int(api.get("totalRequests"))

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
        stage_dir = self.workdir / ".cakit-media" / run_stage
        stage_dir.mkdir(parents=True, exist_ok=True)
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
