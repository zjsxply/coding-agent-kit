from __future__ import annotations

from typing import Dict, Type

from .base import CodeAgent
from .claude import ClaudeAgent
from .copilot import CopilotAgent
from .codex import CodexAgent
from .cursor import CursorAgent
from .gemini import GeminiAgent
from .kimi import KimiAgent
from .openhands import OpenHandsAgent
from .qwen import QwenAgent
from .swe_agent import SweAgent
from .trae_oss import TraeOssAgent

AGENT_REGISTRY: Dict[str, Type[CodeAgent]] = {
    "codex": CodexAgent,
    "claude": ClaudeAgent,
    "copilot": CopilotAgent,
    "gemini": GeminiAgent,
    "kimi": KimiAgent,
    "qwen": QwenAgent,
    "openhands": OpenHandsAgent,
    "swe-agent": SweAgent,
    "trae-oss": TraeOssAgent,
    "cursor": CursorAgent,
}


def list_agents() -> tuple[str, ...]:
    return tuple(AGENT_REGISTRY.keys())


def create_agent(name: str, *, workdir=None) -> CodeAgent:
    key = name.strip().lower()
    if key not in AGENT_REGISTRY:
        raise ValueError(f"Unsupported agent: {name}")
    return AGENT_REGISTRY[key](workdir=workdir)


__all__ = ["list_agents", "create_agent", "AGENT_REGISTRY"]
