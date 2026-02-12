from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class QwenAgent(CodingAgent):
    name = "qwen"
    display_name = "Qwen Code"
    binary = "qwen"
    supports_images = True
    supports_videos = True

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        result = self._npm_install("@qwen-code/qwen-code", scope, version=version)
        config_path = self.configure()
        ok = result.exit_code == 0
        details = result.output
        return InstallResult(
            agent=self.name,
            version=self.get_version() if ok else None,
            ok=ok,
            details=details,
            config_path=config_path,
        )

    def configure(self) -> Optional[str]:
        tavily_key = os.environ.get("TAVILY_API_KEY")
        google_key = os.environ.get("CAKIT_QWEN_GOOGLE_API_KEY")
        google_se_id = os.environ.get("GOOGLE_SEARCH_ENGINE_ID")
        providers = [{"type": "dashscope"}]
        default_provider = "dashscope"
        if tavily_key:
            providers.append({"type": "tavily", "apiKey": tavily_key})
            default_provider = "tavily"
        if google_key and google_se_id:
            providers.append({"type": "google", "apiKey": google_key, "searchEngineId": google_se_id})
            if default_provider == "dashscope":
                default_provider = "google"
        settings = {
            "webSearch": {
                "provider": providers,
                "default": default_provider,
            },
            "permissions": {
                "defaultMode": "yolo",
                "confirmShellCommands": False,
                "confirmFileEdits": False,
            },
            "telemetry": {
                "enabled": True,
                "target": "local",
                "otlpEndpoint": "",
                "logPrompts": True,
                "outfile": str(Path.home() / ".qwen" / "telemetry.log"),
            },
        }
        path = Path.home() / ".qwen" / "settings.json"
        self._write_text(path, json.dumps(settings, ensure_ascii=True, indent=2))
        return str(path)

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
        videos = videos or []
        if images or videos:
            prompt, _ = self._build_symbolic_media_prompt(
                prompt,
                [*images, *videos],
            )

        telemetry_path = str(Path.home() / ".qwen" / "telemetry.log")
        qwen_key = os.environ.get("QWEN_OPENAI_API_KEY")
        qwen_base = os.environ.get("QWEN_OPENAI_BASE_URL")
        qwen_model = model_override or os.environ.get("QWEN_OPENAI_MODEL")
        if isinstance(qwen_model, str):
            qwen_model = qwen_model.strip() or None
        qwen_google_api_key = os.environ.get("CAKIT_QWEN_GOOGLE_API_KEY")
        env = {
            "OPENAI_API_KEY": qwen_key,
            "OPENAI_BASE_URL": qwen_base,
            "OPENAI_MODEL": qwen_model,
            "TAVILY_API_KEY": os.environ.get("TAVILY_API_KEY"),
            "GOOGLE_API_KEY": qwen_google_api_key,
            "GOOGLE_SEARCH_ENGINE_ID": os.environ.get("GOOGLE_SEARCH_ENGINE_ID"),
        }
        cmd = [
            "qwen",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--approval-mode",
            "yolo",
            "--telemetry",
            "--telemetry-target",
            "local",
            "--telemetry-otlp-endpoint",
            "",
            "--telemetry-outfile",
            telemetry_path,
            "--telemetry-log-prompts",
        ]
        if qwen_key:
            cmd.extend(["--auth-type", "openai"])
        if qwen_model:
            cmd.extend(["--model", qwen_model])

        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        payload = self._parse_output_json(output)
        result_payload = self._extract_result_payload(payload)
        models_usage, llm_calls, tool_calls = self._extract_stats(result_payload)
        response = self._extract_response(payload, result_payload)

        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))
        run_exit_code = self._resolve_strict_run_exit_code(
            command_exit_code=result.exit_code,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            response=response,
        )
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            telemetry_log=telemetry_path,
            response=response,
            exit_code=run_exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["qwen", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _parse_output_json(self, output: str) -> Optional[Any]:
        stdout = self._stdout_only(output).strip()
        if not stdout:
            return None
        return self._extract_last_json_value(stdout)

    def _extract_result_payload(self, payload: Optional[Any]) -> Optional[Dict[str, Any]]:
        if isinstance(payload, dict):
            if payload.get("type") == "result":
                return payload
            return None
        if not isinstance(payload, list):
            return None
        result_payload: Optional[Dict[str, Any]] = None
        for item in payload:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "result":
                result_payload = item
        return result_payload

    def _extract_stats(
        self, result_payload: Optional[Dict[str, Any]]
    ) -> Tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int]]:
        models_usage: Dict[str, Dict[str, int]] = {}
        if not isinstance(result_payload, dict):
            return models_usage, None, None

        stats = result_payload.get("stats")
        if not isinstance(stats, dict):
            return {}, None, None

        models = stats.get("models")
        if not isinstance(models, dict) or not models:
            return {}, None, None

        llm_calls = 0
        for model_name, model_stats in models.items():
            if not isinstance(model_name, str) or not model_name.strip():
                return {}, None, None
            usage, calls = self._extract_model_usage(model_stats)
            if usage is None or calls is None:
                return {}, None, None
            models_usage[model_name] = usage
            llm_calls += calls

        tools = stats.get("tools")
        if not isinstance(tools, dict):
            return {}, None, None
        tool_calls = self._as_int(tools.get("totalCalls"))
        if tool_calls is None:
            return {}, None, None

        return models_usage, llm_calls, tool_calls

    def _extract_model_usage(self, model_stats: Any) -> Tuple[Optional[Dict[str, int]], Optional[int]]:
        if not isinstance(model_stats, dict):
            return None, None
        usage = self._extract_tokens_payload(model_stats.get("tokens"))
        llm_calls = self._extract_total_requests(model_stats.get("api"))
        if usage is None or llm_calls is None:
            return None, None
        return usage, llm_calls

    def _extract_tokens_payload(self, tokens: Any) -> Optional[Dict[str, int]]:
        if not isinstance(tokens, dict):
            return None
        prompt = self._as_int(tokens.get("prompt"))
        completion = self._as_int(tokens.get("candidates"))
        total = self._as_int(tokens.get("total"))
        if prompt is None or completion is None or total is None:
            return None
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }

    def _extract_total_requests(self, api: Any) -> Optional[int]:
        if not isinstance(api, dict):
            return None
        return self._as_int(api.get("totalRequests"))

    def _extract_response(self, payload: Optional[Any], result_payload: Optional[Dict[str, Any]]) -> Optional[str]:
        if isinstance(result_payload, dict):
            result_text = result_payload.get("result")
            if isinstance(result_text, str):
                cleaned = result_text.strip()
                if cleaned:
                    return cleaned

        if not isinstance(payload, list):
            return None
        for item in reversed(payload):
            if not isinstance(item, dict):
                continue
            if item.get("type") != "assistant":
                continue
            message = item.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            text = self._extract_assistant_text(content)
            if text:
                return text
        return None

    def _extract_assistant_text(self, content: Any) -> Optional[str]:
        if not isinstance(content, list):
            return None
        lines: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                lines.append(text.strip())
        if not lines:
            return None
        return "\n".join(lines)
