from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class CrushAgent(CodingAgent):
    name = "crush"
    display_name = "Crush"
    binary = "crush"
    supports_images = False
    supports_videos = False

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        result = self._npm_install("@charmland/crush", scope, version=version)
        config_path = self.configure()
        ok = result.exit_code == 0
        return InstallResult(
            agent=self.name,
            version=self.get_version() if ok else None,
            ok=ok,
            details=result.output,
            config_path=config_path,
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
            output_path = self._write_output(self.name, settings_error)
            trajectory_path = self._write_trajectory(
                self.name,
                format_trace_text(settings_error, source=str(output_path)),
            )
            return RunResult(
                agent=self.name,
                agent_version=self.get_version(),
                runtime_seconds=0.0,
                models_usage={},
                tool_calls=None,
                llm_calls=None,
                response=settings_error,
                exit_code=1,
                output_path=str(output_path),
                raw_output=settings_error,
                trajectory_path=str(trajectory_path) if trajectory_path else None,
            )

        data_dir = Path(tempfile.mkdtemp(prefix="cakit-crush-data-"))
        db_path = data_dir / "crush.db"
        telemetry_path = data_dir / "logs" / "crush.log"

        env: Dict[str, str] = {
            "CRUSH_DISABLE_PROVIDER_AUTO_UPDATE": "1",
        }
        selected_model = self._normalize_model_name(model_override or os.environ.get("CAKIT_CRUSH_MODEL"))
        if settings is not None:
            config_dir = Path(tempfile.mkdtemp(prefix="cakit-crush-config-"))
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
            provider_model = self._provider_model_id(selected_model)
            cmd.extend(["--model", provider_model, "--small-model", provider_model])
        cmd.append(prompt)

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        response = self._extract_response(output)
        output_path = self._write_output(self.name, output)

        models_usage, llm_calls, tool_calls, trace_payload = self._extract_stats_from_db(db_path)
        trajectory_content = self._build_trajectory_content(
            db_path=db_path,
            trace_payload=trace_payload,
            raw_output=output,
            output_path=output_path,
        )
        trajectory_path = self._write_trajectory(self.name, trajectory_content)
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
            telemetry_log=str(telemetry_path),
            response=response,
            exit_code=run_exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["crush", "--version"])
        text = result.output.strip()
        if result.exit_code != 0 or not text:
            return None
        line = [item.strip() for item in text.splitlines() if item.strip()]
        if not line:
            return None
        first = line[0]
        prefix = "crush version "
        lowered = first.lower()
        if lowered.startswith(prefix):
            return first[len(prefix) :].strip() or None
        return first

    def _resolve_api_settings(self, *, model_override: Optional[str]) -> tuple[Optional[Dict[str, str]], Optional[str]]:
        api_key = os.environ.get("CRUSH_OPENAI_API_KEY")
        base_url = os.environ.get("CRUSH_OPENAI_BASE_URL")
        model = self._normalize_model_name(model_override or os.environ.get("CAKIT_CRUSH_MODEL"))

        any_set = bool((api_key and api_key.strip()) or (base_url and base_url.strip()) or model)
        if not any_set:
            return None, None

        missing: list[str] = []
        if not api_key or not api_key.strip():
            missing.append("CRUSH_OPENAI_API_KEY")
        if not base_url or not base_url.strip():
            missing.append("CRUSH_OPENAI_BASE_URL")
        if not model:
            missing.append("CAKIT_CRUSH_MODEL")
        if missing:
            return None, f"missing required environment variable(s): {', '.join(missing)}"

        return {
            "api_key": api_key.strip(),
            "base_url": base_url.strip(),
            "model": model,
        }, None

    @staticmethod
    def _normalize_model_name(value: Optional[str]) -> Optional[str]:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

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

    def _extract_response(self, output: str) -> Optional[str]:
        stdout = self._stdout_only(output).strip()
        if not stdout:
            cleaned = output.strip()
            if cleaned:
                return cleaned
            return None
        return stdout

    @staticmethod
    def _provider_model_id(model: str) -> str:
        normalized = model.strip()
        if "/" in normalized:
            return normalized
        return f"cakit-openai/{normalized}"

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
            session = self._load_single_root_session(conn)
            if session is None:
                return {}, None, None, None

            session_id = session.get("id")
            if not isinstance(session_id, str) or not session_id.strip():
                return {}, None, None, None

            prompt_tokens = self._as_int(session.get("prompt_tokens"))
            completion_tokens = self._as_int(session.get("completion_tokens"))
            if prompt_tokens is None or completion_tokens is None:
                return {}, None, None, None

            model_name = self._load_single_model_name(conn, session_id)
            if model_name is None:
                return {}, None, None, None

            llm_calls = self._load_llm_calls(conn, session_id)
            tool_calls = self._load_tool_calls(conn, session_id)
            if llm_calls is None or tool_calls is None:
                return {}, None, None, None

            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
            models_usage = {model_name: usage}
            trace_payload = self._build_trace_payload(conn, session=session, session_id=session_id)
            return models_usage, llm_calls, tool_calls, trace_payload
        except Exception:
            return {}, None, None, None
        finally:
            conn.close()

    def _load_single_root_session(self, conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT id, title, prompt_tokens, completion_tokens, cost, created_at, updated_at
            FROM sessions
            WHERE parent_session_id IS NULL
            ORDER BY created_at DESC
            """
        ).fetchall()
        if len(rows) != 1:
            return None
        row = rows[0]
        data = dict(row)
        if not isinstance(data.get("id"), str) or not str(data.get("id")).strip():
            return None
        return data

    def _load_single_model_name(self, conn: sqlite3.Connection, session_id: str) -> Optional[str]:
        rows = conn.execute(
            """
            SELECT DISTINCT model
            FROM messages
            WHERE session_id = ?
              AND role = 'assistant'
              AND COALESCE(is_summary_message, 0) = 0
            ORDER BY model ASC
            """,
            (session_id,),
        ).fetchall()
        models: list[str] = []
        for row in rows:
            value = row["model"] if isinstance(row, sqlite3.Row) else None
            if not isinstance(value, str):
                return None
            cleaned = value.strip()
            if not cleaned:
                return None
            models.append(cleaned)
        if len(models) != 1:
            return None
        return models[0]

    def _load_llm_calls(self, conn: sqlite3.Connection, session_id: str) -> Optional[int]:
        row = conn.execute(
            """
            SELECT COUNT(*) AS llm_calls
            FROM messages
            WHERE session_id = ?
              AND role = 'assistant'
              AND COALESCE(is_summary_message, 0) = 0
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._as_int(row["llm_calls"])

    def _load_tool_calls(self, conn: sqlite3.Connection, session_id: str) -> Optional[int]:
        row = conn.execute(
            """
            SELECT COUNT(*) AS tool_calls
            FROM messages m, json_each(m.parts) p
            WHERE m.session_id = ?
              AND m.role = 'assistant'
              AND COALESCE(m.is_summary_message, 0) = 0
              AND json_extract(p.value, '$.type') = 'tool_call'
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._as_int(row["tool_calls"])

    def _build_trace_payload(
        self,
        conn: sqlite3.Connection,
        *,
        session: Dict[str, Any],
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT id, role, model, provider, is_summary_message, created_at, updated_at, finished_at, parts
            FROM messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        ).fetchall()
        messages: list[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            parts = item.get("parts")
            if isinstance(parts, str):
                try:
                    parsed = json.loads(parts)
                except Exception:
                    parsed = parts
                item["parts"] = parsed
            messages.append(item)
        return {
            "session": session,
            "messages": messages,
        }

    def _build_trajectory_content(
        self,
        *,
        db_path: Path,
        trace_payload: Optional[Dict[str, Any]],
        raw_output: str,
        output_path: Path,
    ) -> str:
        if trace_payload is not None:
            payload = {
                "db_path": str(db_path),
                "trace": trace_payload,
            }
            return format_trace_text(json.dumps(payload, ensure_ascii=True), source=str(db_path))
        return format_trace_text(raw_output, source=str(output_path))
