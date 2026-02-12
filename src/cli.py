from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .agents import create_agent, list_agents
from .utils import load_env_file


REASONING_EFFORT_OPTIONS: Dict[str, tuple[str, ...]] = {
    "codex": ("minimal", "low", "medium", "high", "xhigh"),
    "claude": ("low", "medium", "high", "max"),
    "kimi": ("thinking", "none"),
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cakit", description="Coding Agent Kit CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install a coding agent")
    install.add_argument("agent", choices=list_agents())
    install.add_argument(
        "--scope",
        choices=("user", "global"),
        default="user",
        help="Install scope for npm-based agents (default: user).",
    )
    install.add_argument(
        "--version",
        help=(
            "Install a specific agent version. Format depends on agent packaging "
            "(for example npm version, pip version, release tag, or git ref)."
        ),
    )

    configure = subparsers.add_parser("configure", help="Configure a coding agent")
    configure.add_argument("agent", choices=list_agents())

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
            "agent-specific model values from the current shell environment."
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

    env_cmd = subparsers.add_parser("env", help="Write .env.template to a file")
    env_cmd.add_argument("--output", default=".env", help="Output path for the template file")

    skills = subparsers.add_parser("skills", help="Manage Skills (delegates to `npx skills`)")
    skills.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments passed through to `npx skills` (e.g., `add vercel-labs/agent-skills -g`).",
    )

    return parser


def _run_install(agent_name: str, scope: str, version: Optional[str]) -> int:
    install_result = _install_agent(agent_name, scope=scope, version=version)
    payload = {
        "agent": install_result.agent,
        "ok": install_result.ok,
        "version": install_result.version,
        "config_path": install_result.config_path,
        "details": install_result.details,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    return 0 if install_result.ok else 1


def _run_configure(agent_name: str) -> int:
    agent = create_agent(agent_name)
    config_path = agent.configure()
    ok = True
    payload = {
        "agent": agent_name,
        "ok": ok,
        "config_path": config_path,
    }
    if config_path is None:
        payload["details"] = "no config written"
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
        exit_code = result.exit_code if result.exit_code is not None else 1
        usage_ok = bool(result.models_usage)
        if exit_code == 0 and not usage_ok:
            return 3
        return 0 if exit_code == 0 else 1
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
        return _run_env(args.output)
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
    base_env["PATH"] = os.environ.get("PATH") or os.defpath
    base_env["HOME"] = os.environ.get("HOME") or str(Path.home())
    for key in _load_managed_env_keys():
        value = os.environ.get(key)
        if value:
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


def _ensure_dependencies(agent_name: str) -> bool:
    needs_node = agent_name in {"codex", "claude", "copilot", "gemini", "qwen"}
    needs_uv = agent_name in {"openhands", "swe-agent", "trae-oss", "kimi"}
    ok = True

    if needs_node and not _ensure_node_tools():
        ok = False
    if needs_uv and shutil.which("uv") is None:
        print("[deps] uv not found, attempting auto-install.")
        ok = _install_uv_linux() and ok
    return ok


def _install_agent(agent_name: str, scope: str, version: Optional[str] = None) -> "InstallResult":
    from .models import InstallResult

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
    sudo = "sudo " if use_sudo else ""
    sudo_exec = "sudo -E " if use_sudo else ""
    if shutil.which("curl") is None:
        subprocess.run(f"{sudo}apt-get update", shell=True, check=False)
        subprocess.run(f"{sudo}apt-get install -y curl ca-certificates", shell=True, check=False)
    steps = [
        f"curl -fsSL https://deb.nodesource.com/setup_22.x | {sudo_exec}bash -",
        f"{sudo}apt-get install -y nodejs",
    ]
    for cmd in steps:
        print(f"[deps] {cmd}")
        result = subprocess.run(cmd, shell=True, check=False)
        if result.returncode != 0:
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
    sudo = "sudo " if use_sudo else ""
    if shutil.which("curl") is None and shutil.which("apt-get") is not None:
        subprocess.run(f"{sudo}apt-get update", shell=True, check=False)
        subprocess.run(f"{sudo}apt-get install -y curl", shell=True, check=False)
    cmd = "curl -LsSf https://astral.sh/uv/install.sh | sh"
    print(f"[deps] {cmd}")
    result = subprocess.run(cmd, shell=True, check=False)
    if result.returncode == 0:
        print("[deps] uv installed; restart your shell if it is not on PATH.")
        return True
    return False


def _run_tools() -> int:
    ok, details = _install_fast_tools_linux()
    payload = {"ok": ok, "details": details}
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    return 0 if ok else 1


def _run_env(output: str) -> int:
    template_path = Path(__file__).resolve().parents[1] / ".env.template"
    if not template_path.exists():
        sys.stdout.write(json.dumps({"ok": False, "details": "env template not found"}, ensure_ascii=True) + "\n")
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
        print(f"[tools] unsupported arch {arch}; only linux amd64 is supported. Skipping ast-grep install.")
    use_sudo = os.geteuid() != 0
    if use_sudo and shutil.which("sudo") is None:
        return False, "sudo not found; run as root to install tools"
    sudo = "sudo " if use_sudo else ""
    steps = [
        f"{sudo}apt-get update",
        f"{sudo}apt-get install -y curl ca-certificates gnupg lsb-release unzip",
        f"{sudo}apt-get install -y ripgrep fd-find fzf jq yq bat git git-delta",
    ]
    for cmd in steps:
        print(f"[tools] {cmd}")
        result = subprocess.run(cmd, shell=True, check=False)
        if result.returncode != 0:
            return False, f"command failed: {cmd}"
    if shutil.which("gh") is None:
        print("[tools] installing GitHub CLI (gh)")
        gh_steps = [
            f"{sudo}mkdir -p /etc/apt/keyrings",
            f"curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | {sudo}tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null",
            f"{sudo}chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg",
            'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | '
            f"{sudo}tee /etc/apt/sources.list.d/github-cli.list >/dev/null",
            f"{sudo}apt-get update",
            f"{sudo}apt-get install -y gh",
        ]
        for cmd in gh_steps:
            print(f"[tools] {cmd}")
            result = subprocess.run(cmd, shell=True, check=False)
            if result.returncode != 0:
                return False, f"command failed: {cmd}"
    if arch_supported and shutil.which("sg") is None:
        print("[tools] installing ast-grep (sg)")
        cmd = (
            "curl -fsSL https://github.com/ast-grep/ast-grep/releases/latest/download/"
            "ast-grep-linux-x86_64.tar.gz | "
            f"{sudo}tar -xz -C /usr/local/bin sg"
        )
        print(f"[tools] {cmd}")
        result = subprocess.run(cmd, shell=True, check=False)
        if result.returncode != 0:
            return False, f"command failed: {cmd}"
    if shutil.which("fd") is None and shutil.which("fdfind") is not None:
        cmd = f"{sudo}ln -sf /usr/bin/fdfind /usr/local/bin/fd"
        print(f"[tools] {cmd}")
        subprocess.run(cmd, shell=True, check=False)
    if shutil.which("bat") is None and shutil.which("batcat") is not None:
        cmd = f"{sudo}ln -sf /usr/bin/batcat /usr/local/bin/bat"
        print(f"[tools] {cmd}")
        subprocess.run(cmd, shell=True, check=False)
    return True, "installed"


if __name__ == "__main__":
    raise SystemExit(main())
