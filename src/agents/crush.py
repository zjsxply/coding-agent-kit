from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import CodingAgent, InstallStrategy, VersionCommandTemplate
from ..models import RunResult
from .base import last_value, parse_usage_by_model, select_values, sum_int
from ..utils import format_trace_text


class CrushAgent(CodingAgent):
    name = "crush"
    display_name = "Crush"
    binary = "crush"
    supports_images = False
    supports_videos = False
    install_strategy = InstallStrategy(kind="npm", package="@charmland/crush")
    version_template = VersionCommandTemplate(
        args=("crush", "--version"),
        parse_mode="regex_first_line",
        regex=r"(?i)^(?:crush version )?([A-Za-z0-9._-]+)$",
    )

    def configure(self) -> Optional[str]:
        settings, error = self._resolve_api_settings(model_override=None)
        if error is not None or settings is None:
            return None
        config_path = Path.home() / ".config" / "crush" / "crush.json"
        payload = self._build_api_config_payload(model=settings["model"])
        self._write_text(config_path, json.dumps(payload, ensure_ascii=True, indent=2))
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
        settings, settings_error = self._resolve_api_settings(model_override=model_override)
        if settings_error is not None:
            return self._build_error_run_result(message=settings_error, cakit_exit_code=1)

        data_dir = self._make_temp_dir(prefix="cakit-crush-data-", keep=True)
        db_path = data_dir / "crush.db"
        telemetry_path = data_dir / "logs" / "crush.log"

        env: Dict[str, str] = {
            "CRUSH_DISABLE_PROVIDER_AUTO_UPDATE": "1",
        }
        selected_model = self._resolve_openai_model("CAKIT_CRUSH_MODEL", model_override=model_override)
        if settings is not None:
            config_dir = self._make_temp_dir(prefix="cakit-crush-config-")
            runtime_config_path = config_dir / "crush.json"
            payload = self._build_api_config_payload(model=settings["model"])
            self._write_text(runtime_config_path, json.dumps(payload, ensure_ascii=True, indent=2))
            env["CRUSH_GLOBAL_CONFIG"] = str(config_dir)
            env["CRUSH_GLOBAL_DATA"] = str(data_dir)
            env["CRUSH_OPENAI_API_KEY"] = settings["api_key"]
            env["CRUSH_OPENAI_BASE_URL"] = settings["base_url"]
            selected_model = settings["model"]

        cmd = [
            "crush",
            "--cwd",
            str(self.workdir),
            "--data-dir",
            str(data_dir),
            "run",
            "--quiet",
        ]
        if selected_model:
            provider_model = self._normalize_provider_model(
                selected_model,
                default_provider="cakit-openai",
                colon_as_provider=False,
            )
            cmd.extend(["--model", provider_model, "--small-model", provider_model])
        cmd.append(prompt)

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output

        models_usage, llm_calls, tool_calls, trace_payload = self._extract_stats_from_db(db_path)
        if trace_payload is not None:
            trajectory_content = format_trace_text(
                json.dumps({"db_path": str(db_path), "trace": trace_payload}, ensure_ascii=True),
                source=str(db_path),
            )
        else:
            trajectory_content = format_trace_text(output, source=str(db_path))
        response = self._last_stdout_line(output) or self._normalize_text(output)
        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            telemetry_log=str(telemetry_path),
            trajectory_content=trajectory_content,
        )

    def _resolve_api_settings(self, *, model_override: Optional[str]) -> tuple[Optional[Dict[str, str]], Optional[str]]:
        api_key = self._resolve_openai_api_key("CRUSH_OPENAI_API_KEY")
        base_url = self._resolve_openai_base_url("CRUSH_OPENAI_BASE_URL")
        model = self._resolve_openai_model("CAKIT_CRUSH_MODEL", model_override=model_override)

        any_set = bool(api_key or base_url or model)
        if not any_set:
            return None, None

        missing: list[tuple[str, str]] = []
        if not api_key:
            missing.append(("CRUSH_OPENAI_API_KEY", "OPENAI_API_KEY"))
        if not base_url:
            missing.append(("CRUSH_OPENAI_BASE_URL", "OPENAI_BASE_URL"))
        if not model:
            missing.append(("CAKIT_CRUSH_MODEL", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return None, self._missing_env_with_fallback_message(missing)

        return {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
        }, None

    def _build_api_config_payload(self, *, model: str) -> Dict[str, Any]:
        provider_id = "cakit-openai"
        return {
            "$schema": "https://charm.land/crush.json",
            "options": {
                "disable_provider_auto_update": True,
                "disable_default_providers": True,
            },
            "providers": {
                provider_id: {
                    "name": "CAKIT OpenAI Compatible",
                    "type": "openai-compat",
                    "base_url": "$CRUSH_OPENAI_BASE_URL",
                    "api_key": "$CRUSH_OPENAI_API_KEY",
                    "models": [
                        {
                            "id": model,
                            "name": model,
                        }
                    ],
                }
            },
            "models": {
                "large": {
                    "provider": provider_id,
                    "model": model,
                },
                "small": {
                    "provider": provider_id,
                    "model": model,
                },
            },
        }

    def _extract_stats_from_db(
        self, db_path: Path
    ) -> Tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int], Optional[Dict[str, Any]]]:
        if not db_path.exists():
            return {}, None, None, None
        try:
            conn = sqlite3.connect(str(db_path))
        except Exception:
            return {}, None, None, None

        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT id, title, prompt_tokens, completion_tokens, cost, created_at, updated_at
                FROM sessions
                WHERE parent_session_id IS NULL
                ORDER BY created_at DESC
                """
            ).fetchall()
            sessions: list[Dict[str, Any]] = []
            for row in rows:
                payload = dict(row)
                if self._normalize_text(last_value(payload, "$.id")) is None:
                    return {}, None, None, None
                sessions.append(payload)
            if len(sessions) != 1:
                return {}, None, None, None
            session = sessions[0]
            session_id = self._normalize_text(last_value(session, "$.id"))
            if session_id is None:
                return {}, None, None, None
            trace_payload = {
                "session": session,
                "messages": self._load_session_messages(conn, session_id=session_id),
            }
            if trace_payload is None:
                return {}, None, None, None
            session_payload = last_value(trace_payload, "$.session")
            usage = (
                parse_usage_by_model(
                    {
                        "prompt_tokens": last_value(session_payload, "$.prompt_tokens"),
                        "completion_tokens": last_value(session_payload, "$.completion_tokens"),
                    },
                    "prompt_completion",
                )
                if isinstance(session_payload, dict)
                else None
            )
            assistant_messages = select_values(
                trace_payload,
                '$.messages[?(@.is_non_summary_assistant == 1)]',
            )
            model_name: Optional[str] = None
            if assistant_messages is not None:
                model_names: set[str] = set()
                model_name_invalid = False
                for message in assistant_messages:
                    current_name = self._normalize_text(last_value(message, "$.model"))
                    if current_name is None:
                        model_name_invalid = True
                        break
                    model_names.add(current_name)
                if not model_name_invalid and len(model_names) == 1:
                    model_name = sorted(model_names)[0]
            models_usage = {model_name: usage} if usage is not None and model_name is not None else {}
            return (
                models_usage,
                sum_int(
                    trace_payload,
                    '$.messages[?(@.is_non_summary_assistant == 1)].message_count',
                ),
                sum_int(
                    trace_payload,
                    '$.messages[?(@.is_non_summary_assistant == 1)].tool_call_count',
                ),
                trace_payload,
            )
        except Exception:
            return {}, None, None, None
        finally:
            conn.close()

    def _load_session_messages(self, conn: sqlite3.Connection, *, session_id: str) -> list[Dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT
                m.id,
                m.role,
                m.model,
                m.provider,
                COALESCE(m.is_summary_message, 0) AS is_summary_message,
                m.created_at,
                m.updated_at,
                m.finished_at,
                m.parts,
                CASE
                    WHEN m.role = 'assistant' AND COALESCE(m.is_summary_message, 0) = 0 THEN 1
                    ELSE 0
                END AS is_non_summary_assistant,
                1 AS message_count,
                COALESCE(
                    (
                        SELECT COUNT(*)
                        FROM json_each(m.parts) p
                        WHERE json_extract(p.value, '$.type') = 'tool_call'
                    ),
                    0
                ) AS tool_call_count
            FROM messages m
            WHERE session_id = ?
            ORDER BY m.created_at ASC
            """,
            (session_id,),
        ).fetchall()
        messages: list[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            parts = last_value(item, "$.parts")
            if isinstance(parts, str):
                parsed_parts = self._parse_json(parts)
                if parsed_parts is not None:
                    item["parts"] = parsed_parts
            messages.append(item)
        return messages
