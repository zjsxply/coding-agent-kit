from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

from ..agents import create_agent
from ..io_helpers import emit_json
from .install import install_agent


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_TEMPLATE_PATH = PROJECT_ROOT / ".env.template"
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REASONING_EFFORT_OPTIONS: dict[str, tuple[str, ...]] = {
    "codex": ("minimal", "low", "medium", "high", "xhigh"),
    "claude": ("low", "medium", "high", "max"),
    "factory": ("off", "none", "low", "medium", "high"),
    "openclaw": ("off", "minimal", "low", "medium", "high"),
    "kimi": ("thinking", "none"),
}


def expand_media_args(items: list[str]) -> list[Path]:
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


def build_base_env(env_file: Optional[str]) -> Optional[dict[str, str]]:
    base_env: dict[str, str] = {}
    path_value = os.environ.get("PATH")
    home_value = os.environ.get("HOME")
    base_env["PATH"] = path_value if path_value is not None else os.defpath
    base_env["HOME"] = home_value if home_value is not None else str(Path.home())
    for key in load_managed_env_keys():
        value = os.environ.get(key)
        if value is not None:
            base_env[key] = value
    if env_file:
        path = Path(env_file).expanduser().resolve()
        if not path.exists():
            emit_json({"error": "env file not found", "env_file": str(path)})
            return None
        if not path.is_file():
            emit_json({"error": "env file is not a file", "env_file": str(path)})
            return None
        base_env.update({key: value for key, value in dotenv_values(path).items() if isinstance(value, str)})
    return base_env


def load_managed_env_keys(template_path: Optional[Path] = None) -> list[str]:
    resolved_template_path = template_path or ENV_TEMPLATE_PATH
    if not resolved_template_path.exists():
        return []
    keys: list[str] = []
    for raw_line in resolved_template_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("##"):
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        if not line or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if ENV_KEY_RE.fullmatch(key) and key not in keys:
            keys.append(key)
    return keys


def normalize_reasoning_effort(agent_name: str, reasoning_effort: Optional[str]) -> Optional[str]:
    if reasoning_effort is None:
        return None
    normalized = reasoning_effort.strip().lower().replace(" ", "-")
    if not normalized:
        return None
    allowed = REASONING_EFFORT_OPTIONS.get(agent_name)
    if not allowed:
        raise ValueError(f"reasoning effort is not supported for {agent_name}")
    if normalized in allowed:
        return normalized
    raise ValueError(
        f"unsupported reasoning effort for {agent_name}: {reasoning_effort!r}; "
        f"available: {', '.join(allowed)}"
    )


def run_agent_command(
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
        emit_json({"error": "prompt is required"})
        return 2
    workdir = Path(cwd).expanduser().resolve()
    base_env = build_base_env(env_file)
    if base_env is None:
        return 2
    image_paths = expand_media_args(images)
    missing_images = [str(path) for path in image_paths if not path.exists()]
    if missing_images:
        emit_json({"error": "image file not found", "missing": missing_images})
        return 2
    video_paths = expand_media_args(videos)
    missing_videos = [str(path) for path in video_paths if not path.exists()]
    if missing_videos:
        emit_json({"error": "video file not found", "missing": missing_videos})
        return 2
    try:
        resolved_reasoning_effort = normalize_reasoning_effort(agent_name, reasoning_effort)
    except ValueError as exc:
        payload = {"error": str(exc)}
        options = REASONING_EFFORT_OPTIONS.get(agent_name)
        if options:
            payload["supported_reasoning_effort"] = list(options)
        emit_json(payload)
        return 2
    resolved_model_override = model.strip() if isinstance(model, str) else None
    if resolved_model_override == "":
        resolved_model_override = None
    agent = create_agent(agent_name, workdir=workdir)
    if not agent.is_installed():
        print(f"[run] {agent_name} not installed; running cakit install {agent_name}.")
        install_result = install_agent(agent_name, scope="user")
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
    emit_json(result.to_dict())
    if result.cakit_exit_code is None:
        return 1
    return result.cakit_exit_code


def write_env_template(output: str, lang: str) -> int:
    template_name = ".env.template" if lang == "en" else ".env.template.zh"
    template_path = PROJECT_ROOT / template_name
    if not template_path.exists():
        emit_json({"ok": False, "details": f"env template not found for lang={lang}", "template": template_name})
        return 1
    template = template_path.read_text(encoding="utf-8")
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template, encoding="utf-8")
    emit_json({"ok": True, "output": str(output_path)})
    return 0
