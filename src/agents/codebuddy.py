from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text, load_json_payloads


class CodeBuddyAgent(CodingAgent):
    name = "codebuddy"
    display_name = "CodeBuddy Code"
    binary = "codebuddy"
    supports_images = True
    supports_videos = False

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        return self._install_with_npm(package="@tencent-ai/codebuddy-code", scope=scope, version=version)

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
        del videos, reasoning_effort
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
        payloads = load_json_payloads(self._stdout_only(output))
        stats = self._extract_stream_json_stats(payloads)

        output_path = self._write_output(self.name, output)
        trajectory_content = format_trace_text(output, source=str(output_path))
        trajectory_path = self._write_trajectory(self.name, trajectory_content)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=stats["models_usage"],
            tool_calls=stats["tool_calls"],
            llm_calls=stats["llm_calls"],
            total_cost=stats["total_cost"],
            response=stats["response"],
            cakit_exit_code=1 if result.exit_code == 0 and stats["result_is_error"] else None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        return self._version_first_line(["codebuddy", "--version"])

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

        init_model = self._extract_init_model(payloads)
        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls = 0
        tool_calls = 0
        last_assistant_text: Optional[str] = None

        for payload in payloads:
            if not isinstance(payload, dict):
                return defaults
            if payload.get("type") != "assistant":
                continue
            metrics = self._extract_assistant_metrics(payload, init_model=init_model)
            if metrics is None:
                return defaults
            model_name, usage, message_text, message_tool_calls = metrics
            entry = models_usage.setdefault(
                model_name,
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            entry["prompt_tokens"] += usage["prompt_tokens"]
            entry["completion_tokens"] += usage["completion_tokens"]
            entry["total_tokens"] += usage["total_tokens"]
            llm_calls += 1
            tool_calls += message_tool_calls
            if message_text:
                last_assistant_text = message_text

        result_payload = self._extract_result_payload(payloads)
        if result_payload is None:
            return defaults
        result_meta = self._extract_result_meta(
            result_payload,
            fallback_response=last_assistant_text,
        )
        if result_meta is None:
            return defaults

        if llm_calls < 1:
            return {
                "models_usage": {},
                "llm_calls": None,
                "tool_calls": None,
                "total_cost": result_meta["total_cost"],
                "response": result_meta["response"],
                "result_is_error": result_meta["is_error"],
            }

        return {
            "models_usage": models_usage,
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "total_cost": result_meta["total_cost"],
            "response": result_meta["response"],
            "result_is_error": result_meta["is_error"],
        }

    def _extract_assistant_metrics(
        self,
        payload: Dict[str, Any],
        *,
        init_model: Optional[str],
    ) -> Optional[tuple[str, Dict[str, int], Optional[str], int]]:
        message = payload.get("message")
        if not isinstance(message, dict):
            return None

        usage_raw = message.get("usage")
        if not isinstance(usage_raw, dict):
            return None
        usage = self._normalize_usage(usage_raw)
        if usage is None:
            return None

        model_name = self._normalize_text(message.get("model"))
        if model_name is None:
            model_name = init_model
        if model_name is None:
            return None

        content = message.get("content")
        message_tool_calls, message_text = self._extract_content_metrics(content)
        if message_tool_calls is None:
            return None

        return model_name, usage, message_text, message_tool_calls

    def _extract_content_metrics(self, content: Any) -> tuple[Optional[int], Optional[str]]:
        if not isinstance(content, list):
            return None, None
        tool_calls = 0
        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                return None, None
            block_type = block.get("type")
            if block_type == "tool_use":
                tool_id = block.get("id")
                tool_name = block.get("name")
                if not isinstance(tool_id, str) or not tool_id.strip():
                    return None, None
                if not isinstance(tool_name, str) or not tool_name.strip():
                    return None, None
                tool_calls += 1
                continue
            if block_type != "text":
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
        assistant_text = "\n".join(text_parts) if text_parts else None
        return tool_calls, assistant_text

    def _extract_init_model(self, payloads: list[Dict[str, Any]]) -> Optional[str]:
        for payload in payloads:
            if not isinstance(payload, dict):
                return None
            if payload.get("type") != "system" or payload.get("subtype") != "init":
                continue
            model = self._normalize_text(payload.get("model"))
            if model is not None:
                return model
        return None

    def _extract_result_payload(self, payloads: list[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        result_payload: Optional[Dict[str, Any]] = None
        for payload in payloads:
            if not isinstance(payload, dict):
                return None
            if payload.get("type") == "result":
                result_payload = payload
        return result_payload

    def _extract_result_meta(
        self,
        payload: Dict[str, Any],
        *,
        fallback_response: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        subtype = self._normalize_text(payload.get("subtype"))
        if subtype is None:
            return None
        is_error = payload.get("is_error")
        if not isinstance(is_error, bool):
            return None

        usage_raw = payload.get("usage")
        if not isinstance(usage_raw, dict):
            return None
        if self._normalize_usage(usage_raw) is None:
            return None

        total_cost_raw = payload.get("total_cost_usd")
        total_cost = None
        if isinstance(total_cost_raw, (int, float)) and not isinstance(total_cost_raw, bool):
            total_cost = float(total_cost_raw)

        response: Optional[str] = None
        if subtype == "success":
            result_text = payload.get("result")
            if not isinstance(result_text, str) or not result_text.strip():
                return None
            response = result_text.strip()
        else:
            response = fallback_response or self._extract_error_text(payload)

        return {
            "response": response,
            "total_cost": total_cost,
            "is_error": is_error,
        }

    def _extract_error_text(self, payload: Dict[str, Any]) -> Optional[str]:
        errors = payload.get("errors")
        if not isinstance(errors, list):
            return None
        texts: list[str] = []
        for item in errors:
            if isinstance(item, str) and item.strip():
                texts.append(item.strip())
        if not texts:
            return None
        return "\n".join(texts)

    def _normalize_usage(self, usage: Dict[str, Any]) -> Optional[Dict[str, int]]:
        input_tokens = self._as_int(usage.get("input_tokens"))
        output_tokens = self._as_int(usage.get("output_tokens"))
        if input_tokens is None or output_tokens is None:
            return None
        cache_read = self._optional_cached_tokens(usage, key="cache_read_input_tokens")
        cache_creation = self._optional_cached_tokens(usage, key="cache_creation_input_tokens")
        if cache_read is None or cache_creation is None:
            return None
        prompt_tokens = input_tokens + cache_read + cache_creation
        completion_tokens = output_tokens
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def _optional_cached_tokens(self, usage: Dict[str, Any], *, key: str) -> Optional[int]:
        raw = usage.get(key)
        if raw is None:
            return 0
        value = self._as_int(raw)
        if value is None:
            return None
        return value

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
            suffix = resolved.suffix.lower()
            if suffix == ".jpg":
                media_type = "image/jpeg"
            elif suffix == ".jpeg":
                media_type = "image/jpeg"
            elif suffix == ".png":
                media_type = "image/png"
            elif suffix == ".gif":
                media_type = "image/gif"
            elif suffix == ".webp":
                media_type = "image/webp"
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
