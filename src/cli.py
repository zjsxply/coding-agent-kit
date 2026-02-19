from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

try:
    import fcntl
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from .agents import create_agent, list_agents
from .models import InstallResult
from .utils import load_env_file


ALL_AGENT_SELECTORS = {"*", "all"}


REASONING_EFFORT_OPTIONS: Dict[str, tuple[str, ...]] = {
    "codex": ("minimal", "low", "medium", "high", "xhigh"),
    "claude": ("low", "medium", "high", "max"),
    "factory": ("off", "none", "low", "medium", "high"),
    "openclaw": ("off", "minimal", "low", "medium", "high"),
    "kimi": ("thinking", "none"),
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cakit", description="Coding Agent Kit CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install a coding agent")
    install.add_argument(
        "agent",
        nargs="?",
        default="all",
        help="Agent name, or `all` / `*` for all agents (`*` should be quoted). Omitted means `all`.",
    )
    install.add_argument(
        "--scope",
        choices=("user", "global"),
        default="user",
        help="Install scope for npm-based agents; non-npm installers may ignore this option (default: user).",
    )
    install.add_argument(
        "--version",
        help=(
            "Install a specific agent version. Format depends on agent packaging "
            "(for example npm version, pip version, release tag, or git ref)."
        ),
    )

    configure = subparsers.add_parser("configure", help="Configure a coding agent")
    configure.add_argument(
        "agent",
        nargs="?",
        default="all",
        help="Agent name, or `all` / `*` for all agents (`*` should be quoted). Omitted means `all`.",
    )

    run = subparsers.add_parser("run", help="Run a coding agent")
    run.add_argument("agent", choices=list_agents())
    run.add_argument("prompt", nargs="+")
    run.add_argument("--cwd", default=".", help="Working directory for the agent run (optional)")
    run.add_argument(
        "--image",
        action="append",
        default=[],
        help="Image file path (repeatable or comma-separated)",
    )
    run.add_argument(
        "--video",
        action="append",
        default=[],
        help="Video file path (repeatable or comma-separated)",
    )
    run.add_argument(
        "--model",
        help=(
            "Override the base LLM model for this run. `--model` takes precedence over "
            "agent-specific model values and `OPENAI_DEFAULT_MODEL` from the current shell environment."
        ),
    )
    run.add_argument(
        "--reasoning-effort",
        help=(
            "Unified reasoning/thinking control for the selected coding agent. "
            "See docs/reasoning_effort.md for per-agent options."
        ),
    )
    run.add_argument(
        "--env-file",
        help=(
            "Path to an extra .env-style file. Only variables from this file and cakit-managed "
            "keys will be passed to the coding agent."
        ),
    )

    tools = subparsers.add_parser("tools", help="Install fast shell power tools (Linux only)")

    env_cmd = subparsers.add_parser("env", help="Write an env template to a file")
    env_cmd.add_argument("--output", default=".env", help="Output path for the template file")
    env_cmd.add_argument(
        "--lang",
        choices=("en", "zh"),
        default="en",
        help="Template language (default: en).",
    )

    skills = subparsers.add_parser("skills", help="Manage Skills (delegates to `npx skills`)")
    skills.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments passed through to `npx skills` (e.g., `add vercel-labs/agent-skills -g`).",
    )

    return parser


def _resolve_agent_targets(agent_name: str) -> list[str]:
    key = agent_name.strip().lower()
    available_agents = list_agents()
    if key in ALL_AGENT_SELECTORS:
        return list(available_agents)
    if key in available_agents:
        return [key]
    raise ValueError(f"Unsupported agent: {agent_name}")


def _run_install(agent_name: str, scope: str, version: Optional[str]) -> int:
    try:
        targets = _resolve_agent_targets(agent_name)
    except ValueError as exc:
        payload = {"error": str(exc), "supported_agents": list(list_agents())}
        sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
        return 2

    if len(targets) == 1:
        install_result = _install_agent(targets[0], scope=scope, version=version)
        payload = {
            "agent": install_result.agent,
            "ok": install_result.ok,
            "version": install_result.version,
            "config_path": install_result.config_path,
            "details": install_result.details,
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
        return 0 if install_result.ok else 1

    results = []
    all_ok = True
    for target in targets:
        install_result = _install_agent(target, scope=scope, version=version)
        all_ok = all_ok and install_result.ok
        results.append(
            {
                "agent": install_result.agent,
                "ok": install_result.ok,
                "version": install_result.version,
                "config_path": install_result.config_path,
                "details": install_result.details,
            }
        )

    payload = {
        "agent": agent_name,
        "resolved_agents": targets,
        "ok": all_ok,
        "results": results,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    return 0 if all_ok else 1


def _run_configure(agent_name: str) -> int:
    try:
        targets = _resolve_agent_targets(agent_name)
    except ValueError as exc:
        payload = {"error": str(exc), "supported_agents": list(list_agents())}
        sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
        return 2

    if len(targets) == 1:
        target = targets[0]
        agent = create_agent(target)
        config_path = agent.configure()
        payload = {
            "agent": target,
            "ok": True,
            "config_path": config_path,
        }
        if config_path is None:
            payload["details"] = "no config written"
        sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
        return 0

    results = []
    for target in targets:
        agent = create_agent(target)
        config_path = agent.configure()
        item = {
            "agent": target,
            "ok": True,
            "config_path": config_path,
        }
        if config_path is None:
            item["details"] = "no config written"
        results.append(item)

    payload = {
        "agent": agent_name,
        "resolved_agents": targets,
        "ok": True,
        "results": results,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    return 0


def _expand_media_args(items: list[str]) -> list[Path]:
    expanded: list[Path] = []
    for item in items:
        if not item:
            continue
        if "," in item:
            candidate = Path(item).expanduser().resolve()
            if candidate.exists():
                expanded.append(candidate)
                continue
            parts = [part.strip() for part in item.split(",") if part.strip()]
            for part in parts:
                expanded.append(Path(part).expanduser().resolve())
            continue
        expanded.append(Path(item).expanduser().resolve())
    return expanded


def _normalize_model_override(model: Optional[str]) -> Optional[str]:
    if model is None:
        return None
    normalized = model.strip()
    if not normalized:
        return None
    return normalized


def _normalize_reasoning_effort(agent_name: str, reasoning_effort: Optional[str]) -> Optional[str]:
    if reasoning_effort is None:
        return None
    effort = reasoning_effort.strip().lower()
    effort_slug = effort.replace(" ", "-")
    if not effort:
        return None
    if agent_name == "kimi":
        aliases = {
            "thinking": "thinking",
            "on": "thinking",
            "true": "thinking",
            "yes": "thinking",
            "none": "none",
            "off": "none",
            "false": "none",
            "no": "none",
            "no-thinking": "none",
        }
        normalized = aliases.get(effort) or aliases.get(effort_slug)
        if normalized:
            return normalized
        raise ValueError(
            "unsupported reasoning effort for kimi: "
            f"{reasoning_effort!r}; available: {', '.join(REASONING_EFFORT_OPTIONS['kimi'])}"
        )
    if agent_name == "claude":
        aliases = {
            "low": "low",
            "medium": "medium",
            "high": "high",
            "max": "max",
        }
        normalized = aliases.get(effort) or aliases.get(effort_slug)
        if normalized:
            return normalized
        raise ValueError(
            "unsupported reasoning effort for claude: "
            f"{reasoning_effort!r}; available: {', '.join(REASONING_EFFORT_OPTIONS['claude'])}"
        )
    allowed = REASONING_EFFORT_OPTIONS.get(agent_name)
    if not allowed:
        raise ValueError(f"reasoning effort is not supported for {agent_name}")
    if effort not in allowed:
        raise ValueError(
            f"unsupported reasoning effort for {agent_name}: {reasoning_effort!r}; "
            f"available: {', '.join(allowed)}"
        )
    return effort


def _run_agent(
    agent_name: str,
    prompt_parts: list[str],
    cwd: str,
    images: list[str],
    videos: list[str],
    model: Optional[str],
    reasoning_effort: Optional[str],
    env_file: Optional[str],
) -> int:
    prompt = " ".join(part for part in prompt_parts if part)
    if not prompt:
        sys.stdout.write(json.dumps({"error": "prompt is required"}, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
        return 2
    workdir = Path(cwd).expanduser().resolve()
    base_env = _build_base_env(env_file)
    if base_env is None:
        return 2
    image_paths = _expand_media_args(images)
    missing = [str(path) for path in image_paths if not path.exists()]
    if missing:
        sys.stdout.write(
            json.dumps({"error": "image file not found", "missing": missing}, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        )
        return 2
    video_paths = _expand_media_args(videos)
    missing_videos = [str(path) for path in video_paths if not path.exists()]
    if missing_videos:
        sys.stdout.write(
            json.dumps({"error": "video file not found", "missing": missing_videos}, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        )
        return 2
    try:
        resolved_reasoning_effort = _normalize_reasoning_effort(agent_name, reasoning_effort)
    except ValueError as exc:
        payload = {"error": str(exc)}
        options = REASONING_EFFORT_OPTIONS.get(agent_name)
        if options:
            payload["supported_reasoning_effort"] = list(options)
        sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
        return 2
    resolved_model_override = _normalize_model_override(model)
    try:
        agent = create_agent(agent_name, workdir=workdir)
        if not agent.is_installed():
            print(f"[run] {agent_name} not installed; running cakit install {agent_name}.")
            install_result = _install_agent(agent_name, scope="user")
            if not install_result.ok:
                print(f"[run] install failed: {install_result.details}")
                return 1
            agent = create_agent(agent_name, workdir=workdir)
        result = agent.run(
            prompt,
            images=image_paths,
            videos=video_paths,
            reasoning_effort=resolved_reasoning_effort,
            model_override=resolved_model_override,
            base_env=base_env,
        )
        sys.stdout.write(json.dumps(result.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n")
        if result.cakit_exit_code is None:
            return 1
        return result.cakit_exit_code
    finally:
        pass


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "install":
        return _run_install(args.agent, args.scope, args.version)
    if args.command == "configure":
        return _run_configure(args.agent)
    if args.command == "run":
        return _run_agent(
            args.agent,
            args.prompt,
            args.cwd,
            args.image,
            args.video,
            args.model,
            args.reasoning_effort,
            args.env_file,
        )
    if args.command == "skills":
        return _run_skills(args.args)
    if args.command == "tools":
        return _run_tools()
    if args.command == "env":
        return _run_env(args.output, args.lang)
    parser.print_help()
    return 1


def _ensure_node_tools() -> bool:
    if shutil.which("node") is None or shutil.which("npm") is None:
        print("[deps] nodejs/npm not found, attempting auto-install (Linux + apt-get required).")
        return _install_node_linux()
    return True


def _run_skills(passthrough_args: list[str]) -> int:
    if not _ensure_node_tools():
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


def _build_base_env(env_file: Optional[str]) -> Optional[Dict[str, str]]:
    base_env: Dict[str, str] = {}
    path_value = os.environ.get("PATH")
    home_value = os.environ.get("HOME")
    base_env["PATH"] = path_value if path_value is not None else os.defpath
    base_env["HOME"] = home_value if home_value is not None else str(Path.home())
    for key in _load_managed_env_keys():
        value = os.environ.get(key)
        if value is not None:
            base_env[key] = value
    if env_file:
        env_file_values = _load_extra_env(env_file)
        if env_file_values is None:
            return None
        base_env.update(env_file_values)
    return base_env


def _load_extra_env(env_file: str) -> Optional[Dict[str, str]]:
    path = Path(env_file).expanduser().resolve()
    if not path.exists():
        payload = {"error": "env file not found", "env_file": str(path)}
        sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
        return None
    if not path.is_file():
        payload = {"error": "env file is not a file", "env_file": str(path)}
        sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
        return None
    return load_env_file(path)

def _load_managed_env_keys() -> list[str]:
    template_path = Path(__file__).resolve().parents[1] / ".env.template"
    if not template_path.exists():
        return []
    keys: list[str] = []
    for raw_line in template_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("##"):
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        if not line:
            continue
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key and key not in keys:
            keys.append(key)
    return keys


@contextmanager
def _file_lock(name: str) -> Iterator[None]:
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


def _run_logged_command(prefix: str, cmd: list[str], *, input_text: Optional[str] = None) -> bool:
    print(f"{prefix} {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        check=False,
        input=input_text,
        text=True,
    )
    return result.returncode == 0


def _with_sudo(cmd: list[str], *, use_sudo: bool, preserve_env: bool = False) -> list[str]:
    if not use_sudo:
        return cmd
    if preserve_env:
        return ["sudo", "-E", *cmd]
    return ["sudo", *cmd]


def _ensure_dependencies(agent_name: str) -> bool:
    needs_node = agent_name in {
        "codebuddy",
        "codex",
        "claude",
        "copilot",
        "gemini",
        "qwen",
        "qoder",
        "crush",
        "auggie",
        "continue",
        "kilocode",
        "openclaw",
        "opencode",
    }
    needs_uv = agent_name in {"openhands", "swe-agent", "trae-oss", "kimi", "deepagents"}
    ok = True

    if needs_node:
        with _file_lock("deps-node"):
            if not _ensure_node_tools():
                ok = False
    if needs_uv and shutil.which("uv") is None:
        with _file_lock("deps-uv"):
            if shutil.which("uv") is None:
                print("[deps] uv not found, attempting auto-install.")
                ok = _install_uv_linux() and ok
    return ok


def _install_agent(agent_name: str, scope: str, version: Optional[str] = None) -> "InstallResult":
    with _file_lock(f"install-{agent_name}"):
        if not _ensure_dependencies(agent_name):
            return InstallResult(
                agent=agent_name,
                version=None,
                ok=False,
                details="dependency install failed",
                config_path=None,
            )
        agent = create_agent(agent_name)
        return agent.install(scope=scope, version=version)


def _install_node_linux() -> bool:
    if not sys.platform.startswith("linux") or shutil.which("apt-get") is None:
        print("[deps] unsupported OS for auto-install; please install Node.js manually.")
        return False
    use_sudo = os.geteuid() != 0
    if use_sudo and shutil.which("sudo") is None:
        print("[deps] sudo not found; run as root to auto-install Node.js.")
        return False
    if shutil.which("curl") is None:
        if not _run_logged_command("[deps]", _with_sudo(["apt-get", "update"], use_sudo=use_sudo)):
            return False
        if not _run_logged_command(
            "[deps]",
            _with_sudo(["apt-get", "install", "-y", "curl", "ca-certificates"], use_sudo=use_sudo),
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
    if not _run_logged_command(
        "[deps]",
        _with_sudo(["bash", "-"], use_sudo=use_sudo, preserve_env=True),
        input_text=setup_script.stdout,
    ):
        return False
    if not _run_logged_command(
        "[deps]",
        _with_sudo(["apt-get", "install", "-y", "nodejs"], use_sudo=use_sudo),
    ):
        return False
    return True


def _install_uv_linux() -> bool:
    if not sys.platform.startswith("linux"):
        print("[deps] unsupported OS for auto-install; please install uv manually.")
        return False
    use_sudo = os.geteuid() != 0
    if use_sudo and shutil.which("sudo") is None:
        print("[deps] sudo not found; run as root to auto-install uv prerequisites.")
        return False
    if shutil.which("curl") is None and shutil.which("apt-get") is not None:
        if not _run_logged_command("[deps]", _with_sudo(["apt-get", "update"], use_sudo=use_sudo)):
            return False
        if not _run_logged_command("[deps]", _with_sudo(["apt-get", "install", "-y", "curl"], use_sudo=use_sudo)):
            return False

    install_script = subprocess.run(
        ["curl", "-LsSf", "https://astral.sh/uv/install.sh"],
        capture_output=True,
        text=True,
        check=False,
    )
    if install_script.returncode != 0:
        return False
    if _run_logged_command("[deps]", ["sh"], input_text=install_script.stdout):
        print("[deps] uv installed; restart your shell if it is not on PATH.")
        return True
    return False


def _run_tools() -> int:
    ok, details = _install_fast_tools_linux()
    payload = {"ok": ok, "details": details}
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    return 0 if ok else 1


def _run_env(output: str, lang: str) -> int:
    template_name = ".env.template" if lang == "en" else ".env.template.zh"
    template_path = Path(__file__).resolve().parents[1] / template_name
    if not template_path.exists():
        sys.stdout.write(
            json.dumps(
                {"ok": False, "details": f"env template not found for lang={lang}", "template": template_name},
                ensure_ascii=True,
            )
            + "\n"
        )
        return 1
    template = template_path.read_text(encoding="utf-8")
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template, encoding="utf-8")
    sys.stdout.write(
        json.dumps({"ok": True, "output": str(output_path)}, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    )
    return 0


def _install_fast_tools_linux() -> tuple[bool, str]:
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

    def run_tool_cmd(cmd: list[str]) -> bool:
        return _run_logged_command("[tools]", _with_sudo(cmd, use_sudo=use_sudo))

    if not run_tool_cmd(["apt-get", "update"]):
        return False, "command failed: apt-get update"
    if not run_tool_cmd(["apt-get", "install", "-y", "curl", "ca-certificates", "gnupg", "lsb-release", "unzip"]):
        return False, "command failed: apt-get install base tools"
    if not run_tool_cmd(["apt-get", "install", "-y", "ripgrep", "fd-find", "fzf", "jq", "yq", "bat", "git", "git-delta"]):
        return False, "command failed: apt-get install shell tools"

    if shutil.which("gh") is None:
        print("[tools] installing GitHub CLI (gh)")
        if not run_tool_cmd(["mkdir", "-p", "/etc/apt/keyrings"]):
            return False, "command failed: mkdir -p /etc/apt/keyrings"

        gh_key_tmp = Path(tempfile.mkdtemp(prefix="cakit-gh-key-")) / "githubcli-archive-keyring.gpg"
        if not _run_logged_command(
            "[tools]",
            ["curl", "-fsSL", "https://cli.github.com/packages/githubcli-archive-keyring.gpg", "-o", str(gh_key_tmp)],
        ):
            return False, "command failed: download gh keyring"
        if not _run_logged_command(
            "[tools]",
            _with_sudo(["cp", str(gh_key_tmp), "/etc/apt/keyrings/githubcli-archive-keyring.gpg"], use_sudo=use_sudo),
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
            return False, "command failed: empty dpkg architecture"

        gh_list_tmp = Path(tempfile.mkdtemp(prefix="cakit-gh-list-")) / "github-cli.list"
        gh_list_content = (
            f"deb [arch={dpkg_arch} signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] "
            "https://cli.github.com/packages stable main\n"
        )
        gh_list_tmp.write_text(gh_list_content, encoding="utf-8")
        if not _run_logged_command(
            "[tools]",
            _with_sudo(["cp", str(gh_list_tmp), "/etc/apt/sources.list.d/github-cli.list"], use_sudo=use_sudo),
        ):
            return False, "command failed: install gh apt source"
        if not run_tool_cmd(["apt-get", "update"]):
            return False, "command failed: apt-get update (gh)"
        if not run_tool_cmd(["apt-get", "install", "-y", "gh"]):
            return False, "command failed: apt-get install gh"

    if arch_supported and shutil.which("sg") is None:
        print("[tools] installing ast-grep (sg)")
        sg_tmp = Path(tempfile.mkdtemp(prefix="cakit-ast-grep-")) / "ast-grep-linux-x86_64.tar.gz"
        if not _run_logged_command(
            "[tools]",
            [
                "curl",
                "-fsSL",
                "https://github.com/ast-grep/ast-grep/releases/latest/download/ast-grep-linux-x86_64.tar.gz",
                "-o",
                str(sg_tmp),
            ],
        ):
            return False, "command failed: download ast-grep"
        if not _run_logged_command(
            "[tools]",
            _with_sudo(["tar", "-xzf", str(sg_tmp), "-C", "/usr/local/bin", "sg"], use_sudo=use_sudo),
        ):
            return False, "command failed: install ast-grep"

    if arch_supported:
        if not _ensure_node_tools():
            return False, "command failed: install nodejs/npm for Playwright"

        playwright_cmd: Optional[list[str]] = None
        if shutil.which("npx") is not None:
            playwright_cmd = ["npx", "-y", "playwright@latest"]
        elif shutil.which("npm") is not None:
            playwright_cmd = ["npm", "exec", "--yes", "playwright@latest", "--"]
        if playwright_cmd is None:
            return False, "command failed: npx/npm not found for Playwright"

        print("[tools] installing Playwright Chromium runtime dependencies")
        if not _run_logged_command(
            "[tools]",
            _with_sudo([*playwright_cmd, "install-deps", "chromium"], use_sudo=use_sudo),
        ):
            return False, "command failed: playwright install-deps chromium"
        print("[tools] installing Playwright Chromium browser")
        if not _run_logged_command("[tools]", [*playwright_cmd, "install", "chromium"]):
            return False, "command failed: playwright install chromium"

    if shutil.which("fd") is None and shutil.which("fdfind") is not None:
        _run_logged_command("[tools]", _with_sudo(["ln", "-sf", "/usr/bin/fdfind", "/usr/local/bin/fd"], use_sudo=use_sudo))
    if shutil.which("bat") is None and shutil.which("batcat") is not None:
        _run_logged_command(
            "[tools]",
            _with_sudo(["ln", "-sf", "/usr/bin/batcat", "/usr/local/bin/bat"], use_sudo=use_sudo),
        )
    return True, "installed"


if __name__ == "__main__":
    raise SystemExit(main())
