from __future__ import annotations

import abc
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional


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
    def install(self, *, scope: str = "user") -> "InstallResult":
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
            base_env=base_env,
        )

    @abc.abstractmethod
    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
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

    def _npm_install(self, package: str, scope: str) -> CommandResult:
        if scope == "global":
            return self._run(["npm", "install", "-g", package])
        prefix = self._npm_prefix()
        prefix.mkdir(parents=True, exist_ok=True)
        return self._run(["npm", "install", "-g", "--prefix", str(prefix), package])

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
        )


from ..models import InstallResult, RunResult  # noqa: E402
