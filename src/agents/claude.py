from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from ..agent_runtime import parsing as runtime_parsing
from ..agent_runtime import trajectory as runtime_trajectory
from .base import CodingAgent, InstallStrategy, RunCommandTemplate
from ..models import RunResult
from ..stats_extract import (
    last_value,
    merge_model_usage,
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
    required_runtimes = ("curl",)
    install_strategy = [
        InstallStrategy(
            kind="shell",
            shell_command="curl -fsSL https://claude.ai/install.sh | bash",
            shell_versioned_command="curl -fsSL https://claude.ai/install.sh | bash -s -- {version_quoted}",
        ),
        InstallStrategy(kind="npm", package="@anthropic-ai/claude-code"),
    ]
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
        telemetry_explicit = telemetry_enabled_raw is not None
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
        if telemetry_explicit and not telemetry_enabled:
            unset_env.append("OTEL_EXPORTER_OTLP_ENDPOINT")
        env = {
            "ANTHROPIC_API_KEY": api_key,
            "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL"),
            "ANTHROPIC_AUTH_TOKEN": auth_token,
            "CLAUDE_CODE_ENABLE_TELEMETRY": telemetry_enabled_env,
            "CLAUDE_CODE_EFFORT_LEVEL": reasoning_effort,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "OTEL_EXPORTER_OTLP_ENDPOINT": (otel_endpoint if telemetry_enabled else None),
            "IS_SANDBOX": "1",
        }
        extra_args: list[str] = []
        for directory in add_dirs:
            extra_args.extend(["--add-dir", directory])
        if add_dirs:
            extra_args.append("--")
        template = self.run_template
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=injected_prompt,
            model=model,
            extra_args=extra_args,
        )
        result = self._run(cmd, env, unset_env=unset_env, base_env=base_env)
        output = result.output
        payloads = runtime_parsing.load_output_json_payloads(output)
        parsed = self._parse_stream_payloads(payloads)
        session_id = self._extract_session_id(payloads)
        transcript_paths = self._find_transcript_family_paths(session_id)
        runtime_seconds = result.duration_seconds
        if parsed is not None and isinstance(parsed["duration_ms"], int):
            runtime_seconds = parsed["duration_ms"] / 1000.0
        trajectory_content = self._build_transcript_family_trajectory(output, transcript_paths)
        return self.finalize_run(
            command_result=result,
            response=(parsed["response"] if parsed is not None else None),
            models_usage=(parsed["models_usage"] if parsed is not None else {}),
            llm_calls=(parsed["llm_calls"] if parsed is not None else None),
            tool_calls=(parsed["tool_calls"] if parsed is not None else None),
            total_cost=(parsed["total_cost_usd"] if parsed is not None else None),
            telemetry_log=otel_endpoint if telemetry_enabled and otel_endpoint else None,
            runtime_seconds=runtime_seconds,
            trajectory_content=trajectory_content,
            trajectory_source=str(Path.home() / ".claude" / "projects"),
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
        session_id = (
            req_str(result_payload, "$.session_id")
            if isinstance(result_payload, dict)
            else req_str(last_value(payloads, '$[?(@.type == "system" && @.subtype == "init")]'), "$.session_id")
        )
        family_stats = self._extract_session_family_stats(session_id)
        stream_stats = self._extract_stream_assistant_stats(payloads)
        assistant_contents = select_values(payloads, '$[?(@.type == "assistant")].message.content')
        if assistant_contents is None:
            tool_calls = None
        else:
            tool_call_values = select_values(
                payloads,
                '$[?(@.type == "assistant")].message.content[?(@.type == "tool_use")]',
            )
            tool_calls = len(tool_call_values) if tool_call_values is not None else None
            if tool_calls is None:
                tool_calls = 0
        if family_stats is not None:
            models_usage = family_stats["models_usage"]
            llm_calls = family_stats["llm_calls"]
            tool_calls = family_stats["tool_calls"]
        elif stream_stats is not None:
            if not models_usage:
                models_usage = stream_stats["models_usage"]
            if llm_calls is None:
                llm_calls = stream_stats["llm_calls"]
            if tool_calls is None:
                tool_calls = stream_stats["tool_calls"]
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
            merge_model_usage(models_usage, model_name.strip(), usage)
        return models_usage

    def _extract_session_id(self, payloads: list[Dict[str, Any]]) -> Optional[str]:
        result_payload = last_value(payloads, '$[?(@.type == "result")]')
        return (
            req_str(result_payload, "$.session_id")
            if isinstance(result_payload, dict)
            else req_str(last_value(payloads, '$[?(@.type == "system" && @.subtype == "init")]'), "$.session_id")
        )

    def _build_transcript_family_trajectory(
        self,
        output: str,
        transcript_paths: list[Path],
    ) -> Optional[str]:
        if not transcript_paths:
            return None
        sections: list[tuple[str, str, Optional[str]]] = [("stdout", output, None)]
        for path in transcript_paths:
            raw = self._read_text_lossy(path)
            if not raw:
                continue
            sections.append((f"transcript:{path.name}", raw, str(path)))
        content = runtime_trajectory.build_family_trajectory_content(
            source=str(Path.home() / ".claude" / "projects"),
            sections=sections,
        )
        return content or None

    def _extract_stream_assistant_stats(self, payloads: list[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        assistant_records = [
            record
            for record in payloads
            if runtime_parsing.normalize_text(last_value(record, "$.type")) == "assistant"
            and isinstance(last_value(record, "$.message"), dict)
        ]
        if not assistant_records:
            return None
        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls = 0
        tool_calls = 0
        for record in assistant_records:
            message = last_value(record, "$.message")
            if not isinstance(message, dict):
                continue
            usage = self._parse_assistant_message_usage(last_value(message, "$.usage"))
            model_name = runtime_parsing.normalize_text(last_value(message, "$.model"))
            if usage is not None and model_name is not None:
                merge_model_usage(models_usage, model_name, usage)
            llm_calls += 1
            tool_use_blocks = select_values(message, '$.content[?(@.type == "tool_use")]')
            if tool_use_blocks is not None:
                tool_calls += len(tool_use_blocks)
        return {
            "models_usage": models_usage,
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
        }

    def _extract_session_family_stats(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if session_id is None:
            return None
        transcript_paths = self._find_transcript_family_paths(session_id)
        if not transcript_paths:
            return None
        assistant_records: Dict[tuple[str, str], Dict[str, Any]] = {}
        for path in transcript_paths:
            raw = self._read_text_lossy(path)
            if not raw:
                continue
            records = runtime_parsing.load_output_json_payloads(raw, stdout_only_output=False)
            for record in records:
                if runtime_parsing.normalize_text(last_value(record, "$.type")) != "assistant":
                    continue
                message = last_value(record, "$.message")
                if not isinstance(message, dict):
                    continue
                message_id = runtime_parsing.normalize_text(last_value(message, "$.id"))
                record_session_id = runtime_parsing.normalize_text(last_value(record, "$.session_id"))
                if message_id is None:
                    continue
                key = (record_session_id or path.name, message_id)
                previous = assistant_records.get(key)
                if previous is None or self._assistant_record_rank(record) >= self._assistant_record_rank(previous):
                    assistant_records[key] = record
        if not assistant_records:
            return None

        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls = 0
        tool_calls = 0
        for record in assistant_records.values():
            message = last_value(record, "$.message")
            if not isinstance(message, dict):
                continue
            usage = self._parse_assistant_message_usage(last_value(message, "$.usage"))
            model_name = runtime_parsing.normalize_text(last_value(message, "$.model"))
            if usage is not None and model_name is not None:
                merge_model_usage(models_usage, model_name, usage)
            llm_calls += 1
            tool_use_blocks = select_values(message, '$.content[?(@.type == "tool_use")]')
            if tool_use_blocks is not None:
                tool_calls += len(tool_use_blocks)
        return {
            "models_usage": models_usage,
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
        }

    def _find_main_transcript(self, session_id: str) -> Optional[Path]:
        projects_root = Path.home() / ".claude" / "projects"
        if not projects_root.exists():
            return None
        matches = list(projects_root.rglob(f"{session_id}.jsonl"))
        if len(matches) != 1:
            return None
        return matches[0]

    def _find_transcript_family_paths(self, session_id: Optional[str]) -> list[Path]:
        if session_id is None:
            return []
        transcript_path = self._find_main_transcript(session_id)
        if transcript_path is None:
            return []
        subagents_dir = transcript_path.with_suffix("") / "subagents"
        if not subagents_dir.exists():
            return [transcript_path]
        return [transcript_path, *sorted(subagents_dir.rglob("*.jsonl"))]

    def _assistant_record_rank(self, record: Dict[str, Any]) -> int:
        message = last_value(record, "$.message")
        if not isinstance(message, dict):
            return 0
        content = last_value(message, "$.content")
        content_length = len(content) if isinstance(content, list) else 0
        has_usage = 1 if isinstance(last_value(message, "$.usage"), dict) else 0
        return content_length + has_usage

    def _parse_assistant_message_usage(self, message_usage: Any) -> Optional[Dict[str, int]]:
        if not isinstance(message_usage, dict):
            return None
        return parse_usage_by_model(
            {
                "inputTokens": last_value(message_usage, "$.input_tokens"),
                "outputTokens": last_value(message_usage, "$.output_tokens"),
                "cacheReadInputTokens": last_value(message_usage, "$.cache_read_input_tokens"),
                "cacheCreationInputTokens": last_value(message_usage, "$.cache_creation_input_tokens"),
            },
            "claude_model_usage",
        )
