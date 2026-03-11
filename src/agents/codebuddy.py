from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, Optional

from ..agent_runtime import env as runtime_env
from ..agent_runtime import parsing as runtime_parsing
from .base import (
    CodingAgent,
    InstallStrategy,
    RunParseResult,
    RunPlan,
)
from ..stats_extract import (
    JsonlStatsSpec,
    StatsArtifacts,
    extract_jsonl_stats,
    last_value,
    merge_stats_snapshots,
    opt_float,
    req_str,
    select_values,
)


class CodeBuddyAgent(CodingAgent):
    name = "codebuddy"
    display_name = "CodeBuddy Code"
    binary = "codebuddy"
    supports_images = True
    supports_videos = False
    install_strategy = InstallStrategy(kind="npm", package="@tencent-ai/codebuddy-code")

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
        selected_model = runtime_env.resolve_openai_model("CODEBUDDY_MODEL", model_override=model_override)
        api_key = runtime_env.resolve_openai_api_key("CODEBUDDY_API_KEY")
        base_url = runtime_env.resolve_openai_base_url("CODEBUDDY_BASE_URL")
        cmd = ["codebuddy", "-p", "--output-format", "stream-json", "-y"]
        input_text: Optional[str] = None
        if images:
            stream_input, build_error = self._build_stream_json_input(prompt=prompt, images=images)
            if build_error:
                self._raise_capability_error(build_error)
            cmd.extend(["--input-format", "stream-json"])
            input_text = stream_input
        if selected_model:
            cmd.extend(["--model", selected_model])
        if not images:
            cmd.append(prompt)

        env = {
            "CODEBUDDY_API_KEY": api_key,
            "CODEBUDDY_AUTH_TOKEN": os.environ.get("CODEBUDDY_AUTH_TOKEN"),
            "CODEBUDDY_BASE_URL": base_url,
            "CODEBUDDY_MODEL": selected_model,
            "CODEBUDDY_INTERNET_ENVIRONMENT": os.environ.get("CODEBUDDY_INTERNET_ENVIRONMENT"),
        }
        result_is_error = {"value": False}
        return RunPlan(
            command=cmd,
            env=env,
            input_text=input_text,
            parse_output=lambda output, command_result: self._parse_pipeline_output(
                output,
                command_result,
                result_is_error=result_is_error,
            ),
            post_finalize=lambda run_result, parsed, command_result: self._post_finalize_pipeline(
                run_result=run_result,
                command_result=command_result,
                result_is_error=result_is_error["value"],
            ),
        )

    def _parse_pipeline_output(
        self,
        output: str,
        command_result: Any,
        *,
        result_is_error: Dict[str, bool],
    ) -> RunParseResult:
        payloads = runtime_parsing.load_output_json_payloads(output)
        stats = self._extract_stream_json_stats(payloads)
        result_is_error["value"] = bool(stats["result_is_error"])
        return RunParseResult(
            response=stats["response"],
            models_usage=stats["models_usage"],
            llm_calls=stats["llm_calls"],
            tool_calls=stats["tool_calls"],
            total_cost=stats["total_cost"],
        )

    def _post_finalize_pipeline(
        self,
        *,
        run_result,
        command_result: Any,
        result_is_error: bool,
    ):
        if command_result.exit_code == 0 and result_is_error:
            run_result.cakit_exit_code = 1
        return run_result

    def _extract_stream_json_stats(self, payloads: list[Dict[str, Any]]) -> Dict[str, Any]:
        defaults = {
            "models_usage": {},
            "llm_calls": None,
            "tool_calls": None,
            "total_cost": None,
            "response": None,
            "result_is_error": False,
        }
        if not payloads:
            return defaults

        artifacts = StatsArtifacts(jsonl_payloads=tuple(payloads))
        stats_spec = JsonlStatsSpec(
            source_field="jsonl_payloads",
            model_field="$.message.model",
            usage_field="$.message.usage",
        )
        snapshot = merge_stats_snapshots(
            snapshots=[
                extract_jsonl_stats(
                    artifacts,
                    spec=stats_spec,
                ),
            ]
        )
        result_payload = last_value(payloads, '$[?(@.type == "result")]')
        result_subtype = req_str(result_payload, "$.subtype") if isinstance(result_payload, dict) else None
        result_response = req_str(result_payload, "$.result") if isinstance(result_payload, dict) else None
        result_total_cost = opt_float(result_payload, "$.total_cost_usd") if isinstance(result_payload, dict) else None
        result_is_error = bool(isinstance(result_payload, dict) and last_value(result_payload, "$.is_error") is True)

        last_assistant_payload = last_value(payloads, '$[?(@.type == "assistant")]')
        assistant_parts = [
            text
            for text in (
                runtime_parsing.normalize_text(item)
                for item in (
                    select_values(last_assistant_payload, '$.message.content[?(@.type == "text")].text') or []
                )
            )
            if text is not None
        ]
        assistant_response = (
            ("\n".join(assistant_parts) if assistant_parts else None)
            or runtime_parsing.last_nonempty_text(
                select_values(
                    payloads,
                    '$[?(@.type == "assistant")].message.content[?(@.type == "text")].text',
                )
            )
        )
        error_parts = [
            text
            for text in (
                runtime_parsing.normalize_text(item)
                for item in (select_values(result_payload, "$.errors[*]") or [])
            )
            if text is not None
        ]
        error_response = "\n".join(error_parts) if error_parts else None
        llm_call_values = select_values(payloads, '$[?(@.type == "assistant")]')
        llm_calls = len(llm_call_values) if llm_call_values is not None else snapshot.llm_calls
        tool_call_values = select_values(payloads, '$[?(@.type == "assistant")].message.content[?(@.type == "tool_use")]')
        tool_calls = len(tool_call_values) if tool_call_values is not None else snapshot.tool_calls
        return {
            "models_usage": snapshot.models_usage,
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "total_cost": (result_total_cost if result_total_cost is not None else snapshot.total_cost),
            "response": (
                result_response
                if result_subtype == "success" and result_response is not None
                else assistant_response or error_response
            ),
            "result_is_error": result_is_error,
        }

    def _build_stream_json_input(
        self,
        *,
        prompt: str,
        images: list[Path],
    ) -> tuple[Optional[str], Optional[str]]:
        content: list[Dict[str, Any]] = []
        content.append({"type": "text", "text": prompt})
        for image in images:
            block, error = self._build_image_content_block(image)
            if error:
                return None, error
            content.append(block)
        payload = {
            "type": "user",
            "message": {
                "role": "user",
                "content": content,
            },
        }
        return json.dumps(payload, ensure_ascii=True) + "\n", None

    def _build_image_content_block(self, image: Path) -> tuple[Dict[str, Any], Optional[str]]:
        resolved = image.expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            return {}, f"image file not found: {resolved}"

        media_type = runtime_parsing.normalize_text(mimetypes.guess_type(str(resolved))[0])
        if media_type is None:
            suffix_to_type = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }
            media_type = suffix_to_type.get(resolved.suffix.lower())
        if media_type is None or not media_type.startswith("image/"):
            return {}, f"unsupported image media type for stream-json: {resolved}"

        try:
            encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
        except Exception as exc:
            return {}, f"failed to read image file: {resolved} ({exc})"

        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": encoded,
            },
        }, None
