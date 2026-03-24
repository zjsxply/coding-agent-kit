from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, Optional


def _default_install_home(env_source: Dict[str, str]) -> Path:
    install_home = env_source.get("CAKIT_INSTALL_HOME")
    if install_home:
        return Path(install_home).expanduser()
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return Path("/opt/cakit")
    return Path.home() / ".local" / "share" / "cakit"


def _node_install_bin_dirs(env_source: Dict[str, str]) -> tuple[str, ...]:
    node_root = _default_install_home(env_source) / "node"
    if not node_root.exists():
        return ()
    return tuple(str(path) for path in sorted(node_root.glob("*/bin"), reverse=True) if path.is_dir())


def build_runtime_path_prefixes(cache_key: str) -> tuple[str, ...]:
    npm_prefix_env = os.environ.get("CAKIT_NPM_PREFIX")
    npm_prefix = Path(npm_prefix_env).expanduser() if npm_prefix_env else Path.home() / ".npm-global"
    uv_tool_bin = os.environ.get("UV_TOOL_BIN_DIR")
    xdg_bin_home = os.environ.get("XDG_BIN_HOME")
    install_uv_dir = os.environ.get("CAKIT_INSTALL_UV_DIR")
    cakit_uv_root = (
        Path(install_uv_dir).expanduser()
        if install_uv_dir
        else _default_install_home(os.environ) / "uv"
    )
    default_bin_home = Path(xdg_bin_home).expanduser() if xdg_bin_home else Path.home() / ".local" / "bin"
    node_bin_dirs = _node_install_bin_dirs(os.environ)
    return tuple(
        dict.fromkeys(
            (
                str(Path(uv_tool_bin).expanduser()) if uv_tool_bin else str(default_bin_home),
                str(default_bin_home),
                str(cakit_uv_root),
                *node_bin_dirs,
                str(npm_prefix / "bin"),
                str(Path.home() / ".npm" / "bin"),
                str(Path.home() / ".local" / "bin"),
                str(Path("/tmp") / "cakit" / "bin"),
            )
        )
    )


def run_command(
    *,
    args: Iterable[str],
    workdir: Path,
    env: Optional[Dict[str, str]] = None,
    input_text: Optional[str] = None,
    timeout: Optional[int] = None,
    unset_env: Optional[Iterable[str]] = None,
    base_env: Optional[Dict[str, str]] = None,
    path_prefixes: tuple[str, ...] = (),
) -> tuple[int, str, str, float]:
    merged_env = dict(base_env) if base_env is not None else os.environ.copy()
    if unset_env:
        for key in unset_env:
            merged_env.pop(key, None)
    if env:
        merged_env.update({k: v for k, v in env.items() if v is not None})

    if path_prefixes:
        current_path = merged_env.get("PATH", "")
        merged_env["PATH"] = os.pathsep.join(path_prefixes + ((current_path,) if current_path else ()))

    start = time.monotonic()
    command_args = list(args)
    try:
        with (
            tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout_file,
            tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file,
        ):
            result = subprocess.run(
                command_args,
                cwd=str(workdir),
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
        return 127, "", str(exc), duration
    duration = time.monotonic() - start
    return result.returncode, captured_stdout, captured_stderr, duration


def resolve_binary(
    *,
    agent_name: str,
    binary: Optional[str],
    npm_prefix: Path,
    env_source: Dict[str, str],
) -> Optional[str]:
    if not binary:
        return None

    env_keys = (
        f"{agent_name.upper()}_BIN",
        f"{binary.upper()}_BIN",
    )
    for key in env_keys:
        value = env_source.get(key)
        if value:
            candidate = Path(value).expanduser()
            if candidate.exists():
                return str(candidate)

    path = shutil.which(binary)
    if path:
        return path

    extra_bin_candidates = []
    xdg_bin_home = env_source.get("XDG_BIN_HOME")
    if xdg_bin_home:
        extra_bin_candidates.append(Path(xdg_bin_home).expanduser())
    uv_tool_bin = env_source.get("UV_TOOL_BIN_DIR")
    if uv_tool_bin:
        extra_bin_candidates.append(Path(uv_tool_bin).expanduser())
    extra_bin_candidates.append(Path("/tmp") / "cakit" / "bin")
    extra_bin_candidates.extend(Path(path) for path in _node_install_bin_dirs(env_source))

    for folder in (
        *extra_bin_candidates,
        npm_prefix / "bin",
        Path.home() / ".npm" / "bin",
        Path.home() / ".local" / "bin",
    ):
        candidate = folder / binary
        if candidate.exists():
            return str(candidate)
    return None


def keep_temp_dirs(env_source: Dict[str, str]) -> bool:
    raw_value = env_source.get("CAKIT_KEEP_TEMP_DIRS")
    if not isinstance(raw_value, str):
        return False
    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}


def make_temp_dir(
    *,
    prefix: str,
    keep: bool,
    env_source: Dict[str, str],
    ephemeral_dirs: set[Path],
) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))
    if not keep and not keep_temp_dirs(env_source):
        ephemeral_dirs.add(path)
    return path


def cleanup_dirs(paths: set[Path]) -> None:
    if not paths:
        return
    to_remove = tuple(paths)
    paths.clear()
    for path in to_remove:
        shutil.rmtree(path, ignore_errors=True)
