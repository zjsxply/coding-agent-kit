from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional, TextIO
from urllib import request as urlrequest

from ..agent_runtime import install_version as runtime_install
from ..agents import create_agent, list_agents
from ..io_helpers import emit_json, file_lock
from ..models import InstallResult


ALL_AGENT_SELECTORS = {"*", "all"}
MAX_PARALLEL_INSTALL_TARGETS = 6
SUPPORTED_PACKAGE_MANAGERS = ("apt-get", "apk", "dnf", "microdnf", "yum", "zypper", "pacman")
MINIMUM_NODE_VERSION = (22, 16, 0)
NODEJS_LTS_LINE = "22"
NODEJS_DIST_BASE_URL = "https://nodejs.org/dist"
APK_EDGE_REPOSITORIES = (
    "https://dl-cdn.alpinelinux.org/alpine/edge/main",
    "https://dl-cdn.alpinelinux.org/alpine/edge/community",
)
SYSTEM_RUNTIME_BINARIES = {
    "bash": "bash",
    "bzip2": "bzip2",
    "curl": "curl",
    "git": "git",
    "gzip": "gzip",
    "tar": "tar",
}
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

    def print_captured_output(output: str) -> None:
        print(f"{prefix} {' '.join(cmd)}", file=stream)
        if output:
            stream.write(output)
            if not output.endswith("\n"):
                stream.write("\n")

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
        print_captured_output(result.stdout or "")
    return result.returncode == 0


def with_sudo(cmd: list[str], *, use_sudo: bool, preserve_env: bool = False) -> list[str]:
    if not use_sudo:
        return cmd
    if preserve_env:
        return ["sudo", "-E", *cmd]
    return ["sudo", *cmd]


def apt_get_command(*args: str) -> list[str]:
    return ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-qq", *args]


def _preferred_bin_dir() -> Path:
    xdg_bin_home = os.environ.get("XDG_BIN_HOME")
    if xdg_bin_home:
        return Path(xdg_bin_home).expanduser()
    return Path.home() / ".local" / "bin"


def _default_install_home() -> Path:
    install_home = os.environ.get("CAKIT_INSTALL_HOME")
    if install_home:
        return Path(install_home).expanduser()
    if os.geteuid() == 0:
        return Path("/opt/cakit")
    return Path.home() / ".local" / "share" / "cakit"


def _node_install_root() -> Path:
    return _default_install_home() / "node"


def _package_manager_lock_name(package_manager: str) -> str:
    return f"deps-package-manager-{package_manager}"


def _candidate_runtime_binary(name: str) -> Optional[str]:
    path = shutil.which(name)
    if path:
        return path
    candidate = _preferred_bin_dir() / name
    if candidate.exists():
        return str(candidate)
    return None


def _parse_version_tuple(raw_value: str) -> Optional[tuple[int, ...]]:
    text = raw_value.strip()
    if not text:
        return None
    first_token = text.split()[0]
    normalized = first_token[1:] if first_token.startswith("v") else first_token
    parts = normalized.split(".")
    if not parts:
        return None
    version: list[int] = []
    for part in parts:
        digits = []
        for char in part:
            if char.isdigit():
                digits.append(char)
                continue
            break
        if not digits:
            return None
        version.append(int("".join(digits)))
    return tuple(version)


def _format_version_tuple(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def _installed_node_version() -> Optional[tuple[int, ...]]:
    node_binary = _candidate_runtime_binary("node")
    if node_binary is None:
        return None
    result = subprocess.run(
        [node_binary, "--version"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        return None
    return _parse_version_tuple(result.stdout or "")


def _node_tools_ready() -> bool:
    node_binary = _candidate_runtime_binary("node")
    npm_binary = _candidate_runtime_binary("npm")
    version = _installed_node_version()
    return node_binary is not None and npm_binary is not None and version is not None and version >= MINIMUM_NODE_VERSION


def _prepend_path(path: Path) -> None:
    current_path = os.environ.get("PATH", "")
    target = str(path)
    parts = [item for item in current_path.split(os.pathsep) if item] if current_path else []
    if target in parts:
        return
    os.environ["PATH"] = os.pathsep.join([target, *parts]) if parts else target


def _link_node_binaries(node_root: Path) -> bool:
    bin_dir = _preferred_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    linked_any = False
    for binary_name in ("node", "npm", "npx", "corepack"):
        source = node_root / "bin" / binary_name
        if not source.is_file():
            continue
        target = bin_dir / binary_name
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.symlink_to(source)
        linked_any = True
    _prepend_path(bin_dir)
    return linked_any


def _linux_node_arch() -> Optional[str]:
    normalized = platform.machine().strip().lower()
    return {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(normalized)


def _download_text(url: str) -> str:
    req = urlrequest.Request(url, headers={"User-Agent": "cakit"})
    with urlrequest.urlopen(req, timeout=60) as response:
        return response.read().decode("utf-8")


def _resolve_latest_node_version() -> Optional[str]:
    shasums_url = f"{NODEJS_DIST_BASE_URL}/latest-v{NODEJS_LTS_LINE}.x/SHASUMS256.txt"
    try:
        shasums_text = _download_text(shasums_url)
    except OSError:
        return None
    for line in shasums_text.splitlines():
        filename = line.strip().split()[-1] if line.strip() else ""
        if not filename.startswith("node-") or "-linux-" not in filename or not filename.endswith(".tar.xz"):
            continue
        return filename.split("-linux-", 1)[0].removeprefix("node-")
    return None


def _extract_tar_safely(archive: tarfile.TarFile, destination: Path) -> None:
    destination_str = str(destination.resolve())
    for member in archive.getmembers():
        member_path = (destination / member.name).resolve()
        if not str(member_path).startswith(destination_str + os.sep) and str(member_path) != destination_str:
            raise RuntimeError("refusing to extract archive outside destination")
    archive.extractall(destination)


def _install_node_from_archive(*, quiet_success: bool = False, output_stream: Optional[TextIO] = None) -> bool:
    stream = output_stream or sys.stderr
    arch = _linux_node_arch()
    if arch is None:
        print("[deps] unsupported CPU architecture for Node.js auto-install; please install Node.js manually.", file=stream)
        return False
    version = _resolve_latest_node_version()
    if version is None:
        print("[deps] failed to resolve the latest Node.js 22.x release.", file=stream)
        return False
    archive_url = f"{NODEJS_DIST_BASE_URL}/{version}/node-{version}-linux-{arch}.tar.xz"
    install_root = _node_install_root()
    final_root = install_root / f"node-{version}-linux-{arch}"
    if (final_root / "bin" / "node").is_file():
        _link_node_binaries(final_root)
        return _node_tools_ready()

    staging_dir = Path(tempfile.mkdtemp(prefix="cakit-node-"))
    archive_path = staging_dir / "node.tar.xz"
    extract_root = staging_dir / "extract"
    extract_root.mkdir(parents=True, exist_ok=True)
    try:
        if not quiet_success:
            print(f"[deps] downloading Node.js {version} from {archive_url}", file=stream)
        req = urlrequest.Request(archive_url, headers={"User-Agent": "cakit"})
        with urlrequest.urlopen(req, timeout=120) as response, archive_path.open("wb") as archive_file:
            shutil.copyfileobj(response, archive_file)
        with tarfile.open(archive_path, mode="r:xz") as archive:
            _extract_tar_safely(archive, extract_root)
        extracted_roots = [path for path in extract_root.iterdir() if path.is_dir()]
        if len(extracted_roots) != 1:
            print("[deps] downloaded Node.js archive had an unexpected layout.", file=stream)
            return False
        install_root.mkdir(parents=True, exist_ok=True)
        if final_root.exists():
            shutil.rmtree(final_root)
        shutil.move(str(extracted_roots[0]), str(final_root))
        if not _link_node_binaries(final_root):
            print("[deps] downloaded Node.js archive did not contain expected binaries.", file=stream)
            return False
        return _node_tools_ready()
    except (OSError, tarfile.TarError, RuntimeError) as exc:
        print(f"[deps] failed to install Node.js from archive: {exc}", file=stream)
        return False
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def detect_package_manager() -> Optional[str]:
    for candidate in SUPPORTED_PACKAGE_MANAGERS:
        if shutil.which(candidate) is not None:
            return candidate
    return None


def package_install_commands(
    package_manager: str,
    packages: list[str],
    *,
    refresh_package_index: bool,
) -> list[list[str]]:
    if package_manager == "apt-get":
        commands: list[list[str]] = []
        if refresh_package_index:
            commands.append(apt_get_command("update"))
        commands.append(apt_get_command("install", "-y", *packages))
        return commands
    if package_manager == "apk":
        return [["apk", "add", "--no-cache", *packages]]
    if package_manager == "dnf":
        return [["dnf", "install", "-y", "--setopt=install_weak_deps=False", "--setopt=tsflags=nodocs", *packages]]
    if package_manager == "microdnf":
        return [["microdnf", "install", "-y", "--setopt=install_weak_deps=0", "--setopt=tsflags=nodocs", *packages]]
    if package_manager == "yum":
        return [["yum", "install", "-y", "--setopt=tsflags=nodocs", *packages]]
    if package_manager == "zypper":
        return [["zypper", "--non-interactive", "install", "--auto-agree-with-licenses", "--no-recommends", *packages]]
    if package_manager == "pacman":
        command = ["pacman", "-Sy" if refresh_package_index else "-S", "--noconfirm", "--needed", *packages]
        return [command]
    return []


def system_runtime_package_name(runtime_name: str, package_manager: str) -> str:
    if runtime_name == "git" and package_manager in {"dnf", "microdnf", "yum", "zypper"}:
        return "git-core"
    return runtime_name


def install_system_packages_linux(
    packages: list[str],
    *,
    quiet_success: bool = False,
    output_stream: Optional[TextIO] = None,
    refresh_package_index: bool = True,
) -> bool:
    stream = output_stream or sys.stderr
    if not sys.platform.startswith("linux"):
        print("[deps] unsupported OS for auto-install; please install dependencies manually.", file=stream)
        return False
    package_manager = detect_package_manager()
    if package_manager is None:
        print("[deps] no supported package manager detected; please install dependencies manually.", file=stream)
        return False
    use_sudo = os.geteuid() != 0
    if use_sudo and shutil.which("sudo") is None:
        print("[deps] sudo not found; run as root to auto-install dependencies.", file=stream)
        return False
    commands = package_install_commands(
        package_manager,
        packages,
        refresh_package_index=refresh_package_index,
    )
    if not commands:
        print("[deps] unsupported package manager for auto-install; please install dependencies manually.", file=stream)
        return False
    with file_lock(_package_manager_lock_name(package_manager)):
        for command in commands:
            if not run_logged_command(
                "[deps]",
                with_sudo(command, use_sudo=use_sudo),
                quiet_success=quiet_success,
                output_stream=stream,
            ):
                return False
    return True


def ensure_node_tools(*, quiet_success: bool = False, output_stream: Optional[TextIO] = None) -> bool:
    stream = output_stream or sys.stderr
    if not _node_tools_ready():
        if not quiet_success:
            version = _installed_node_version()
            if _candidate_runtime_binary("node") is None or _candidate_runtime_binary("npm") is None:
                reason = "nodejs/npm not found"
            elif version is None:
                reason = "nodejs version could not be determined"
            else:
                reason = (
                    f"nodejs {_format_version_tuple(version)} is too old; "
                    f"need >= {_format_version_tuple(MINIMUM_NODE_VERSION)}"
                )
            print(f"[deps] {reason}, attempting auto-install.", file=stream)
        return install_node_linux(quiet_success=quiet_success, output_stream=stream)
    return True


def ensure_dependencies(agent_name: str, *, output_stream: Optional[TextIO] = None) -> bool:
    stream = output_stream or sys.stderr
    required_runtimes = create_agent(agent_name).runtime_dependencies()
    ok = True
    for runtime_name in required_runtimes:
        if runtime_name == "node":
            with file_lock("deps-node"):
                if not ensure_node_tools(quiet_success=True, output_stream=stream):
                    ok = False
            continue
        if runtime_name == "uv":
            with file_lock("deps-uv"):
                if runtime_install.resolve_uv_binary() is None:
                    ok = install_uv_linux(quiet_success=True, output_stream=stream) and ok
            continue
        if runtime_name in SYSTEM_RUNTIME_BINARIES:
            binary_name = SYSTEM_RUNTIME_BINARIES[runtime_name]
            with file_lock(f"deps-system-{runtime_name}"):
                if shutil.which(binary_name) is None:
                    package_manager = detect_package_manager()
                    if package_manager is None:
                        ok = False
                        continue
                    ok = install_system_packages_linux(
                        [system_runtime_package_name(runtime_name, package_manager)],
                        quiet_success=True,
                        output_stream=stream,
                        refresh_package_index=True,
                    ) and ok
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
    if not sys.platform.startswith("linux"):
        print("[deps] unsupported OS for auto-install; please install Node.js manually.", file=stream)
        return False
    if _node_tools_ready():
        return True
    package_manager = detect_package_manager()
    if package_manager == "apk":
        use_sudo = os.geteuid() != 0
        if use_sudo and shutil.which("sudo") is None:
            print("[deps] sudo not found; run as root to auto-install Node.js on Alpine Linux.", file=stream)
            return False
        apk_command = [
            "apk",
            "add",
            "--no-cache",
            "--update-cache",
            "--upgrade",
            "--repository",
            APK_EDGE_REPOSITORIES[0],
            "--repository",
            APK_EDGE_REPOSITORIES[1],
            "nghttp2-libs",
            "nodejs",
            "npm",
        ]
        with file_lock(_package_manager_lock_name(package_manager)):
            if not run_logged_command(
                "[deps]",
                with_sudo(apk_command, use_sudo=use_sudo),
                quiet_success=quiet_success,
                output_stream=stream,
            ):
                return False
        return _node_tools_ready()
    return _install_node_from_archive(quiet_success=quiet_success, output_stream=stream)


def install_uv_linux(*, quiet_success: bool = False, output_stream: Optional[TextIO] = None) -> bool:
    stream = output_stream or sys.stderr
    if not sys.platform.startswith("linux"):
        print("[deps] unsupported OS for auto-install; please install uv manually.", file=stream)
        return False
    if shutil.which("curl") is None or shutil.which("tar") is None or shutil.which("gzip") is None:
        if not install_system_packages_linux(
            ["ca-certificates", "curl", "tar", "gzip"],
            quiet_success=quiet_success,
            output_stream=stream,
            refresh_package_index=True,
        ):
            return False

    install_cmd = ["curl", "-LsSf", "https://astral.sh/uv/install.sh"]
    install_script = subprocess.run(
        install_cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if install_script.returncode != 0:
        print(f"[deps] {' '.join(install_cmd)}", file=stream)
        error_output = install_script.stderr or install_script.stdout
        if error_output:
            stream.write(error_output)
            if not error_output.endswith("\n"):
                stream.write("\n")
        return False
    if run_logged_command(
        "[deps]",
        ["sh"],
        input_text=install_script.stdout,
        quiet_success=quiet_success,
        output_stream=stream,
    ):
        _prepend_path(_preferred_bin_dir())
        if not quiet_success:
            print("[deps] uv installed; restart your shell if it is not on PATH.", file=stream)
        return _candidate_runtime_binary("uv") is not None
    return False


def run_install_command(agent_name: str, scope: str, version: Optional[str]) -> int:
    return _run_for_targets(
        agent_name,
        lambda target: _install_target(target, scope=scope, version=version),
        parallel=agent_name.strip().lower() in ALL_AGENT_SELECTORS,
    )


def run_configure_command(agent_name: str) -> int:
    return _run_for_targets(agent_name, _configure_target)
