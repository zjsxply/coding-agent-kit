from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text, load_json_payloads


class ClaudeAgent(CodingAgent):
    name = "claude"
    display_name = "Anthropic Claude Code"
    binary = "claude"
    supports_images = True
    supports_videos = False

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        return self._install_with_npm(package="@anthropic-ai/claude-code", scope=scope, version=version)

    def configure(self) -> Optional[str]:
        return None

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        images = images or []
        injected_prompt, resolved_images, _ = self._build_natural_media_prompt(
            prompt,
            images=images,
            videos=None,
            tool_name="Read",
        )
        add_dirs = sorted({str(image.parent) for image in resolved_images})
        model = model_override or os.environ.get("ANTHROPIC_MODEL")
        telemetry_enabled_raw = os.environ.get("CLAUDE_CODE_ENABLE_TELEMETRY")
        otel_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        telemetry_enabled = bool(otel_endpoint)
        telemetry_enabled_env: Optional[str] = None
        if telemetry_enabled_raw is None:
            if telemetry_enabled:
                telemetry_enabled_env = "1"
        else:
            telemetry_enabled_env = telemetry_enabled_raw
            telemetry_enabled = telemetry_enabled_raw.strip().lower() in {"1", "true", "yes", "y", "on"}
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        use_oauth_raw = os.environ.get("CAKIT_CLAUDE_USE_OAUTH")
        use_oauth = bool(use_oauth_raw and use_oauth_raw.strip().lower() in {"1", "true", "yes", "y", "on"})
        unset_env: list[str] = []
        if api_key and auth_token:
            if use_oauth:
                api_key = None
                unset_env.append("ANTHROPIC_API_KEY")
            else:
                auth_token = None
                unset_env.append("ANTHROPIC_AUTH_TOKEN")
        env = {
            "ANTHROPIC_API_KEY": api_key,
            "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL"),
            "ANTHROPIC_AUTH_TOKEN": auth_token,
            "CLAUDE_CODE_ENABLE_TELEMETRY": telemetry_enabled_env,
            "CLAUDE_CODE_EFFORT_LEVEL": reasoning_effort,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "OTEL_EXPORTER_OTLP_ENDPOINT": otel_endpoint,
            "IS_SANDBOX": "1",
        }
        cmd = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        if model:
            cmd.extend(["--model", model])
        for directory in add_dirs:
            cmd.extend(["--add-dir", directory])
        cmd.append("--")
        cmd.append(injected_prompt)
        result = self._run(cmd, env, unset_env=unset_env, base_env=base_env)
        output = result.output
        payloads = load_json_payloads(output)
        try:
            parsed = self._parse_stream_payloads(payloads)
        except Exception as exc:
            message = f"failed to parse Claude Code JSON output: {exc}"
            return self._build_error_run_result(
                message=message,
                cakit_exit_code=2,
                command_exit_code=result.exit_code,
                raw_output=output or message,
                runtime_seconds=result.duration_seconds,
            )
        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=parsed["duration_ms"] / 1000.0,
            models_usage=parsed["models_usage"],
            tool_calls=parsed["tool_calls"],
            llm_calls=parsed["llm_calls"],
            total_cost=parsed["total_cost_usd"],
            telemetry_log=otel_endpoint if telemetry_enabled and otel_endpoint else None,
            response=parsed["response"],
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        return self._version_text(["claude", "--version"])

    def _parse_stream_payloads(self, payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not payloads:
            raise ValueError("no JSON payloads found")

        result_payload: Dict[str, Any] | None = None
        for payload in reversed(payloads):
            if payload.get("type") == "result":
                result_payload = payload
                break
        if result_payload is None:
            raise KeyError('missing payload with type="result"')

        duration_ms = result_payload["duration_ms"]
        if not isinstance(duration_ms, int):
            raise TypeError("duration_ms is not an int")
        llm_calls = result_payload["num_turns"]
        if not isinstance(llm_calls, int):
            raise TypeError("num_turns is not an int")

        total_cost_usd = result_payload["total_cost_usd"]
        if not isinstance(total_cost_usd, (int, float)):
            raise TypeError("total_cost_usd is not a number")

        response = result_payload["result"]
        if not isinstance(response, str):
            raise TypeError("result is not a string")

        model_usage = result_payload["modelUsage"]
        if not isinstance(model_usage, dict) or not model_usage:
            raise TypeError("modelUsage is missing or empty")
        models_usage: Dict[str, Dict[str, int]] = {}
        for model_name, model_stats in model_usage.items():
            if not isinstance(model_name, str):
                raise TypeError("modelUsage key is not a string")
            if not isinstance(model_stats, dict):
                raise TypeError("modelUsage value is not an object")
            prompt_tokens = model_stats.get("inputTokens")
            completion_tokens = model_stats.get("outputTokens")
            cache_read_tokens = model_stats.get("cacheReadInputTokens")
            cache_creation_tokens = model_stats.get("cacheCreationInputTokens")
            for key, value in (
                ("inputTokens", prompt_tokens),
                ("outputTokens", completion_tokens),
                ("cacheReadInputTokens", cache_read_tokens),
                ("cacheCreationInputTokens", cache_creation_tokens),
            ):
                if not isinstance(value, int):
                    raise TypeError(f"modelUsage {key} is not an int")
            prompt_tokens_total = prompt_tokens + cache_read_tokens + cache_creation_tokens
            models_usage[model_name] = {
                "prompt_tokens": prompt_tokens_total,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens_total + completion_tokens,
            }

        tool_calls = 0
        for payload in payloads:
            if payload.get("type") != "assistant":
                continue
            message = payload.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls += 1

        return {
            "duration_ms": duration_ms,
            "llm_calls": llm_calls,
            "total_cost_usd": float(total_cost_usd),
            "models_usage": models_usage,
            "tool_calls": tool_calls,
            "response": response,
        }
