from __future__ import annotations

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional, TextIO

from ..agents import create_agent, list_agents
from ..io_helpers import emit_json, file_lock
from ..models import InstallResult


ALL_AGENT_SELECTORS = {"*", "all"}
MAX_PARALLEL_INSTALL_TARGETS = 6
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


def _format_target_exception(exc: BaseException) -> str:
    if isinstance(exc, SystemExit):
        code = exc.code
        if code is None:
            return "SystemExit: exited without status"
        if isinstance(code, str):
            detail = code.strip()
            return f"SystemExit: {detail}" if detail else "SystemExit"
        return f"SystemExit: exit code {code}"
    detail = str(exc).strip()
    if detail:
        return f"{type(exc).__name__}: {detail}"
    return type(exc).__name__


def _run_target_safely(target: str, target_runner: Callable[[str], TargetCommandResult]) -> TargetCommandResult:
    try:
        ok, payload = target_runner(target)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        return False, {
            "agent": target,
            "ok": False,
            "details": _format_target_exception(exc),
            "error_type": type(exc).__name__,
        }

    normalized_payload = dict(payload)
    normalized_payload.setdefault("agent", target)
    normalized_payload["ok"] = bool(ok)
    return bool(ok), normalized_payload


def _run_targets(
    targets: list[str],
    *,
    target_runner: Callable[[str], TargetCommandResult],
    parallel: bool,
) -> list[TargetCommandResult]:
    if not parallel or len(targets) <= 1:
        return [_run_target_safely(target, target_runner) for target in targets]

    def run_target(target: str) -> TargetCommandResult:
        return _run_target_safely(target, target_runner)

    with ThreadPoolExecutor(max_workers=min(len(targets), MAX_PARALLEL_INSTALL_TARGETS)) as executor:
        return list(executor.map(run_target, targets))


def _run_for_targets(
    agent_name: str,
    target_runner: Callable[[str], TargetCommandResult],
    *,
    parallel: bool = False,
) -> int:
    targets = _resolve_targets_or_emit(agent_name)
    if targets is None:
        return 2

    results = _run_targets(targets, target_runner=target_runner, parallel=parallel)
    if len(results) == 1:
        ok, payload = results[0]
        emit_json(payload)
        return 0 if ok else 1

    failed_agents = [target for target, (ok, _) in zip(targets, results) if not ok]
    successful_agents = [target for target, (ok, _) in zip(targets, results) if ok]
    emit_json(
        {
            "agent": agent_name,
            "resolved_agents": targets,
            "ok": all(ok for ok, _ in results),
            "parallel": parallel,
            "successful_agents": successful_agents,
            "failed_agents": failed_agents,
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


def _resolve_configure_post_command() -> Optional[str]:
    raw_value = os.environ.get("CAKIT_CONFIGURE_POST_COMMAND")
    if raw_value is None:
        return None
    command = raw_value.strip()
    return command or None


def _run_configure_post_command(target: str, config_path: str) -> TargetCommandResult:
    command = _resolve_configure_post_command()
    if command is None:
        return True, {}

    config_file = Path(config_path).expanduser()
    command_env = os.environ.copy()
    command_env["CAKIT_CONFIGURE_AGENT"] = target
    command_env["CAKIT_CONFIG_PATH"] = str(config_file)
    command_env["CAKIT_CONFIG_DIR"] = str(config_file.parent)
    command_cwd = config_file.parent if config_file.parent.exists() else Path.cwd()

    try:
        result = subprocess.run(
            ["bash", "-lc", command],
            cwd=str(command_cwd),
            env=command_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        return False, {
            "post_config_command": command,
            "details": f"post-config command failed: {exc}",
            "error_type": type(exc).__name__,
        }

    payload: dict[str, object] = {
        "post_config_command": command,
        "post_config_exit_code": result.returncode,
    }
    output = (result.stdout or "").strip()
    if output:
        payload["post_config_output"] = output
    if result.returncode != 0:
        payload["details"] = f"post-config command failed with exit code {result.returncode}"
        return False, payload
    return True, payload


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
    post_ok, post_payload = _run_configure_post_command(target, config_path)
    payload.update(post_payload)
    return post_ok, payload


def run_logged_command(
    prefix: str,
    cmd: list[str],
    *,
    input_text: Optional[str] = None,
    quiet_success: bool = False,
    output_stream: Optional[TextIO] = None,
) -> bool:
    stream = output_stream or sys.stderr
    if not quiet_success:
        print(f"{prefix} {' '.join(cmd)}", file=stream)
        result = subprocess.run(
            cmd,
            check=False,
            input=input_text,
            text=True,
            stdout=stream,
            stderr=stream,
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
        print(f"{prefix} {' '.join(cmd)}", file=stream)
        if result.stdout:
            stream.write(result.stdout)
            if not result.stdout.endswith("\n"):
                stream.write("\n")
    return result.returncode == 0


def with_sudo(cmd: list[str], *, use_sudo: bool, preserve_env: bool = False) -> list[str]:
    if not use_sudo:
        return cmd
    if preserve_env:
        return ["sudo", "-E", *cmd]
    return ["sudo", *cmd]


def ensure_node_tools(*, quiet_success: bool = False, output_stream: Optional[TextIO] = None) -> bool:
    stream = output_stream or sys.stderr
    if shutil.which("node") is None or shutil.which("npm") is None:
        if not quiet_success:
            print("[deps] nodejs/npm not found, attempting auto-install (Linux + apt-get required).", file=stream)
        return install_node_linux(quiet_success=quiet_success, output_stream=stream)
    return True


def ensure_dependencies(agent_name: str, *, output_stream: Optional[TextIO] = None) -> bool:
    stream = output_stream or sys.stderr
    required_runtimes = create_agent(agent_name).runtime_dependencies()
    ok = True
    for runtime_name in required_runtimes:
        if runtime_name == "node":
            with file_lock("deps-node"):
                if not ensure_node_tools(output_stream=stream):
                    ok = False
            continue
        if runtime_name == "uv":
            with file_lock("deps-uv"):
                if shutil.which("uv") is None:
                    print("[deps] uv not found, attempting auto-install.", file=stream)
                    ok = install_uv_linux(output_stream=stream) and ok
            continue
        print(f"[deps] unsupported runtime dependency for {agent_name}: {runtime_name}", file=stream)
        ok = False
    return ok


def install_agent(
    agent_name: str,
    scope: str,
    version: Optional[str] = None,
    *,
    output_stream: Optional[TextIO] = None,
) -> InstallResult:
    with file_lock(f"install-{agent_name}"):
        return _install_agent_locked(agent_name, scope=scope, version=version, output_stream=output_stream)


def ensure_agent_installed(
    agent_name: str,
    *,
    scope: str,
    version: Optional[str] = None,
    output_stream: Optional[TextIO] = None,
) -> tuple[bool, Optional[InstallResult]]:
    with file_lock(f"install-{agent_name}"):
        if create_agent(agent_name).is_installed():
            return False, None
        return True, _install_agent_locked(
            agent_name,
            scope=scope,
            version=version,
            output_stream=output_stream,
        )


def _install_agent_locked(
    agent_name: str,
    *,
    scope: str,
    version: Optional[str],
    output_stream: Optional[TextIO],
) -> InstallResult:
    if not ensure_dependencies(agent_name, output_stream=output_stream):
        return InstallResult(
            agent=agent_name,
            version=None,
            ok=False,
            details="dependency install failed",
            config_path=None,
        )
    agent = create_agent(agent_name)
    try:
        return agent.install(scope=scope, version=version)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        return InstallResult(
            agent=agent_name,
            version=None,
            ok=False,
            details=_format_target_exception(exc),
            config_path=None,
        )


def install_node_linux(*, quiet_success: bool = False, output_stream: Optional[TextIO] = None) -> bool:
    stream = output_stream or sys.stderr
    if not sys.platform.startswith("linux") or shutil.which("apt-get") is None:
        print("[deps] unsupported OS for auto-install; please install Node.js manually.", file=stream)
        return False
    use_sudo = os.geteuid() != 0
    if use_sudo and shutil.which("sudo") is None:
        print("[deps] sudo not found; run as root to auto-install Node.js.", file=stream)
        return False
    if shutil.which("curl") is None:
        if not run_logged_command(
            "[deps]",
            with_sudo(["apt-get", "update"], use_sudo=use_sudo),
            quiet_success=quiet_success,
            output_stream=stream,
        ):
            return False
        if not run_logged_command(
            "[deps]",
            with_sudo(["apt-get", "install", "-y", "curl", "ca-certificates"], use_sudo=use_sudo),
            quiet_success=quiet_success,
            output_stream=stream,
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
        output_stream=stream,
    ):
        return False
    if not run_logged_command(
        "[deps]",
        with_sudo(["apt-get", "install", "-y", "nodejs"], use_sudo=use_sudo),
        quiet_success=quiet_success,
        output_stream=stream,
    ):
        return False
    return True


def install_uv_linux(*, output_stream: Optional[TextIO] = None) -> bool:
    stream = output_stream or sys.stderr
    if not sys.platform.startswith("linux"):
        print("[deps] unsupported OS for auto-install; please install uv manually.", file=stream)
        return False
    use_sudo = os.geteuid() != 0
    if use_sudo and shutil.which("sudo") is None:
        print("[deps] sudo not found; run as root to auto-install uv prerequisites.", file=stream)
        return False
    if shutil.which("curl") is None and shutil.which("apt-get") is not None:
        if not run_logged_command("[deps]", with_sudo(["apt-get", "update"], use_sudo=use_sudo), output_stream=stream):
            return False
        if not run_logged_command(
            "[deps]",
            with_sudo(["apt-get", "install", "-y", "curl"], use_sudo=use_sudo),
            output_stream=stream,
        ):
            return False

    install_script = subprocess.run(
        ["curl", "-LsSf", "https://astral.sh/uv/install.sh"],
        capture_output=True,
        text=True,
        check=False,
    )
    if install_script.returncode != 0:
        return False
    if run_logged_command("[deps]", ["sh"], input_text=install_script.stdout, output_stream=stream):
        print("[deps] uv installed; restart your shell if it is not on PATH.", file=stream)
        return True
    return False


def run_install_command(agent_name: str, scope: str, version: Optional[str]) -> int:
    return _run_for_targets(
        agent_name,
        lambda target: _install_target(target, scope=scope, version=version),
        parallel=agent_name.strip().lower() in ALL_AGENT_SELECTORS,
    )


def run_configure_command(agent_name: str) -> int:
    return _run_for_targets(agent_name, _configure_target)
