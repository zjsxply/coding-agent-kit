from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from .base import (
    CodingAgent,
    InstallStrategy,
    RunCommandTemplate,
)
from ..models import RunResult
from ..stats_extract import StatsSnapshot, last_value, parse_usage_by_model, req_str, select_values


class QoderAgent(CodingAgent):
    name = "qoder"
    display_name = "Qoder"
    binary = "qodercli"
    supports_images = True
    supports_videos = False
    install_strategy = InstallStrategy(kind="npm", package="@qoder-ai/qodercli")
    run_template = RunCommandTemplate(
        base_args=("-q", "--output-format", "stream-json", "--dangerously-skip-permissions"),
        prompt_mode="flag",
        prompt_flag="-p",
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
        model = self._normalize_text(model_override or os.environ.get("CAKIT_QODER_MODEL"))
        env = {
            "QODER_PERSONAL_ACCESS_TOKEN": os.environ.get("QODER_PERSONAL_ACCESS_TOKEN"),
        }
        template = self.run_template
        extra_args = [arg for image in images for arg in ("--attachment", str(image))]
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=prompt,
            model=model,
            extra_args=extra_args,
        )

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        payloads = self._load_output_json_payloads(output)
        parsed_stats = self._extract_stats(payloads)
        parsed_snapshot, parsed_response = parsed_stats if parsed_stats is not None else (None, None)
        stats = self._merge_stats_snapshots(
            snapshots=[parsed_snapshot]
        )
        telemetry_path = Path.home() / ".qoder" / "logs" / "qodercli.log"
        return self.finalize_run(
            command_result=result,
            response=parsed_response,
            models_usage=stats.models_usage,
            llm_calls=stats.llm_calls,
            tool_calls=stats.tool_calls,
            total_cost=stats.total_cost,
            telemetry_log=str(telemetry_path) if telemetry_path.exists() else None,
        )

    def _extract_stats(self, payloads: list[Dict[str, Any]]) -> Optional[tuple[StatsSnapshot, Optional[str]]]:
        if not payloads:
            return None
        event_types = {
            event_type.strip()
            for event_type in (select_values(payloads, "$[*].type") or [])
            if isinstance(event_type, str) and event_type.strip()
        }
        if not event_types:
            return None
        if "qoder_message" in event_types:
            return self._extract_qoder_message_stats(payloads)
        if "message_start" in event_types and "message_stop" in event_types:
            return self._extract_stream_message_stats(payloads)
        return None

    def _extract_qoder_message_stats(
        self, payloads: list[Dict[str, Any]]
    ) -> Optional[tuple[StatsSnapshot, Optional[str]]]:
        records = [
            {
                "model_name": req_str(message, "$.response_meta.model_name"),
                "usage": (
                    parse_usage_by_model(raw_usage, "qoder_total")
                    if isinstance(raw_usage := last_value(message, "$.usage"), dict)
                    else None
                ),
                "tool_calls": (
                    len(tool_call_values)
                    if (tool_call_values := select_values(message, "$.tool_calls[*]")) is not None
                    else 0
                ),
                "response": req_str(message, "$.content"),
            }
            for message in (select_values(payloads, '$[?(@.type == "qoder_message")].message') or [])
            if isinstance(message, dict) and req_str(message, "$.role") == "assistant"
        ]
        if not records:
            return None

        usage_records = [
            record
            for record in records
            if record["model_name"] is not None and isinstance(record["usage"], dict)
        ]
        models_usage: Dict[str, Dict[str, int]] = {}
        for record in usage_records:
            self._merge_model_usage(models_usage, record["model_name"], record["usage"])
        tool_calls = sum(record["tool_calls"] for record in records)
        responses = [record["response"] for record in records if record["response"] is not None]
        snapshot = self._normalize_stats_snapshot(
            models_usage=models_usage,
            llm_calls=(len(records) if records else None),
            tool_calls=tool_calls,
        )
        return snapshot, self._last_nonempty_text(responses)

    def _extract_stream_message_stats(
        self, payloads: list[Dict[str, Any]]
    ) -> Optional[tuple[StatsSnapshot, Optional[str]]]:
        models_usage: Dict[str, Dict[str, int]] = {}
        tool_calls = 0
        responses: list[str] = []
        assistant_messages = 0
        current_assistant_active = False
        current_model_name: Optional[str] = None
        current_prompt_tokens: Optional[int] = None
        current_completion_tokens: Optional[int] = None
        current_response_parts: list[str] = []

        for payload in payloads:
            event_type = req_str(payload, "$.type")
            if event_type is None:
                continue

            if event_type == "message_start":
                current_assistant_active = False
                current_model_name = None
                current_prompt_tokens = None
                current_completion_tokens = None
                current_response_parts = []
                message = last_value(payload, "$.message")
                if not isinstance(message, dict) or req_str(message, "$.role") != "assistant":
                    continue
                current_assistant_active = True
                assistant_messages += 1
                model_name = req_str(message, "$.model")
                usage = last_value(message, "$.usage")
                parsed_usage = parse_usage_by_model(usage, "qoder_stream") if isinstance(usage, dict) else None
                current_model_name = model_name
                current_prompt_tokens = parsed_usage["prompt_tokens"] if parsed_usage is not None else None
                current_completion_tokens = parsed_usage["completion_tokens"] if parsed_usage is not None else None
                continue

            if not current_assistant_active:
                continue

            if event_type == "content_block_start":
                content_block = last_value(payload, "$.content_block")
                if not isinstance(content_block, dict):
                    continue
                block_type = req_str(content_block, "$.type")
                if block_type == "tool_use":
                    tool_calls += 1
                    continue
                if block_type != "text":
                    continue
                text = req_str(content_block, "$.text")
                if text:
                    current_response_parts.append(text)
                continue

            if event_type == "content_block_delta":
                delta = last_value(payload, "$.delta")
                if not isinstance(delta, dict) or req_str(delta, "$.type") != "text_delta":
                    continue
                text = req_str(delta, "$.text")
                if text:
                    current_response_parts.append(text)
                continue

            if event_type == "message_delta":
                usage = last_value(payload, "$.usage")
                parsed_usage = parse_usage_by_model(usage, "qoder_stream") if isinstance(usage, dict) else None
                if parsed_usage is not None:
                    current_completion_tokens = parsed_usage["completion_tokens"]
                continue

            if event_type != "message_stop":
                continue

            if (
                current_model_name is not None
                and current_prompt_tokens is not None
                and current_completion_tokens is not None
            ):
                total_tokens = current_prompt_tokens + current_completion_tokens
                self._merge_model_usage(
                    models_usage,
                    current_model_name,
                    {
                        "prompt_tokens": current_prompt_tokens,
                        "completion_tokens": current_completion_tokens,
                        "total_tokens": total_tokens,
                    },
                )
            response = "".join(current_response_parts).strip()
            if response:
                responses.append(response)
            current_assistant_active = False
            current_model_name = None
            current_prompt_tokens = None
            current_completion_tokens = None
            current_response_parts = []

        if assistant_messages < 1 or current_assistant_active:
            return None
        snapshot = self._normalize_stats_snapshot(
            models_usage=models_usage,
            llm_calls=assistant_messages,
            tool_calls=tool_calls,
        )
        return snapshot, self._last_nonempty_text(responses)
