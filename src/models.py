from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class InstallResult:
    agent: str
    version: Optional[str]
    ok: bool
    details: Optional[str] = None
    config_path: Optional[str] = None


@dataclass
class RunResult:
    agent: str
    agent_version: Optional[str]
    runtime_seconds: Optional[float]
    models_usage: Dict[str, Dict[str, int]] = field(default_factory=dict)
    tool_calls: Optional[int] = None
    llm_calls: Optional[int] = None
    total_cost: Optional[float] = None
    telemetry_log: Optional[str] = None
    response: Optional[str] = None
    exit_code: Optional[int] = None
    output_path: Optional[str] = None
    raw_output: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        models_usage = dict(self.models_usage or {})
        return {
            "agent": self.agent,
            "agent_version": self.agent_version,
            "runtime_seconds": self.runtime_seconds,
            "models_usage": models_usage,
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
            "total_cost": self.total_cost,
            "telemetry_log": self.telemetry_log,
            "response": self.response,
            "exit_code": self.exit_code,
            "output_path": self.output_path,
            "raw_output": self.raw_output,
        }
