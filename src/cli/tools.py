from __future__ import annotations

import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request as urlrequest
import zipfile
from pathlib import Path
from typing import Callable, Optional

from ..io_helpers import emit_json
from .install import (
    apt_get_command,
    detect_package_manager,
    ensure_node_tools,
    package_install_commands,
    run_logged_command,
    with_sudo,
)


def run_skills(passthrough_args: list[str]) -> int:
    if not ensure_node_tools(quiet_success=True):
        return 1

    args = [arg for arg in passthrough_args if arg]
    if not args:
        args = ["-h"]

    if shutil.which("npx") is not None:
        cmd = ["npx", "skills", *args]
    elif shutil.which("npm") is not None:
        print("[skills] npx not found; falling back to `npm exec -- skills ...`.")
        cmd = ["npm", "exec", "--", "skills", *args]
    else:
        print("[skills] npm not found; please install Node.js/npm.")
        return 1

    print(f"[skills] {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    return result.returncode


ARCH_SPECIFIC_TOOL_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("ast-grep", "_install_ast_grep"),
    ("playwright-chromium (deps+browser)", "_install_playwright_chromium"),
)

TOOL_ALIAS_RULES: tuple[tuple[str, str, str, str], ...] = (
    ("fd", "fdfind", "/usr/bin/fdfind", "/usr/local/bin/fd"),
    ("bat", "batcat", "/usr/bin/batcat", "/usr/local/bin/bat"),
)

COMPONENT_BINARIES: dict[str, tuple[str, ...]] = {
    "rg": ("rg",),
    "fd": ("fd", "fdfind"),
    "fzf": ("fzf",),
    "jq": ("jq",),
    "yq": ("yq",),
    "bat": ("bat", "batcat"),
    "git": ("git",),
    "git-lfs": ("git-lfs",),
    "git-delta": ("delta",),
    "gh": ("gh",),
    "ast-grep": ("sg",),
}

AST_GREP_RELEASE_API_URL = "https://api.github.com/repos/ast-grep/ast-grep/releases/latest"
AST_GREP_LINUX_ASSET_NAMES = {
    "x86_64": "app-x86_64-unknown-linux-gnu.zip",
    "amd64": "app-x86_64-unknown-linux-gnu.zip",
}
YQ_LINUX_DOWNLOAD_URLS = {
    "x86_64": "https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64",
    "amd64": "https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64",
}
DELTA_RELEASES_LATEST_URL = "https://github.com/dandavison/delta/releases/latest"
DELTA_LINUX_ASSET_PATTERNS = {
    "x86_64": "delta-{tag}-x86_64-unknown-linux-gnu.tar.gz",
    "amd64": "delta-{tag}-x86_64-unknown-linux-gnu.tar.gz",
}

PACKAGE_MANAGER_BOOTSTRAP_PACKAGES: dict[str, tuple[str, ...]] = {
    "apt-get": ("curl", "ca-certificates", "gnupg", "lsb-release", "unzip", "tar", "gzip"),
    "apk": ("curl", "ca-certificates", "unzip", "tar", "gzip"),
    "dnf": ("curl", "ca-certificates", "unzip", "tar", "gzip"),
    "microdnf": ("curl", "ca-certificates", "unzip", "tar", "gzip"),
    "yum": ("curl", "ca-certificates", "unzip", "tar", "gzip"),
    "zypper": ("curl", "ca-certificates", "unzip", "tar", "gzip"),
    "pacman": ("curl", "ca-certificates", "unzip", "tar", "gzip"),
}

TOOL_PACKAGE_CANDIDATES: dict[str, dict[str, tuple[tuple[str, ...], ...]]] = {
    "rg": {
        "default": (("ripgrep",),),
    },
    "fd": {
        "default": (("fd",),),
        "apt-get": (("fd-find",),),
        "dnf": (("fd-find",), ("fd",)),
        "microdnf": (("fd-find",), ("fd",)),
        "yum": (("fd-find",), ("fd",)),
    },
    "fzf": {
        "default": (("fzf",),),
    },
    "jq": {
        "default": (("jq",),),
    },
    "yq": {
        "default": (("yq",),),
        "apk": (("yq-go",), ("yq",)),
    },
    "bat": {
        "default": (("bat",),),
    },
    "git": {
        "default": (("git",),),
        "dnf": (("git-core",), ("git",)),
        "microdnf": (("git-core",), ("git",)),
        "yum": (("git-core",), ("git",)),
        "zypper": (("git-core",), ("git",)),
    },
    "git-lfs": {
        "default": (("git-lfs",),),
    },
    "git-delta": {
        "default": (("git-delta",),),
        "apk": (("delta",), ("git-delta",)),
        "pacman": (("git-delta",), ("delta",)),
    },
    "gh": {
        "apk": (("github-cli",), ("gh",)),
        "dnf": (("gh",), ("github-cli",)),
        "microdnf": (("gh",), ("github-cli",)),
        "yum": (("gh",), ("github-cli",)),
        "zypper": (("gh",), ("github-cli",)),
        "pacman": (("github-cli",), ("gh",)),
    },
}

PLAYWRIGHT_DEPS_PACKAGE_MANAGERS = {"apt-get"}
PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS = 300


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _format_tools_details(*, installed: list[str], skipped: list[str], failed: list[str]) -> str:
    parts: list[str] = []
    if installed:
        parts.append(f"installed: {', '.join(installed)}")
    if skipped:
        parts.append(f"skipped: {', '.join(skipped)}")
    if failed:
        parts.append(f"failed: {', '.join(failed)}")
    return "; ".join(parts) if parts else "no tool changes were applied"


def _has_component_binary(component: str) -> bool:
    binaries = COMPONENT_BINARIES.get(component, ())
    return any(shutil.which(binary) is not None for binary in binaries)


def _tool_package_candidates(component: str, package_manager: str) -> tuple[tuple[str, ...], ...]:
    package_mapping = TOOL_PACKAGE_CANDIDATES.get(component, {})
    if package_manager in package_mapping:
        return package_mapping[package_manager]
    return package_mapping.get("default", ())


def _install_package_candidates(
    component: str,
    *,
    package_manager: str,
    package_installer: Callable[[list[str]], bool],
) -> bool:
    for package_names in _tool_package_candidates(component, package_manager):
        if package_installer(list(package_names)):
            return True
    return False


def _resolve_ast_grep_download_url(arch: str) -> Optional[str]:
    asset_name = AST_GREP_LINUX_ASSET_NAMES.get(arch)
    if asset_name is None:
        return None
    request = urlrequest.Request(AST_GREP_RELEASE_API_URL, headers={"User-Agent": "cakit"})
    try:
        with urlrequest.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        if asset.get("name") != asset_name:
            continue
        download_url = asset.get("browser_download_url")
        if isinstance(download_url, str) and download_url:
            return download_url
    return None


def _resolve_latest_github_release_tag(releases_latest_url: str) -> Optional[str]:
    request = urlrequest.Request(releases_latest_url, headers={"User-Agent": "cakit"})
    try:
        with urlrequest.urlopen(request, timeout=60) as response:
            final_url = response.geturl()
    except OSError:
        return None
    marker = "/tag/"
    if marker not in final_url:
        return None
    tag = final_url.rsplit(marker, 1)[-1].strip()
    return tag or None


def _download_url_to_file(download_url: str, destination: Path) -> bool:
    request = urlrequest.Request(download_url, headers={"User-Agent": "cakit"})
    try:
        with urlrequest.urlopen(request, timeout=120) as response, destination.open("wb") as output_file:
            shutil.copyfileobj(response, output_file)
    except OSError:
        return False
    destination.chmod(0o755)
    return True


def _download_binary_from_zip(download_url: str, *, binary_name: str, destination: Path) -> bool:
    request = urlrequest.Request(download_url, headers={"User-Agent": "cakit"})
    try:
        with urlrequest.urlopen(request, timeout=120) as response:
            payload = response.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive, destination.open("wb") as output_file:
            member_name = next(
                (
                    name
                    for name in archive.namelist()
                    if not name.endswith("/") and Path(name).name == binary_name
                ),
                None,
            )
            if member_name is None:
                return False
            with archive.open(member_name) as member_file:
                shutil.copyfileobj(member_file, output_file)
    except (OSError, ValueError, TypeError, zipfile.BadZipFile):
        return False
    destination.chmod(0o755)
    return True


def _download_binary_from_tar_gz(download_url: str, *, binary_name: str, destination: Path) -> bool:
    request = urlrequest.Request(download_url, headers={"User-Agent": "cakit"})
    try:
        with urlrequest.urlopen(request, timeout=120) as response:
            payload = response.read()
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive, destination.open("wb") as output_file:
            member = next(
                (
                    candidate
                    for candidate in archive.getmembers()
                    if candidate.isfile() and Path(candidate.name).name == binary_name
                ),
                None,
            )
            if member is None:
                return False
            member_file = archive.extractfile(member)
            if member_file is None:
                return False
            with member_file:
                shutil.copyfileobj(member_file, output_file)
    except (OSError, tarfile.TarError):
        return False
    destination.chmod(0o755)
    return True


def _install_download_fallback(component_name: str, *, arch: str, use_sudo: bool, run_tool_cmd: Callable[[list[str]], bool]) -> bool:
    download_url: Optional[str] = None
    binary_name: Optional[str] = None
    target_name: Optional[str] = None
    use_tar_gz = False

    if component_name == "yq":
        download_url = YQ_LINUX_DOWNLOAD_URLS.get(arch)
        binary_name = "yq"
        target_name = "yq"
    elif component_name == "git-delta":
        tag = _resolve_latest_github_release_tag(DELTA_RELEASES_LATEST_URL)
        asset_pattern = DELTA_LINUX_ASSET_PATTERNS.get(arch)
        if tag is not None and asset_pattern is not None:
            asset_name = asset_pattern.format(tag=tag)
            download_url = f"https://github.com/dandavison/delta/releases/download/{tag}/{asset_name}"
            binary_name = "delta"
            target_name = "delta"
            use_tar_gz = True

    if download_url is None or binary_name is None or target_name is None:
        return False

    with tempfile.TemporaryDirectory(prefix=f"cakit-{component_name}-") as download_dir:
        download_path = Path(download_dir) / binary_name
        download_ok = (
            _download_binary_from_tar_gz(download_url, binary_name=binary_name, destination=download_path)
            if use_tar_gz
            else _download_url_to_file(download_url, download_path)
        )
        if not download_ok:
            return False
        if not run_logged_command(
            "[tools]",
            with_sudo(["cp", str(download_path), f"/usr/local/bin/{target_name}"], use_sudo=use_sudo),
            quiet_success=True,
        ):
            return False
        return run_tool_cmd(["chmod", "0755", f"/usr/local/bin/{target_name}"])


def install_fast_tools_linux() -> dict[str, object]:
    if not sys.platform.startswith("linux"):
        return {
            "ok": False,
            "details": "unsupported OS; only Linux is supported",
            "installed": [],
            "skipped": [],
            "failed": [],
        }
    package_manager = detect_package_manager()
    if package_manager is None:
        return {
            "ok": False,
            "details": "no supported package manager detected; please install tools manually",
            "installed": [],
            "skipped": [],
            "failed": [],
        }
    arch = platform.machine().lower()
    arch_supported = arch in {"x86_64", "amd64"}
    use_sudo = os.geteuid() != 0
    if use_sudo and shutil.which("sudo") is None:
        return {
            "ok": False,
            "details": "sudo not found; run as root to install tools",
            "installed": [],
            "skipped": [],
            "failed": [],
        }
    installed_components: list[str] = []
    skipped_components: list[str] = []
    failed_components: list[str] = []
    refresh_package_index = package_manager == "pacman"

    def run_tool_cmd(cmd: list[str]) -> bool:
        return run_logged_command("[tools]", with_sudo(cmd, use_sudo=use_sudo), quiet_success=True)

    def install_package_group(packages: list[str]) -> bool:
        nonlocal refresh_package_index
        commands = package_install_commands(
            package_manager,
            packages,
            refresh_package_index=refresh_package_index,
        )
        if not commands:
            return False
        refresh_package_index = False
        for command in commands:
            if not run_tool_cmd(command):
                return False
        return True

    if package_manager == "apt-get" and not run_tool_cmd(apt_get_command("update")):
        _append_unique(failed_components, "apt-get update")

    bootstrap_packages = PACKAGE_MANAGER_BOOTSTRAP_PACKAGES.get(package_manager, ())
    if bootstrap_packages:
        install_package_group(list(bootstrap_packages))

    package_manager_components: tuple[str, ...] = (
        "rg",
        "fd",
        "fzf",
        "jq",
        "yq",
        "bat",
        "git",
        "git-lfs",
        "git-delta",
    )
    for component_name in package_manager_components:
        if _has_component_binary(component_name):
            _append_unique(skipped_components, f"{component_name} (already available)")
            continue
        install_ok = _install_package_candidates(
            component_name,
            package_manager=package_manager,
            package_installer=install_package_group,
        )
        if not install_ok and _install_download_fallback(
            component_name,
            arch=arch,
            use_sudo=use_sudo,
            run_tool_cmd=run_tool_cmd,
        ):
            install_ok = True
        if install_ok:
            _append_unique(installed_components, component_name)
        else:
            _append_unique(failed_components, component_name)

    if shutil.which("git-lfs") is not None:
        if run_tool_cmd(["git", "-C", "/", "lfs", "install", "--system", "--skip-repo"]):
            _append_unique(installed_components, "git-lfs")
        else:
            _append_unique(failed_components, "git-lfs")

    if shutil.which("gh") is not None:
        _append_unique(skipped_components, "gh (already available)")
    elif package_manager == "apt-get":
        if not run_tool_cmd(["mkdir", "-p", "/etc/apt/keyrings"]):
            _append_unique(failed_components, "gh")
        else:
            gh_ok = True
            with tempfile.TemporaryDirectory(prefix="cakit-gh-key-") as key_tmp_dir:
                gh_key_tmp = Path(key_tmp_dir) / "githubcli-archive-keyring.gpg"
                if not run_logged_command(
                    "[tools]",
                    [
                        "curl",
                        "-fsSL",
                        "https://cli.github.com/packages/githubcli-archive-keyring.gpg",
                        "-o",
                        str(gh_key_tmp),
                    ],
                    quiet_success=True,
                ):
                    gh_ok = False
                elif not run_logged_command(
                    "[tools]",
                    with_sudo(
                        ["cp", str(gh_key_tmp), "/etc/apt/keyrings/githubcli-archive-keyring.gpg"],
                        use_sudo=use_sudo,
                    ),
                    quiet_success=True,
                ):
                    gh_ok = False
            if gh_ok and not run_tool_cmd(["chmod", "go+r", "/etc/apt/keyrings/githubcli-archive-keyring.gpg"]):
                gh_ok = False

            if gh_ok:
                arch_result = subprocess.run(
                    ["dpkg", "--print-architecture"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                dpkg_arch = arch_result.stdout.strip() if arch_result.returncode == 0 else ""
                if not dpkg_arch:
                    gh_ok = False
            else:
                dpkg_arch = ""

            if gh_ok:
                with tempfile.TemporaryDirectory(prefix="cakit-gh-list-") as list_tmp_dir:
                    gh_list_tmp = Path(list_tmp_dir) / "github-cli.list"
                    gh_list_content = (
                        f"deb [arch={dpkg_arch} signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] "
                        "https://cli.github.com/packages stable main\n"
                    )
                    gh_list_tmp.write_text(gh_list_content, encoding="utf-8")
                    if not run_logged_command(
                        "[tools]",
                        with_sudo(
                            ["cp", str(gh_list_tmp), "/etc/apt/sources.list.d/github-cli.list"],
                            use_sudo=use_sudo,
                        ),
                        quiet_success=True,
                    ):
                        gh_ok = False
            if gh_ok and not run_tool_cmd(apt_get_command("update")):
                gh_ok = False
            if gh_ok and not run_tool_cmd(apt_get_command("install", "-y", "gh")):
                gh_ok = False
            if gh_ok:
                _append_unique(installed_components, "gh")
            else:
                _append_unique(failed_components, "gh")
    else:
        if _install_package_candidates("gh", package_manager=package_manager, package_installer=install_package_group):
            _append_unique(installed_components, "gh")
        else:
            _append_unique(failed_components, "gh")

    if arch_supported:
        for component_name, installer_key in ARCH_SPECIFIC_TOOL_COMPONENTS:
            if installer_key == "_install_ast_grep":
                if shutil.which("sg") is not None:
                    _append_unique(skipped_components, f"{component_name} (already available)")
                    continue
                sg_ok = True
                download_url = _resolve_ast_grep_download_url(arch)
                with tempfile.TemporaryDirectory(prefix="cakit-ast-grep-") as sg_tmp_dir:
                    sg_tmp = Path(sg_tmp_dir) / "sg"
                    if download_url is None:
                        sg_ok = False
                    else:
                        if not _download_binary_from_zip(download_url, binary_name="sg", destination=sg_tmp):
                            sg_ok = False
                        elif not run_logged_command(
                            "[tools]",
                            with_sudo(
                                ["cp", str(sg_tmp), "/usr/local/bin/sg"],
                                use_sudo=use_sudo,
                            ),
                            quiet_success=True,
                        ):
                            sg_ok = False
                        elif not run_tool_cmd(["chmod", "0755", "/usr/local/bin/sg"]):
                            sg_ok = False
                if sg_ok:
                    _append_unique(installed_components, component_name)
                else:
                    _append_unique(failed_components, component_name)
                continue

            if installer_key == "_install_playwright_chromium":
                if package_manager not in PLAYWRIGHT_DEPS_PACKAGE_MANAGERS:
                    _append_unique(
                        skipped_components,
                        f"{component_name} (auto-deps unsupported on {package_manager})",
                    )
                    continue
                if not ensure_node_tools(quiet_success=True):
                    _append_unique(failed_components, component_name)
                    continue

                playwright_cmd: Optional[list[str]] = None
                if shutil.which("npx") is not None:
                    playwright_cmd = ["npx", "-y", "playwright@latest"]
                elif shutil.which("npm") is not None:
                    playwright_cmd = ["npm", "exec", "--yes", "playwright@latest", "--"]
                if playwright_cmd is None:
                    _append_unique(failed_components, component_name)
                    continue

                deps_ok = run_logged_command(
                    "[tools]",
                    with_sudo([*playwright_cmd, "install-deps", "chromium"], use_sudo=use_sudo),
                    quiet_success=True,
                    timeout_seconds=PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS,
                )
                browser_ok = False
                if deps_ok:
                    browser_ok = run_logged_command(
                        "[tools]",
                        [*playwright_cmd, "install", "chromium"],
                        quiet_success=True,
                        timeout_seconds=PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS,
                    )
                if deps_ok and browser_ok:
                    _append_unique(installed_components, component_name)
                else:
                    _append_unique(failed_components, component_name)
                continue

            _append_unique(failed_components, component_name)
    else:
        _append_unique(skipped_components, f"ast-grep (unsupported arch: {arch})")
        _append_unique(skipped_components, f"playwright-chromium (unsupported arch: {arch})")

    for expected_binary, fallback_binary, source_path, target_path in TOOL_ALIAS_RULES:
        if shutil.which(expected_binary) is not None or shutil.which(fallback_binary) is None:
            continue
        if run_logged_command(
            "[tools]",
            with_sudo(["ln", "-sf", source_path, target_path], use_sudo=use_sudo),
            quiet_success=True,
        ):
            _append_unique(installed_components, expected_binary)
        else:
            _append_unique(failed_components, expected_binary)
            if expected_binary in installed_components:
                installed_components.remove(expected_binary)

    for component_name in list(installed_components):
        if component_name == "playwright-chromium (deps+browser)":
            continue
        if _has_component_binary(component_name):
            continue
        installed_components.remove(component_name)
        _append_unique(failed_components, component_name)

    available_components = [
        component_name
        for component_name in COMPONENT_BINARIES
        if _has_component_binary(component_name)
    ]
    ok = bool(available_components or "playwright-chromium (deps+browser)" in installed_components)
    return {
        "ok": ok,
        "details": _format_tools_details(
            installed=installed_components,
            skipped=skipped_components,
            failed=failed_components,
        ),
        "installed": installed_components,
        "skipped": skipped_components,
        "failed": failed_components,
    }


def run_tools_command() -> int:
    result = install_fast_tools_linux()
    emit_json(result)
    return 0 if bool(result.get("ok")) else 1
