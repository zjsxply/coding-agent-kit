from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .base import (
    CodingAgent,
    InstallStrategy,
    extract_jsonl_stats,
    last_value,
    opt_float,
    req_str,
    select_values,
)
from ..models import RunResult


class CodeBuddyAgent(CodingAgent):
    name = "codebuddy"
    display_name = "CodeBuddy Code"
    binary = "codebuddy"
    supports_images = True
    supports_videos = False
    install_strategy = InstallStrategy(kind="npm", package="@tencent-ai/codebuddy-code")

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
        selected_model = self._resolve_openai_model("CODEBUDDY_MODEL", model_override=model_override)
        api_key = self._resolve_openai_api_key("CODEBUDDY_API_KEY")
        base_url = self._resolve_openai_base_url("CODEBUDDY_BASE_URL")
        cmd = ["codebuddy", "-p", "--output-format", "stream-json", "-y"]
        input_text: Optional[str] = None
        if images:
            stream_input, build_error = self._build_stream_json_input(prompt=prompt, images=images)
            if build_error:
                return self._build_error_run_result(message=build_error, cakit_exit_code=2)
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
        result = self._run(cmd, env=env, input_text=input_text, base_env=base_env)
        output = result.output
        payloads = self._load_output_json_payloads(output)
        stats = self._extract_stream_json_stats(payloads)

        run_result = self.finalize_run(
            command_result=result,
            response=stats["response"],
            models_usage=stats["models_usage"],
            llm_calls=stats["llm_calls"],
            tool_calls=stats["tool_calls"],
            total_cost=stats["total_cost"],
        )
        if result.exit_code == 0 and stats["result_is_error"]:
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

        artifacts = self._build_stats_artifacts(jsonl_payloads=payloads)
        snapshot = self._merge_stats_snapshots(
            snapshots=[
                extract_jsonl_stats(
                    artifacts,
                    source_field="jsonl_payloads",
                    model_field="$.message.model",
                    usage_field="$.message.usage",
                ),
            ]
        )
        result_payload = last_value(payloads, '$[?(@.type == "result")]')
        result_subtype = req_str(result_payload, "$.subtype") if isinstance(result_payload, dict) else None
        result_response = req_str(result_payload, "$.result") if isinstance(result_payload, dict) else None
        result_total_cost = opt_float(result_payload, "$.total_cost_usd") if isinstance(result_payload, dict) else None
        result_is_error = bool(isinstance(result_payload, dict) and last_value(result_payload, "$.is_error") is True)

        last_assistant_payload = last_value(payloads, '$[?(@.type == "assistant")]')
        assistant_response = self._joined_selected_text(
            last_assistant_payload,
            '$.message.content[?(@.type == "text")].text',
        ) or self._last_selected_text(
            payloads,
            '$[?(@.type == "assistant")].message.content[?(@.type == "text")].text',
        )
        error_response = self._joined_selected_text(result_payload, "$.errors[*]")
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

        media_type = self._normalize_text(mimetypes.guess_type(str(resolved))[0])
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
