from __future__ import annotations

import abc
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


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
        return self._run_impl(
            prompt,
            images=images,
            videos=videos,
            reasoning_effort=reasoning_effort,
            model_override=model_override,
            base_env=base_env,
        )

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
            merged_env.update({k: v for k, v in env.items() if v})
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
            f"CAKIT_{self.name.upper()}_BIN",
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
        stamp = time.strftime("%Y%m%d-%H%M%S")
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
        stamp = time.strftime("%Y%m%d-%H%M%S")
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
        output_path = self._write_output(self.name, message)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=0.0,
            models_usage={},
            tool_calls=None,
            llm_calls=None,
            total_cost=None,
            telemetry_log=None,
            response=message,
            exit_code=2,
            output_path=str(output_path),
            raw_output=message,
            trajectory_path=None,
        )

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
        stage_dir = self.workdir / ".cakit-media"
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


from ..models import InstallResult, RunResult  # noqa: E402
