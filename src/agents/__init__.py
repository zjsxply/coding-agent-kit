from __future__ import annotations

import importlib
from typing import Dict, Type

from .aider import AiderAgent
from .auggie import AuggieAgent
from .base import CodingAgent
from .claude import ClaudeAgent
from .copilot import CopilotAgent
from .codebuddy import CodeBuddyAgent
from .codex import CodexAgent
from .crush import CrushAgent
from .cursor import CursorAgent
from .deepagents import DeepAgentsAgent
from .factory import FactoryAgent
from .gemini import GeminiAgent
from .goose import GooseAgent
from .kilocode import KiloCodeAgent
from .kimi import KimiAgent
from .openclaw import OpenClawAgent
from .openhands import OpenHandsAgent
from .opencode import OpenCodeAgent
from .qoder import QoderAgent
from .qwen import QwenAgent
from .swe_agent import SweAgent
from .trae_cn import TraeCnAgent
from .trae_oss import TraeOssAgent

ContinueAgent = importlib.import_module(".continue", __name__).ContinueAgent

AGENT_REGISTRY: Dict[str, Type[CodingAgent]] = {
    "aider": AiderAgent,
    "codex": CodexAgent,
    "claude": ClaudeAgent,
    "copilot": CopilotAgent,
    "codebuddy": CodeBuddyAgent,
    "gemini": GeminiAgent,
    "crush": CrushAgent,
    "auggie": AuggieAgent,
    "continue": ContinueAgent,
    "goose": GooseAgent,
    "kilocode": KiloCodeAgent,
    "openclaw": OpenClawAgent,
    "opencode": OpenCodeAgent,
    "factory": FactoryAgent,
    "deepagents": DeepAgentsAgent,
    "kimi": KimiAgent,
    "trae-cn": TraeCnAgent,
    "qoder": QoderAgent,
    "qwen": QwenAgent,
    "openhands": OpenHandsAgent,
    "swe-agent": SweAgent,
    "trae-oss": TraeOssAgent,
    "cursor": CursorAgent,
}


def list_agents() -> tuple[str, ...]:
    return tuple(AGENT_REGISTRY.keys())


def create_agent(name: str, *, workdir=None) -> CodingAgent:
    key = name.strip().lower()
    if key not in AGENT_REGISTRY:
        raise ValueError(f"Unsupported agent: {name}")
    return AGENT_REGISTRY[key](workdir=workdir)


__all__ = ["list_agents", "create_agent", "AGENT_REGISTRY"]
