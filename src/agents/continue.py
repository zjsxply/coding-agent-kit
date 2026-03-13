from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from ..agent_runtime import env as runtime_env
from ..agent_runtime import parsing as runtime_parsing
from ..io_helpers import dump_yaml
from .base import (
    CodingAgent,
    InstallStrategy,
    RunCommandTemplate,
    RunParseResult,
    RunPlan,
)
from ..stats_extract import last_value, merge_model_usage, opt_float, req_int, req_str, select_values

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

    def _continue_root(self) -> Path:
        continue_root = os.environ.get("CONTINUE_GLOBAL_DIR")
        if continue_root:
            return Path(continue_root).expanduser()
        return self._resolve_writable_dir(
            Path.home() / ".continue",
            Path("/tmp") / "cakit" / "continue",
            purpose="Continue config",
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
        config_path = self._continue_root() / "config.yaml"
        self._write_text(config_path, self._build_config_yaml(api_key=api_key, model=model, base_url=base_url))
        return str(config_path)

    def _build_run_plan(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> Optional[RunPlan]:
        resolved, env_error = self._resolve_openai_auth(model_override=model_override)
        if env_error is not None:
            self._raise_config_error(env_error)

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
        return self._build_templated_run_plan(
            prompt=prompt,
            env=env,
            template=self.run_template,
            extra_args=["--config", str(config_path)],
            parse_output=lambda output, command_result: self._parse_pipeline_output(
                output,
                command_result,
                run_home=run_home,
            ),
        )

    def _parse_pipeline_output(
        self,
        output: str,
        command_result: Any,
        *,
        run_home: Path,
    ) -> RunParseResult:
        sessions_dir = run_home / "sessions"
        session_payload: Optional[Dict[str, Any]] = None
        if sessions_dir.is_dir():
            session_id: Optional[str] = None
            manifest_path = sessions_dir / "sessions.json"
            if manifest_path.is_file():
                manifest = runtime_parsing.load_json(manifest_path)
                if isinstance(manifest, list) and manifest:
                    last_item = manifest[-1]
                    if isinstance(last_item, dict):
                        session_id = runtime_parsing.normalize_text(last_value(last_item, "$.sessionId"))
            else:
                candidates = sorted(path for path in sessions_dir.glob("*.json") if path.name != "sessions.json")
                if len(candidates) == 1:
                    session_id = candidates[0].stem
            if session_id:
                session_payload = runtime_parsing.load_json_dict(sessions_dir / f"{session_id}.json")
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
                direct_content = runtime_parsing.normalize_text(last_value(last_message, "$.content"))
                if direct_content is not None:
                    response = direct_content
                else:
                    text_parts = [
                        text
                        for text in (
                            runtime_parsing.normalize_text(item)
                            for item in (select_values(last_message, "$.content[*].text") or [])
                        )
                        if text is not None
                    ]
                    if text_parts:
                        response = "\n".join(text_parts)
        if response is None:
            response = runtime_parsing.last_stdout_line(output)
        return RunParseResult(
            response=response,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=total_cost,
            telemetry_log=str(run_home / "logs" / "cn.log"),
        )

    def _resolve_openai_auth(self, *, model_override: Optional[str]) -> tuple[Dict[str, str], Optional[str]]:
        resolved, error = runtime_env.resolve_openai_env(
            api_key_env="CAKIT_CONTINUE_OPENAI_API_KEY",
            model_env="CAKIT_CONTINUE_OPENAI_MODEL",
            base_url_env="CAKIT_CONTINUE_OPENAI_BASE_URL",
            model_override=model_override,
            normalize_text=runtime_parsing.normalize_text,
        )
        if error is not None:
            return {}, error

        auth: Dict[str, str] = {
            "api_key": str(resolved.get("api_key")),
            "model": str(resolved.get("model")),
        }
        base_url = resolved.get("base_url")
        if isinstance(base_url, str) and base_url.strip():
            auth["base_url"] = base_url
        return auth, None

    def _build_config_yaml(self, *, api_key: str, model: str, base_url: Optional[str]) -> str:
        config = {
            "name": "CAKIT Continue Config",
            "version": "1.0.0",
            "schema": "v1",
            "models": [
                {
                    "name": "cakit-openai",
                    "provider": "openai",
                    "model": model,
                    "apiKey": api_key,
                    "roles": ["chat"],
                }
            ],
        }
        if base_url:
            config["models"][0]["apiBase"] = base_url
        return dump_yaml(config)

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
            merge_model_usage(models_usage, model_name, usage_entry)

        tool_call_values = select_values(
            payload,
            '$.history[?(@.message.role == "assistant")].message.toolCalls[*]',
        )
        return (
            models_usage,
            llm_calls,
            (len(tool_call_values) if tool_call_values is not None else 0),
            opt_float(payload, "$.usage.totalCost"),
        )
