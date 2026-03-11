from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from .base import (
    CodingAgent,
    InstallStrategy,
    RunParseResult,
    RunPlan,
    RunCommandTemplate,
    StatsParseResult,
)
from ..stats_extract import (
    last_value,
    merge_model_usage,
    merge_stats_snapshots,
    normalize_stats_snapshot,
    parse_usage_by_model,
    req_str,
    select_values,
)
from ..agent_runtime import parsing as runtime_parsing


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

    def _build_run_plan(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> Optional[RunPlan]:
        images = images or []
        model = runtime_parsing.normalize_text(model_override or os.environ.get("CAKIT_QODER_MODEL"))
        env = {
            "QODER_PERSONAL_ACCESS_TOKEN": os.environ.get("QODER_PERSONAL_ACCESS_TOKEN"),
        }
        extra_args = [arg for image in images for arg in ("--attachment", str(image))]
        telemetry_path = Path.home() / ".qoder" / "logs" / "qodercli.log"
        return self._build_templated_run_plan(
            prompt=prompt,
            model=model,
            env=env,
            template=self.run_template,
            extra_args=extra_args,
            parse_output=lambda output, command_result: self._parse_pipeline_output(
                output,
                command_result,
                telemetry_path=telemetry_path,
            ),
        )

    def _parse_pipeline_output(
        self,
        output: str,
        command_result: Any,
        *,
        telemetry_path: Path,
    ) -> RunParseResult:
        payloads = runtime_parsing.load_output_json_payloads(output)
        parsed_stats = self._extract_stats(payloads)
        stats = merge_stats_snapshots([parsed_stats.snapshot if parsed_stats is not None else None])
        return RunParseResult(
            response=parsed_stats.response if parsed_stats is not None else None,
            models_usage=stats.models_usage,
            llm_calls=stats.llm_calls,
            tool_calls=stats.tool_calls,
            total_cost=stats.total_cost,
            telemetry_log=str(telemetry_path) if telemetry_path.exists() else None,
        )

    def _extract_stats(self, payloads: list[Dict[str, Any]]) -> Optional[StatsParseResult]:
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
    ) -> Optional[StatsParseResult]:
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
            merge_model_usage(models_usage, record["model_name"], record["usage"])
        tool_calls = sum(record["tool_calls"] for record in records)
        responses = [record["response"] for record in records if record["response"] is not None]
        snapshot = normalize_stats_snapshot(
            models_usage=models_usage,
            llm_calls=(len(records) if records else None),
            tool_calls=tool_calls,
        )
        return StatsParseResult(snapshot=snapshot, response=runtime_parsing.last_nonempty_text(responses))

    def _extract_stream_message_stats(
        self, payloads: list[Dict[str, Any]]
    ) -> Optional[StatsParseResult]:
        assistant_start_messages = [
            message
            for message in (
                select_values(
                    payloads,
                    '$[?(@.type == "message_start")].message',
                )
                or []
            )
            if isinstance(message, dict) and req_str(message, "$.role") == "assistant"
        ]
        if not assistant_start_messages:
            return None

        llm_call_ids = {
            message_id
            for message in assistant_start_messages
            if (message_id := req_str(message, "$.id")) is not None
        }
        llm_calls = len(llm_call_ids) if llm_call_ids else len(assistant_start_messages)
        tool_calls = len(select_values(payloads, '$[?(@.content_block.type == "tool_use")]') or [])

        models_usage: Dict[str, Dict[str, int]] = {}
        responses: list[str] = []
        active_message: Optional[dict[str, Any]] = None

        # JSONPath can batch-pick event subsets, but response/usage finalization depends on stream order.
        for payload in payloads:
            event_type = req_str(payload, "$.type")
            if event_type == "message_start":
                message = last_value(payload, "$.message")
                if not isinstance(message, dict) or req_str(message, "$.role") != "assistant":
                    active_message = None
                    continue
                parsed_usage = (
                    parse_usage_by_model(raw_usage, "qoder_stream")
                    if isinstance(raw_usage := last_value(message, "$.usage"), dict)
                    else None
                )
                active_message = {
                    "model_name": req_str(message, "$.model"),
                    "prompt_tokens": parsed_usage["prompt_tokens"] if parsed_usage is not None else None,
                    "completion_tokens": parsed_usage["completion_tokens"] if parsed_usage is not None else None,
                    "parts": [],
                }
                continue

            if active_message is None:
                continue

            if event_type == "content_block_start" and req_str(payload, "$.content_block.type") == "text":
                if (text := req_str(payload, "$.content_block.text")) is not None:
                    active_message["parts"].append(text)
                continue

            if event_type == "content_block_delta" and req_str(payload, "$.delta.type") == "text_delta":
                if (text := req_str(payload, "$.delta.text")) is not None:
                    active_message["parts"].append(text)
                continue

            if event_type == "message_delta":
                parsed_usage = (
                    parse_usage_by_model(raw_usage, "qoder_stream")
                    if isinstance(raw_usage := last_value(payload, "$.usage"), dict)
                    else None
                )
                if parsed_usage is not None:
                    active_message["completion_tokens"] = parsed_usage["completion_tokens"]
                continue

            if event_type != "message_stop":
                continue

            model_name = active_message.get("model_name")
            prompt_tokens = active_message.get("prompt_tokens")
            completion_tokens = active_message.get("completion_tokens")
            if model_name is not None and prompt_tokens is not None and completion_tokens is not None:
                total_tokens = prompt_tokens + completion_tokens
                merge_model_usage(
                    models_usage,
                    model_name,
                    {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    },
                )
            response = "".join(active_message.get("parts", [])).strip()
            if response:
                responses.append(response)
            active_message = None

        if active_message is not None:
            return None
        snapshot = normalize_stats_snapshot(
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
        )
        return StatsParseResult(snapshot=snapshot, response=runtime_parsing.last_nonempty_text(responses))
