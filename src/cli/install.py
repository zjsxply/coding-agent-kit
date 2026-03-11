from __future__ import annotations

import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

try:
    import fcntl
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from ..agents import create_agent, list_agents
from ..io_helpers import emit_json
from ..models import InstallResult


ALL_AGENT_SELECTORS = {"*", "all"}
TargetCommandResult = tuple[bool, dict[str, object]]


def resolve_agent_targets(agent_name: str) -> list[str]:
    key = agent_name.strip().lower()
    available_agents = list_agents()
    if key in ALL_AGENT_SELECTORS:
        return list(available_agents)
    if key in available_agents:
        return [key]
    raise ValueError(f"Unsupported agent: {agent_name}")


def _resolve_targets_or_emit(agent_name: str) -> Optional[list[str]]:
    try:
        return resolve_agent_targets(agent_name)
    except ValueError as exc:
        emit_json({"error": str(exc), "supported_agents": list(list_agents())})
        return None


def _run_for_targets(agent_name: str, target_runner: Callable[[str], TargetCommandResult]) -> int:
    targets = _resolve_targets_or_emit(agent_name)
    if targets is None:
        return 2

    results = [target_runner(target) for target in targets]
    if len(results) == 1:
        ok, payload = results[0]
        emit_json(payload)
        return 0 if ok else 1

    emit_json(
        {
            "agent": agent_name,
            "resolved_agents": targets,
            "ok": all(ok for ok, _ in results),
            "results": [payload for _, payload in results],
        }
    )
    return 0 if all(ok for ok, _ in results) else 1


def _install_target(target: str, *, scope: str, version: Optional[str]) -> TargetCommandResult:
    install_result = install_agent(target, scope=scope, version=version)
    return install_result.ok, {
        "agent": install_result.agent,
        "ok": install_result.ok,
        "version": install_result.version,
        "config_path": install_result.config_path,
        "details": install_result.details,
    }


def _configure_target(target: str) -> TargetCommandResult:
    config_path = create_agent(target).configure()
    payload: dict[str, object] = {
        "agent": target,
        "ok": True,
        "config_path": config_path,
    }
    if config_path is None:
        payload["details"] = "no config written"
    return True, payload


@contextmanager
def file_lock(name: str) -> Iterator[None]:
    if fcntl is None:
        yield
        return
    lock_root = Path("/tmp") / "cakit-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{name}.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def run_logged_command(
    prefix: str,
    cmd: list[str],
    *,
    input_text: Optional[str] = None,
    quiet_success: bool = False,
) -> bool:
    if not quiet_success:
        print(f"{prefix} {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            check=False,
            input=input_text,
            text=True,
        )
        return result.returncode == 0

    result = subprocess.run(
        cmd,
        check=False,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        print(f"{prefix} {' '.join(cmd)}")
        if result.stdout:
            sys.stdout.write(result.stdout)
            if not result.stdout.endswith("\n"):
                sys.stdout.write("\n")
    return result.returncode == 0


def with_sudo(cmd: list[str], *, use_sudo: bool, preserve_env: bool = False) -> list[str]:
    if not use_sudo:
        return cmd
    if preserve_env:
        return ["sudo", "-E", *cmd]
    return ["sudo", *cmd]


def ensure_node_tools(*, quiet_success: bool = False) -> bool:
    if shutil.which("node") is None or shutil.which("npm") is None:
        print("[deps] nodejs/npm not found, attempting auto-install (Linux + apt-get required).")
        return install_node_linux(quiet_success=quiet_success)
    return True


def ensure_dependencies(agent_name: str) -> bool:
    required_runtimes = create_agent(agent_name).runtime_dependencies()
    ok = True
    for runtime_name in required_runtimes:
        if runtime_name == "node":
            with file_lock("deps-node"):
                if not ensure_node_tools():
                    ok = False
            continue
        if runtime_name == "uv":
            with file_lock("deps-uv"):
                if shutil.which("uv") is None:
                    print("[deps] uv not found, attempting auto-install.")
                    ok = install_uv_linux() and ok
            continue
        print(f"[deps] unsupported runtime dependency for {agent_name}: {runtime_name}")
        ok = False
    return ok


def install_agent(agent_name: str, scope: str, version: Optional[str] = None) -> InstallResult:
    with file_lock(f"install-{agent_name}"):
        if not ensure_dependencies(agent_name):
            return InstallResult(
                agent=agent_name,
                version=None,
                ok=False,
                details="dependency install failed",
                config_path=None,
            )
        agent = create_agent(agent_name)
        return agent.install(scope=scope, version=version)


def install_node_linux(*, quiet_success: bool = False) -> bool:
    if not sys.platform.startswith("linux") or shutil.which("apt-get") is None:
        print("[deps] unsupported OS for auto-install; please install Node.js manually.")
        return False
    use_sudo = os.geteuid() != 0
    if use_sudo and shutil.which("sudo") is None:
        print("[deps] sudo not found; run as root to auto-install Node.js.")
        return False
    if shutil.which("curl") is None:
        if not run_logged_command(
            "[deps]",
            with_sudo(["apt-get", "update"], use_sudo=use_sudo),
            quiet_success=quiet_success,
        ):
            return False
        if not run_logged_command(
            "[deps]",
            with_sudo(["apt-get", "install", "-y", "curl", "ca-certificates"], use_sudo=use_sudo),
            quiet_success=quiet_success,
        ):
            return False

    setup_script = subprocess.run(
        ["curl", "-fsSL", "https://deb.nodesource.com/setup_22.x"],
        capture_output=True,
        text=True,
        check=False,
    )
    if setup_script.returncode != 0:
        return False
    if not run_logged_command(
        "[deps]",
        with_sudo(["bash", "-"], use_sudo=use_sudo, preserve_env=True),
        input_text=setup_script.stdout,
        quiet_success=quiet_success,
    ):
        return False
    if not run_logged_command(
        "[deps]",
        with_sudo(["apt-get", "install", "-y", "nodejs"], use_sudo=use_sudo),
        quiet_success=quiet_success,
    ):
        return False
    return True


def install_uv_linux() -> bool:
    if not sys.platform.startswith("linux"):
        print("[deps] unsupported OS for auto-install; please install uv manually.")
        return False
    use_sudo = os.geteuid() != 0
    if use_sudo and shutil.which("sudo") is None:
        print("[deps] sudo not found; run as root to auto-install uv prerequisites.")
        return False
    if shutil.which("curl") is None and shutil.which("apt-get") is not None:
        if not run_logged_command("[deps]", with_sudo(["apt-get", "update"], use_sudo=use_sudo)):
            return False
        if not run_logged_command("[deps]", with_sudo(["apt-get", "install", "-y", "curl"], use_sudo=use_sudo)):
            return False

    install_script = subprocess.run(
        ["curl", "-LsSf", "https://astral.sh/uv/install.sh"],
        capture_output=True,
        text=True,
        check=False,
    )
    if install_script.returncode != 0:
        return False
    if run_logged_command("[deps]", ["sh"], input_text=install_script.stdout):
        print("[deps] uv installed; restart your shell if it is not on PATH.")
        return True
    return False


def run_install_command(agent_name: str, scope: str, version: Optional[str]) -> int:
    return _run_for_targets(
        agent_name,
        lambda target: _install_target(target, scope=scope, version=version),
    )


def run_configure_command(agent_name: str) -> int:
    return _run_for_targets(agent_name, _configure_target)
