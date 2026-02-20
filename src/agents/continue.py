from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent, InstallStrategy, RunCommandTemplate
from ..models import RunResult
from .base import (
    last_value,
    opt_float,
    req_int,
    req_str,
    select_values,
)

class ContinueAgent(CodingAgent):
    name = "continue"
    display_name = "Continue"
    binary = "cn"
    supports_images = False
    supports_videos = False
    install_strategy = InstallStrategy(kind="npm", package="@continuedev/cli")
    run_template = RunCommandTemplate(
        base_args=("-p", "--auto"),
        prompt_mode="arg",
        prompt_flag=None,
        model_flag=None,
        media_injection="none",
    )

    def configure(self) -> Optional[str]:
        resolved, error = self._resolve_openai_auth(model_override=None)
        if error is not None:
            return None
        api_key = resolved.get("api_key")
        model = resolved.get("model")
        base_url = resolved.get("base_url")
        if not api_key or not model:
            return None
        continue_root = os.environ.get("CONTINUE_GLOBAL_DIR")
        config_path = (Path(continue_root).expanduser() if continue_root else Path.home() / ".continue") / "config.yaml"
        self._write_text(config_path, self._build_config_yaml(api_key=api_key, model=model, base_url=base_url))
        return str(config_path)

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        resolved, env_error = self._resolve_openai_auth(model_override=model_override)
        if env_error is not None:
            return self._build_error_run_result(message=env_error, cakit_exit_code=1)

        run_home = self._make_temp_dir(prefix="cakit-continue-", keep=True)
        config_path = run_home / "config.yaml"
        self._write_text(
            config_path,
            self._build_config_yaml(
                api_key=resolved["api_key"],
                model=resolved["model"],
                base_url=resolved.get("base_url"),
            ),
        )
        env = {
            "CONTINUE_GLOBAL_DIR": str(run_home),
            "FORCE_NO_TTY": "true",
            "OPENAI_API_KEY": resolved["api_key"],
            "OPENAI_MODEL": resolved["model"],
            "OPENAI_BASE_URL": resolved.get("base_url"),
        }
        template = self.run_template
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=prompt,
            extra_args=["--config", str(config_path)],
        )
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        sessions_dir = run_home / "sessions"
        session_payload: Optional[Dict[str, Any]] = None
        if sessions_dir.is_dir():
            session_id: Optional[str] = None
            manifest_path = sessions_dir / "sessions.json"
            if manifest_path.is_file():
                manifest = self._load_json(manifest_path)
                if isinstance(manifest, list) and manifest:
                    last_item = manifest[-1]
                    if isinstance(last_item, dict):
                        session_id = self._normalize_text(last_value(last_item, "$.sessionId"))
            else:
                candidates = sorted(path for path in sessions_dir.glob("*.json") if path.name != "sessions.json")
                if len(candidates) == 1:
                    session_id = candidates[0].stem
            if session_id:
                session_payload = self._load_json_dict(sessions_dir / f"{session_id}.json")
        models_usage, llm_calls, tool_calls, total_cost = self._extract_session_stats(
            session_payload=session_payload,
        )
        response: Optional[str] = None
        if isinstance(session_payload, dict):
            last_message = last_value(
                session_payload,
                '$.history[?(@.message.role == "assistant")].message',
            )
            if isinstance(last_message, dict):
                direct_content = self._normalize_text(last_value(last_message, "$.content"))
                if direct_content is not None:
                    response = direct_content
                else:
                    response = self._joined_selected_text(last_message, "$.content[*].text")
        if response is None:
            response = self._last_stdout_line(output)
        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=total_cost,
            telemetry_log=str(run_home / "logs" / "cn.log"),
        )

    def _resolve_openai_auth(self, *, model_override: Optional[str]) -> tuple[Dict[str, str], Optional[str]]:
        api_key = self._resolve_openai_api_key("CAKIT_CONTINUE_OPENAI_API_KEY")
        model = self._resolve_openai_model("CAKIT_CONTINUE_OPENAI_MODEL", model_override=model_override)
        base_url = self._resolve_openai_base_url("CAKIT_CONTINUE_OPENAI_BASE_URL")

        missing: list[tuple[str, str]] = []
        if not api_key:
            missing.append(("CAKIT_CONTINUE_OPENAI_API_KEY", "OPENAI_API_KEY"))
        if not model:
            missing.append(("CAKIT_CONTINUE_OPENAI_MODEL", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return {}, self._missing_env_with_fallback_message(missing)

        resolved: Dict[str, str] = {
            "api_key": api_key,
            "model": model,
        }
        if base_url:
            resolved["base_url"] = base_url
        return resolved, None

    def _build_config_yaml(self, *, api_key: str, model: str, base_url: Optional[str]) -> str:
        lines = [
            "name: CAKIT Continue Config",
            "version: 1.0.0",
            "schema: v1",
            "models:",
            "  - name: cakit-openai",
            "    provider: openai",
            f"    model: {json.dumps(model)}",
            f"    apiKey: {json.dumps(api_key)}",
        ]
        if base_url:
            lines.append(f"    apiBase: {json.dumps(base_url)}")
        lines.extend(
            [
                "    roles:",
                "      - chat",
            ]
        )
        return "\n".join(lines) + "\n"

    def _extract_session_stats(
        self,
        *,
        session_payload: Optional[Dict[str, Any]],
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int], Optional[float]]:
        payload = session_payload if isinstance(session_payload, dict) else None
        if payload is None:
            return {}, None, None, None

        assistant_messages = select_values(
            payload,
            '$.history[?(@.message.role == "assistant")].message',
        )
        llm_calls = len(assistant_messages) if assistant_messages is not None else None

        models_usage: Dict[str, Dict[str, int]] = {}
        for usage in select_values(payload, '$.history[?(@.message.role == "assistant")].message.usage') or []:
            if not isinstance(usage, dict):
                continue
            model_name = req_str(usage, "$.model")
            prompt_tokens = req_int(usage, "$.prompt_tokens")
            completion_tokens = req_int(usage, "$.completion_tokens")
            if model_name is None or prompt_tokens is None or completion_tokens is None:
                continue
            total_tokens = req_int(usage, "$.total_tokens")
            if total_tokens is None:
                total_tokens = prompt_tokens + completion_tokens
            usage_entry = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }
            self._merge_model_usage(models_usage, model_name, usage_entry)

        tool_call_values = select_values(
            payload,
            '$.history[?(@.message.role == "assistant")].message.toolCalls[*]',
        )
        return (
            models_usage,
            llm_calls,
            (len(tool_call_values) if tool_call_values is not None else None),
            opt_float(payload, "$.usage.totalCost"),
        )
