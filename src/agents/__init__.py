from __future__ import annotations

from collections import Counter
import importlib
from pathlib import Path
import pkgutil
from typing import Dict, Type

from .base import CodingAgent


package_dir = Path(__file__).resolve().parent
module_names = sorted(
    module_info.name
    for module_info in pkgutil.iter_modules([str(package_dir)])
    if not module_info.ispkg and not module_info.name.startswith("_") and module_info.name != "base"
)
[importlib.import_module(f".{module_name}", __name__) for module_name in module_names]

entries = [
    (agent_class.name.strip().lower(), agent_class)
    for agent_class in CodingAgent.__subclasses__()
    if agent_class.__module__.startswith(f"{__name__}.")
    and isinstance(getattr(agent_class, "name", None), str)
    and agent_class.name.strip()
]
duplicates = sorted(name for name, count in Counter(name for name, _ in entries).items() if count > 1)
if duplicates:
    raise RuntimeError(f"Duplicate coding agent registration for: {', '.join(duplicates)}")
AGENT_REGISTRY: Dict[str, Type[CodingAgent]] = dict(sorted(entries, key=lambda item: item[0]))


def list_agents() -> tuple[str, ...]:
    return tuple(AGENT_REGISTRY.keys())


def create_agent(name: str, *, workdir=None) -> CodingAgent:
    key = name.strip().lower()
    if key not in AGENT_REGISTRY:
        raise ValueError(f"Unsupported agent: {name}")
    return AGENT_REGISTRY[key](workdir=workdir)


__all__ = ["list_agents", "create_agent", "AGENT_REGISTRY"]
