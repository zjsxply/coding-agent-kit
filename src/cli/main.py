from __future__ import annotations

import argparse

from ..agents import list_agents
from .env import run_agent_command, write_env_template
from .install import run_configure_command, run_install_command
from .tools import run_skills, run_tools_command


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cakit", description="Coding Agent Kit CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser(
        "install",
        help="Install a coding agent",
        description="Install a coding agent",
    )
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
    install.set_defaults(handler=lambda args: run_install_command(args.agent, args.scope, args.version))

    configure = subparsers.add_parser(
        "configure",
        help="Configure a coding agent",
        description="Configure a coding agent",
    )
    configure.add_argument(
        "agent",
        nargs="?",
        default="all",
        help="Agent name, or `all` / `*` for all agents (`*` should be quoted). Omitted means `all`.",
    )
    configure.set_defaults(handler=lambda args: run_configure_command(args.agent))

    run = subparsers.add_parser(
        "run",
        help="Run a coding agent",
        description="Run a coding agent",
    )
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
    run.set_defaults(
        handler=lambda args: run_agent_command(
            args.agent,
            args.prompt,
            args.cwd,
            args.image,
            args.video,
            args.model,
            args.reasoning_effort,
            args.env_file,
        )
    )

    tools = subparsers.add_parser(
        "tools",
        help="Install fast shell power tools (Linux only)",
        description="Install fast shell power tools (Linux only)",
    )
    tools.set_defaults(handler=lambda args: run_tools_command())

    env_cmd = subparsers.add_parser(
        "env",
        help="Write an env template to a file",
        description="Write an env template to a file",
    )
    env_cmd.add_argument("--output", default=".env", help="Output path for the template file")
    env_cmd.add_argument(
        "--lang",
        choices=("en", "zh"),
        default="en",
        help="Template language (default: en).",
    )
    env_cmd.set_defaults(handler=lambda args: write_env_template(args.output, args.lang))

    skills = subparsers.add_parser(
        "skills",
        help="Manage Skills (delegates to `npx skills`)",
        description="Manage Skills (delegates to `npx skills`)",
    )
    skills.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments passed through to `npx skills` (e.g., `add vercel-labs/agent-skills -g`).",
    )
    skills.set_defaults(handler=lambda args: run_skills(args.args))

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    handler = getattr(args, "handler", None)
    if callable(handler):
        return handler(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
