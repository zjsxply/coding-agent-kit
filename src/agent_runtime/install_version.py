from __future__ import annotations

import json
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


def build_install_package_spec(package: str, version: Optional[str], *, style: str) -> str:
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


def shell_install(
    *,
    shell_command: Optional[str],
    shell_versioned_command: Optional[str],
    shell_version_env: Optional[str],
    version: Optional[str],
    version_normalizer: str,
    run: Callable[[Iterable[str]], Any],
):
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
        return run(["bash", "-lc", "echo 'shell install strategy missing command template' >&2; exit 1"])

    if normalized_version:
        quoted_version = shlex.quote(normalized_version)
        command = command.format(version=normalized_version, version_quoted=quoted_version)
        if shell_version_env:
            command = f"{shell_version_env}={quoted_version} {command}"
    return run(["bash", "-lc", command])


def parse_version_output(
    *,
    parse_mode: str,
    output: str,
    prefix: Optional[str] = None,
    regex: Optional[str] = None,
    regex_group: int = 1,
    json_key: Optional[str] = None,
    json_path: Optional[str] = None,
    first_nonempty_line: Callable[[Optional[str]], Optional[str]],
    parse_json: Callable[[str], Optional[Any]],
    select_last_value: Callable[[Any, str], Any],
) -> Optional[str]:
    text = output.strip()
    if not text:
        return None

    if parse_mode == "text":
        return text

    if parse_mode == "json_key":
        return parse_version_json_path(
            text,
            key=json_key,
            parse_json=parse_json,
            select_last_value=select_last_value,
        )

    if parse_mode == "json_path":
        return parse_version_json_path(
            text,
            path=json_path,
            parse_json=parse_json,
            select_last_value=select_last_value,
        )

    line = first_nonempty_line(text)
    if line is None:
        return None

    if parse_mode == "first_line":
        return line

    if parse_mode == "prefixed_second_token":
        if not prefix:
            return None
        parts = line.split()
        if len(parts) < 2 or not parts[0].lower().startswith(prefix.lower()):
            return None
        value = parts[1].strip()
        return value or None

    if parse_mode == "prefixed_remainder":
        if not prefix or not line.lower().startswith(prefix.lower()):
            return None
        remainder = line[len(prefix) :].strip()
        return remainder or None

    if parse_mode == "regex_first_line":
        if not regex:
            return None
        match = re.search(regex, line)
        if not match:
            return None
        value = match.group(regex_group)
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    return None


def parse_version_json_path(
    text: str,
    *,
    path: Optional[str] = None,
    key: Optional[str] = None,
    parse_json: Callable[[str], Optional[Any]],
    select_last_value: Callable[[Any, str], Any],
) -> Optional[str]:
    payload = parse_json(text)
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

    value = select_last_value(payload, resolved_path)
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    cleaned = str(value).strip()
    return cleaned or None


def ensure_uv(run: Callable[[Iterable[str]], Any]) -> bool:
    if shutil.which("uv") is not None:
        return True
    if not sys.platform.startswith("linux"):
        return False
    if shutil.which("curl") is None:
        return False
    install = run(["bash", "-lc", "curl -LsSf https://astral.sh/uv/install.sh | sh"])
    if getattr(install, "exit_code", 1) != 0:
        return False
    return shutil.which("uv") is not None or (Path.home() / ".local" / "bin" / "uv").exists()


def pip_install(
    *,
    packages: list[str],
    no_cache_dir: bool,
    run: Callable[[Iterable[str]], Any],
):
    cmd = ["python", "-m", "pip", "install"]
    if no_cache_dir:
        cmd.append("--no-cache-dir")
    cmd.extend(packages)
    return run(cmd)


def uv_tool_install(
    *,
    package_spec: str,
    python_version: Optional[str],
    force: bool,
    with_packages: Optional[list[str]],
    fallback_no_cache_dir: bool,
    run: Callable[[Iterable[str]], Any],
    ensure_uv_fn: Callable[[], bool],
    pip_install_fn: Callable[[list[str], bool], Any],
):
    extras = [pkg for pkg in (with_packages or []) if pkg]
    if ensure_uv_fn():
        cmd = ["uv", "tool", "install"]
        if force:
            cmd.append("--force")
        if python_version:
            cmd.extend(["--python", python_version])
        for pkg in extras:
            cmd.extend(["--with", pkg])
        cmd.append(package_spec)
        return run(cmd)
    return pip_install_fn([package_spec, *extras], fallback_no_cache_dir)


def uv_pip_install(
    *,
    packages: list[str],
    no_cache_dir: bool,
    run: Callable[[Iterable[str]], Any],
    ensure_uv_fn: Callable[[], bool],
    pip_install_fn: Callable[[list[str], bool], Any],
):
    if ensure_uv_fn():
        cmd = ["uv", "pip", "install"]
        if no_cache_dir:
            cmd.append("--no-cache-dir")
        cmd.extend(packages)
        return run(cmd)
    return pip_install_fn(packages, no_cache_dir)
