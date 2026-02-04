from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodeAgent
from ..models import InstallResult, RunResult
from ..utils import load_json_payloads


class ClaudeAgent(CodeAgent):
    name = "claude"
    display_name = "Anthropic Claude Code"
    binary = "claude"

    def install(self) -> InstallResult:
        result = self._run(["npm", "install", "-g", "@anthropic-ai/claude-code"])
        config_path = self.configure()
        ok = result.exit_code == 0
        details = result.output
        return InstallResult(
            agent=self.name,
            version=self.get_version(),
            ok=ok,
            details=details,
            config_path=config_path,
        )

    def configure(self) -> Optional[str]:
        model = os.environ.get("CLAUDE_CODE_MODEL") or os.environ.get("ANTHROPIC_MODEL")
        if not model:
            return None
        settings: Dict[str, Any] = {"model": model}
        path = os.path.expanduser("~/.claude/settings.json")
        self._write_text(Path(path), json.dumps(settings, ensure_ascii=True, indent=2))
        return path

    def run(self, prompt: str, images: Optional[list[Path]] = None) -> RunResult:
        images = images or []
        if images:
            message = "image input is only supported in interactive mode for claude; cakit run does not support it."
            output_path = self._write_output(self.name, message)
            return RunResult(
                agent=self.name,
                agent_version=self.get_version(),
                runtime_seconds=0.0,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                models_usage={},
                tool_calls=None,
                llm_calls=None,
                total_cost=None,
                telemetry_log=None,
                exit_code=2,
                output_path=str(output_path),
                raw_output=message,
            )
        model = os.environ.get("CLAUDE_CODE_MODEL") or os.environ.get("ANTHROPIC_MODEL")
        telemetry_enabled = os.environ.get("CLAUDE_CODE_ENABLE_TELEMETRY")
        otel_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otel_endpoint and telemetry_enabled is None:
            telemetry_enabled = "1"
        env = {
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY"),
            "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL"),
            "ANTHROPIC_AUTH_TOKEN": os.environ.get("ANTHROPIC_AUTH_TOKEN"),
            "CLAUDE_CODE_ENABLE_TELEMETRY": telemetry_enabled,
            "OTEL_EXPORTER_OTLP_ENDPOINT": otel_endpoint,
        }
        cmd = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.append(prompt)
        result = self._run(cmd, env)
        output = result.output
        payloads = load_json_payloads(output)
        stats, models_usage = self._extract_stats(payloads)
        usage = self._extract_usage(payloads)
        tool_calls = self._count_tool_calls(payloads)
        runtime_seconds = result.duration_seconds
        if stats.get("duration_ms") is not None:
            runtime_seconds = stats["duration_ms"] / 1000.0
        prompt_tokens, completion_tokens, total_tokens = self._usage_totals(stats, usage)
        output_path = self._write_output(self.name, output)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=runtime_seconds,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=stats.get("num_turns"),
            total_cost=stats.get("total_cost_usd"),
            telemetry_log=otel_endpoint if telemetry_enabled and otel_endpoint else None,
            exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["claude", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _extract_stats(self, payloads: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Dict[str, int]]]:
        stats: Dict[str, Any] = {}
        models_usage: Dict[str, Dict[str, int]] = {}
        for payload in payloads:
            if "duration_ms" in payload:
                value = self._as_int(payload.get("duration_ms"))
                if value is not None:
                    stats["duration_ms"] = value
            if "num_turns" in payload:
                value = self._as_int(payload.get("num_turns"))
                if value is not None:
                    stats["num_turns"] = value
            if "total_cost_usd" in payload:
                try:
                    stats["total_cost_usd"] = float(payload.get("total_cost_usd"))
                except Exception:
                    pass
            model_usage = payload.get("modelUsage")
            if isinstance(model_usage, dict):
                prompt_tokens = 0
                completion_tokens = 0
                total_cost = 0.0
                cost_seen = False
                for model_name, model_stats in model_usage.items():
                    if not isinstance(model_stats, dict):
                        continue
                    input_tokens = self._as_int(model_stats.get("inputTokens")) or 0
                    output_tokens = self._as_int(model_stats.get("outputTokens")) or 0
                    models_usage[str(model_name)] = {
                        "prompt_tokens": input_tokens,
                        "completion_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens,
                    }
                    prompt_tokens += input_tokens
                    completion_tokens += output_tokens
                    cost = model_stats.get("costUSD")
                    if isinstance(cost, (int, float)):
                        total_cost += float(cost)
                        cost_seen = True
                stats["prompt_tokens"] = prompt_tokens
                stats["completion_tokens"] = completion_tokens
                stats["total_tokens"] = prompt_tokens + completion_tokens
                if cost_seen:
                    stats["total_cost_usd"] = total_cost
        return stats, models_usage

    def _extract_usage(self, payloads: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        for payload in payloads:
            usage = self._find_usage(payload)
            if usage:
                return usage
        return None

    def _find_usage(self, payload: Any) -> Optional[Dict[str, int]]:
        if not isinstance(payload, dict):
            return None
        if "usage" in payload and isinstance(payload["usage"], dict):
            return self._normalize_usage(payload["usage"])
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
            if key in payload:
                return self._normalize_usage(payload)
        for value in payload.values():
            if isinstance(value, dict):
                nested = self._find_usage(value)
                if nested:
                    return nested
            if isinstance(value, list):
                for item in value:
                    nested = self._find_usage(item)
                    if nested:
                        return nested
        return None

    def _normalize_usage(self, raw: Dict[str, Any]) -> Dict[str, int]:
        prompt = self._as_int(raw.get("prompt_tokens"))
        completion = self._as_int(raw.get("completion_tokens"))
        total = self._as_int(raw.get("total_tokens"))
        if prompt is None and "input_tokens" in raw:
            prompt = self._as_int(raw.get("input_tokens"))
        if completion is None and "output_tokens" in raw:
            completion = self._as_int(raw.get("output_tokens"))
        if total is None:
            total = (prompt or 0) + (completion or 0)
        return {
            "prompt_tokens": prompt or 0,
            "completion_tokens": completion or 0,
            "total_tokens": total or 0,
        }

    def _count_tool_calls(self, payloads: List[Dict[str, Any]]) -> int:
        count = 0
        for payload in payloads:
            if self._looks_like_tool_call(payload):
                count += 1
        return count

    def _looks_like_tool_call(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        for key in ("tool", "tool_name", "toolName", "tool_call", "toolCall", "tool_use", "toolUse"):
            if key in payload:
                return True
        event_type = payload.get("type") or payload.get("event") or payload.get("name")
        if isinstance(event_type, str) and "tool" in event_type.lower():
            return True
        for value in payload.values():
            if isinstance(value, dict) and self._looks_like_tool_call(value):
                return True
            if isinstance(value, list):
                for item in value:
                    if self._looks_like_tool_call(item):
                        return True
        return False

    def _usage_totals(
        self,
        stats: Dict[str, Any],
        usage: Optional[Dict[str, int]],
    ) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        if stats.get("prompt_tokens") is not None:
            return (
                stats.get("prompt_tokens"),
                stats.get("completion_tokens"),
                stats.get("total_tokens"),
            )
        if usage:
            return (
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("total_tokens"),
            )
        return None, None, None

    @staticmethod
    def _as_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except Exception:
            return None
