from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent, InstallStrategy, RunCommandTemplate
from ..models import RunResult
from .base import (
    last_value,
    opt_float,
    parse_usage_by_model,
    req_int,
    req_str,
    select_values,
)


class ClaudeAgent(CodingAgent):
    name = "claude"
    display_name = "Anthropic Claude Code"
    binary = "claude"
    supports_images = True
    supports_videos = False
    install_strategy = InstallStrategy(kind="npm", package="@anthropic-ai/claude-code")
    run_template = RunCommandTemplate(
        base_args=(
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ),
        prompt_mode="arg",
        prompt_flag=None,
        model_flag="--model",
        media_injection="none",
    )

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
        extra_args: list[str] = []
        for directory in add_dirs:
            extra_args.extend(["--add-dir", directory])
        template = self.run_template
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=injected_prompt,
            model=model,
            extra_args=extra_args,
        )
        result = self._run(cmd, env, unset_env=unset_env, base_env=base_env)
        output = result.output
        payloads = self._load_output_json_payloads(output)
        parsed = self._parse_stream_payloads(payloads)
        runtime_seconds = result.duration_seconds
        if parsed is not None and isinstance(parsed["duration_ms"], int):
            runtime_seconds = parsed["duration_ms"] / 1000.0
        return self.finalize_run(
            command_result=result,
            response=(parsed["response"] if parsed is not None else None),
            models_usage=(parsed["models_usage"] if parsed is not None else {}),
            llm_calls=(parsed["llm_calls"] if parsed is not None else None),
            tool_calls=(parsed["tool_calls"] if parsed is not None else None),
            total_cost=(parsed["total_cost_usd"] if parsed is not None else None),
            telemetry_log=otel_endpoint if telemetry_enabled and otel_endpoint else None,
            runtime_seconds=runtime_seconds,
        )

    def _parse_stream_payloads(self, payloads: list[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not payloads:
            return None
        result_payload = last_value(payloads, '$[?(@.type == "result")]')
        duration_ms = req_int(result_payload, "$.duration_ms") if isinstance(result_payload, dict) else None
        llm_calls = req_int(result_payload, "$.num_turns") if isinstance(result_payload, dict) else None
        total_cost_usd = opt_float(result_payload, "$.total_cost_usd") if isinstance(result_payload, dict) else None
        response = req_str(result_payload, "$.result") if isinstance(result_payload, dict) else None
        model_usage = last_value(result_payload, "$.modelUsage") if isinstance(result_payload, dict) else None
        models_usage = self._parse_model_usage(model_usage)
        tool_calls = self._count_selected(payloads, '$[?(@.type == "assistant")].message.content[?(@.type == "tool_use")]')
        if (
            duration_ms is None
            and llm_calls is None
            and total_cost_usd is None
            and not models_usage
            and tool_calls is None
            and response is None
        ):
            return None
        return {
            "duration_ms": duration_ms,
            "llm_calls": llm_calls,
            "total_cost_usd": total_cost_usd,
            "models_usage": models_usage,
            "tool_calls": tool_calls,
            "response": response,
        }

    def _parse_model_usage(self, model_usage: Any) -> Dict[str, Dict[str, int]]:
        if not isinstance(model_usage, dict):
            return {}
        models_usage: Dict[str, Dict[str, int]] = {}
        for model_name, model_stats in model_usage.items():
            if not isinstance(model_name, str) or not model_name.strip():
                continue
            if not isinstance(model_stats, dict):
                continue
            usage = parse_usage_by_model(
                {
                    "inputTokens": last_value(model_stats, "$.inputTokens"),
                    "outputTokens": last_value(model_stats, "$.outputTokens"),
                    "cacheReadInputTokens": last_value(model_stats, "$.cacheReadInputTokens"),
                    "cacheCreationInputTokens": last_value(model_stats, "$.cacheCreationInputTokens"),
                },
                "claude_model_usage",
            )
            if usage is None:
                continue
            self._merge_model_usage(models_usage, model_name.strip(), usage)
        return models_usage
