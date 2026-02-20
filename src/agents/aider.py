from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import CodingAgent, InstallStrategy, VersionCommandTemplate
from ..models import RunResult
from .base import (
    opt_float,
    req_int,
    req_str,
    select_values,
)
from ..utils import format_trace_text


class AiderAgent(CodingAgent):
    name = "aider"
    display_name = "Aider"
    binary = "aider"
    supports_images = True
    supports_videos = False
    install_strategy = InstallStrategy(
        kind="uv_tool",
        package="aider-chat",
        version_style="pep440",
        python_version="3.12",
        force=True,
    )
    version_template = VersionCommandTemplate(
        args=("aider", "--version"),
        parse_mode="regex_first_line",
        regex=r"^(?:aider\s+)?([A-Za-z0-9._-]+)$",
    )
    _OUTPUT_META_PREFIXES = (
        "Aider v",
        "Model:",
        "Main model:",
        "Weak model:",
        "Git repo:",
        "Repo-map:",
        "Added ",
        "https://aider.chat/HISTORY.html#release-notes",
    )
    _OUTPUT_SEPARATOR_LINES = {"--------------", "------------"}

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
        settings, env_error = self._resolve_runtime_settings(model_override=model_override)
        if env_error is not None:
            return self._build_error_run_result(message=env_error, cakit_exit_code=1)

        run_dir = self._make_temp_dir(prefix="cakit-aider-", keep=True)
        analytics_log = run_dir / "analytics.jsonl"
        input_history = run_dir / "input.history"
        chat_history = run_dir / "chat.history.md"
        llm_history = run_dir / "llm.history.log"

        cmd = [
            "aider",
            "--message",
            prompt,
            "--model",
            settings["model"],
            "--edit-format",
            "ask",
            "--no-git",
            "--yes-always",
            "--no-show-model-warnings",
            "--no-show-release-notes",
            "--no-check-update",
            "--no-fancy-input",
            "--no-suggest-shell-commands",
            "--no-pretty",
            "--no-stream",
            "--analytics-log",
            str(analytics_log),
            "--no-analytics",
            "--input-history-file",
            str(input_history),
            "--chat-history-file",
            str(chat_history),
            "--llm-history-file",
            str(llm_history),
        ]
        if reasoning_effort:
            cmd.extend(["--reasoning-effort", reasoning_effort])
        cmd.extend(str(image) for image in images)

        env: Dict[str, str] = {
            "AIDER_OPENAI_API_KEY": settings["api_key"],
        }
        if settings.get("api_base"):
            env["AIDER_OPENAI_API_BASE"] = settings["api_base"]

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        analytics_payloads: Optional[list[Dict[str, Any]]] = None
        if analytics_log.exists():
            analytics_text = self._read_text(analytics_log)
            if analytics_text:
                loaded_payloads = self._load_output_json_payloads(analytics_text, stdout_only=False)
                if loaded_payloads:
                    analytics_payloads = loaded_payloads
        models_usage, llm_calls, tool_calls, total_cost = self._extract_analytics_stats(
            payload_rows=analytics_payloads,
        )
        response = self._extract_response_from_output(output)
        parts = [output]
        analytics_text = self._read_text(analytics_log)
        if analytics_text and analytics_text.strip():
            parts.append(f"----- ANALYTICS LOG ({analytics_log}) -----\n{analytics_text}")
        chat_history_text = self._read_text(chat_history)
        if chat_history_text and chat_history_text.strip():
            parts.append(f"----- CHAT HISTORY ({chat_history}) -----\n{chat_history_text}")
        llm_history_text = self._read_text(llm_history)
        if llm_history_text and llm_history_text.strip():
            parts.append(f"----- LLM HISTORY ({llm_history}) -----\n{llm_history_text}")
        trajectory_content = format_trace_text("\n\n".join(parts), source=str(run_dir))
        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=total_cost,
            telemetry_log=str(analytics_log) if analytics_log.exists() else None,
            trajectory_content=trajectory_content,
        )

    def _resolve_runtime_settings(
        self, *, model_override: Optional[str]
    ) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
        api_key = self._resolve_openai_api_key("AIDER_OPENAI_API_KEY")
        api_base = self._resolve_openai_base_url("AIDER_OPENAI_API_BASE")
        model = self._resolve_litellm_model(
            "AIDER_MODEL",
            model_override=model_override,
            output_format="slash",
        )

        missing: list[tuple[str, str]] = []
        if api_key is None:
            missing.append(("AIDER_OPENAI_API_KEY", "OPENAI_API_KEY"))
        if model is None:
            missing.append(("AIDER_MODEL", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return None, self._missing_env_with_fallback_message(missing)

        return {
            "api_key": api_key,
            "api_base": api_base or "",
            "model": model,
        }, None

    def _extract_analytics_stats(
        self,
        *,
        payload_rows: Optional[list[Dict[str, Any]]],
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int], Optional[float]]:
        if not payload_rows:
            return {}, None, None, None

        message_send_properties = self._selected_dicts(payload_rows, '$[?(@.event == "message_send")].properties')
        llm_calls = len(message_send_properties) if message_send_properties else None
        models_usage: Dict[str, Dict[str, int]] = {}
        total_cost: Optional[float] = None

        for properties in message_send_properties:
            model_name = req_str(properties, "$.main_model")
            prompt_tokens = req_int(properties, "$.prompt_tokens")
            completion_tokens = req_int(properties, "$.completion_tokens")
            total_tokens = req_int(properties, "$.total_tokens")
            if model_name is None or prompt_tokens is None or completion_tokens is None or total_tokens is None:
                continue
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }

            self._merge_model_usage(models_usage, model_name, usage)

            candidate_total_cost = opt_float(properties, "$.total_cost")
            if candidate_total_cost is not None:
                total_cost = candidate_total_cost

        event_values = select_values(payload_rows, "$[*].event")
        tool_calls = (
            sum(1 for event in event_values if isinstance(event, str) and event.startswith("command_"))
            if event_values is not None
            else None
        )
        return models_usage, llm_calls, tool_calls, total_cost

    def _extract_response_from_output(self, output: str) -> Optional[str]:
        stdout = self._stdout_only(output)
        if not stdout.strip():
            return None

        entries: list[Dict[str, str]] = []
        current_section = "body"
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if line in self._OUTPUT_SEPARATOR_LINES:
                continue
            if line == "► **THINKING**":
                current_section = "thinking"
                continue
            if line == "► **ANSWER**":
                current_section = "answer"
                continue
            if not line:
                continue
            if line.startswith("Tokens:") or line.startswith("Cost:"):
                break
            if line.startswith(self._OUTPUT_META_PREFIXES):
                continue
            if current_section == "thinking":
                continue
            section = "answer" if current_section == "answer" else "body"
            entries.append({"section": section, "text": line})
        if not entries:
            return None

        answer = self._joined_selected_text(entries, '$[?(@.section == "answer")].text')
        if answer:
            return answer
        return self._joined_selected_text(entries, '$[?(@.section == "body")].text')
