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
            return f"{self.stdout}\n{self.stderr}"
        return self.stdout or self.stderr


class CodeAgent(abc.ABC):
    name: str
    display_name: str
    binary: Optional[str] = None

    def __init__(self, *, workdir: Optional[Path] = None) -> None:
        self.workdir = (workdir or Path.cwd()).expanduser().resolve()

    @abc.abstractmethod
    def install(self) -> "InstallResult":
        raise NotImplementedError

    @abc.abstractmethod
    def configure(self) -> Optional[str]:
        raise NotImplementedError

    @abc.abstractmethod
    def run(self, prompt: str, images: Optional[list[Path]] = None) -> "RunResult":
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
    ) -> CommandResult:
        merged_env = os.environ.copy()
        if env:
            merged_env.update({k: v for k, v in env.items() if v})
        start = time.monotonic()
        result = subprocess.run(
            list(args),
            cwd=str(self.workdir),
            env=merged_env,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
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
        return shutil.which(self.binary) is not None

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


from ..models import InstallResult, RunResult  # noqa: E402
