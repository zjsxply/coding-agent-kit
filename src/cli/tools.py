from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from ..io_helpers import emit_json
from .install import ensure_node_tools, run_logged_command, with_sudo


def run_skills(passthrough_args: list[str]) -> int:
    if not ensure_node_tools():
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


BASE_SHELL_TOOL_STEPS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("apt-get", "update"), "apt-get update"),
    (("apt-get", "install", "-y", "curl", "ca-certificates", "gnupg", "lsb-release", "unzip"), "apt-get install base tools"),
    (
        ("apt-get", "install", "-y", "ripgrep", "fd-find", "fzf", "jq", "yq", "bat", "git", "git-lfs", "git-delta"),
        "apt-get install shell tools",
    ),
    (("git", "lfs", "install", "--system"), "git lfs install --system"),
)

ARCH_SPECIFIC_TOOL_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("ast-grep", "_install_ast_grep"),
    ("playwright-chromium (deps+browser)", "_install_playwright_chromium"),
)

TOOL_ALIAS_RULES: tuple[tuple[str, str, str, str], ...] = (
    ("fd", "fdfind", "/usr/bin/fdfind", "/usr/local/bin/fd"),
    ("bat", "batcat", "/usr/bin/batcat", "/usr/local/bin/bat"),
)

BASE_SHELL_COMPONENTS: tuple[str, ...] = (
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


def install_fast_tools_linux() -> tuple[bool, str]:
    if not sys.platform.startswith("linux"):
        return False, "unsupported OS; only Linux is supported"
    if shutil.which("apt-get") is None:
        return False, "apt-get not found; please install tools manually"
    arch = platform.machine().lower()
    arch_supported = arch in {"x86_64", "amd64"}
    if not arch_supported:
        print(
            f"[tools] unsupported arch {arch}; only linux amd64 is supported. "
            "Skipping ast-grep and Playwright Chromium install."
        )
    use_sudo = os.geteuid() != 0
    if use_sudo and shutil.which("sudo") is None:
        return False, "sudo not found; run as root to install tools"
    installed_components: list[str] = list(BASE_SHELL_COMPONENTS)

    def run_tool_cmd(cmd: list[str]) -> bool:
        return run_logged_command("[tools]", with_sudo(cmd, use_sudo=use_sudo), quiet_success=True)

    for command, description in BASE_SHELL_TOOL_STEPS:
        if run_tool_cmd(list(command)):
            continue
        return False, f"command failed: {description}"

    if shutil.which("gh") is None:
        print("[tools] installing GitHub CLI (gh)")
        if not run_tool_cmd(["mkdir", "-p", "/etc/apt/keyrings"]):
            return False, "command failed: mkdir -p /etc/apt/keyrings"

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
                return False, "command failed: download gh keyring"
            if not run_logged_command(
                "[tools]",
                with_sudo(
                    ["cp", str(gh_key_tmp), "/etc/apt/keyrings/githubcli-archive-keyring.gpg"],
                    use_sudo=use_sudo,
                ),
                quiet_success=True,
            ):
                return False, "command failed: install gh keyring"
        if not run_tool_cmd(["chmod", "go+r", "/etc/apt/keyrings/githubcli-archive-keyring.gpg"]):
            return False, "command failed: chmod gh keyring"

        arch_result = subprocess.run(
            ["dpkg", "--print-architecture"],
            capture_output=True,
            text=True,
            check=False,
        )
        if arch_result.returncode != 0:
            return False, "command failed: dpkg --print-architecture"
        dpkg_arch = arch_result.stdout.strip()
        if not dpkg_arch:
            return False, "command failed: dpkg --print-architecture"

        with tempfile.TemporaryDirectory(prefix="cakit-gh-list-") as list_tmp_dir:
            gh_list_tmp = Path(list_tmp_dir) / "github-cli.list"
            gh_list_content = (
                f"deb [arch={dpkg_arch} signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] "
                "https://cli.github.com/packages stable main\n"
            )
            gh_list_tmp.write_text(gh_list_content, encoding="utf-8")
            if not run_logged_command(
                "[tools]",
                with_sudo(["cp", str(gh_list_tmp), "/etc/apt/sources.list.d/github-cli.list"], use_sudo=use_sudo),
                quiet_success=True,
            ):
                return False, "command failed: install gh apt source"
        if not run_tool_cmd(["apt-get", "update"]):
            return False, "command failed: apt-get update (gh)"
        if not run_tool_cmd(["apt-get", "install", "-y", "gh"]):
            return False, "command failed: apt-get install gh"
    installed_components.append("gh")

    if arch_supported:
        for component_name, installer_key in ARCH_SPECIFIC_TOOL_COMPONENTS:
            if installer_key == "_install_ast_grep":
                if shutil.which("sg") is None:
                    print("[tools] installing ast-grep (sg)")
                    with tempfile.TemporaryDirectory(prefix="cakit-ast-grep-") as sg_tmp_dir:
                        sg_tmp = Path(sg_tmp_dir) / "ast-grep-linux-x86_64.tar.gz"
                        if not run_logged_command(
                            "[tools]",
                            [
                                "curl",
                                "-fsSL",
                                "https://github.com/ast-grep/ast-grep/releases/latest/download/ast-grep-linux-x86_64.tar.gz",
                                "-o",
                                str(sg_tmp),
                            ],
                            quiet_success=True,
                        ):
                            return False, "command failed: download ast-grep"
                        if not run_logged_command(
                            "[tools]",
                            with_sudo(
                                ["tar", "-xzf", str(sg_tmp), "-C", "/usr/local/bin", "sg"],
                                use_sudo=use_sudo,
                            ),
                            quiet_success=True,
                        ):
                            return False, "command failed: install ast-grep"
                installed_components.append(component_name)
                continue

            if installer_key == "_install_playwright_chromium":
                if not ensure_node_tools(quiet_success=True):
                    return False, "command failed: install nodejs/npm for Playwright"

                playwright_cmd: Optional[list[str]] = None
                if shutil.which("npx") is not None:
                    playwright_cmd = ["npx", "-y", "playwright@latest"]
                elif shutil.which("npm") is not None:
                    playwright_cmd = ["npm", "exec", "--yes", "playwright@latest", "--"]
                if playwright_cmd is None:
                    return False, "command failed: npx/npm not found for Playwright"

                if not run_logged_command(
                    "[tools]",
                    with_sudo([*playwright_cmd, "install-deps", "chromium"], use_sudo=use_sudo),
                    quiet_success=True,
                ):
                    return False, "command failed: playwright install-deps chromium"
                if not run_logged_command("[tools]", [*playwright_cmd, "install", "chromium"], quiet_success=True):
                    return False, "command failed: playwright install chromium"
                installed_components.append(component_name)
                continue

            return False, f"command failed: unknown installer {installer_key}"

    for expected_binary, fallback_binary, source_path, target_path in TOOL_ALIAS_RULES:
        if shutil.which(expected_binary) is not None or shutil.which(fallback_binary) is None:
            continue
        run_logged_command(
            "[tools]",
            with_sudo(["ln", "-sf", source_path, target_path], use_sudo=use_sudo),
            quiet_success=True,
        )

    if not arch_supported:
        return True, f"installed: {', '.join(installed_components)}; skipped: ast-grep, playwright-chromium"
    return True, f"installed: {', '.join(installed_components)}"


def run_tools_command() -> int:
    ok, details = install_fast_tools_linux()
    emit_json({"ok": ok, "details": details})
    return 0 if ok else 1
