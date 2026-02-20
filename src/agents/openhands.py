from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent, InstallStrategy, RunCommandTemplate
from ..models import RunResult
from ..stats_extract import last_value, opt_float, parse_usage_by_model, req_str, select_values
from ..utils import format_trace_text


class OpenHandsAgent(CodingAgent):
    name = "openhands"
    display_name = "OpenHands"
    binary = "openhands"
    install_strategy = InstallStrategy(
        kind="uv_tool",
        package="openhands",
        version_style="pep440",
        python_version="3.12",
    )
    run_template = RunCommandTemplate(
        base_args=("--headless", "--json", "--override-with-envs"),
        prompt_mode="flag",
        prompt_flag="-t",
        model_flag=None,
        media_injection="none",
    )
    _CONVERSATION_ID_RE = re.compile(r"Conversation ID:\s*([0-9a-fA-F-]{32,36})")

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        env, env_error = self._build_run_env(model_override=model_override)
        if env_error is not None:
            return self._build_error_run_result(message=env_error, cakit_exit_code=1)

        template = self.run_template
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=prompt,
        )
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        match = self._CONVERSATION_ID_RE.search(output)
        if match:
            normalized_conversation = match.group(1).strip().lower().replace("-", "")
            conversation_id = normalized_conversation if len(normalized_conversation) == 32 else None
        else:
            conversation_id = None
        conversation_dir, base_state, events = self._load_conversation_artifacts(conversation_id)

        model_name, usage, llm_calls, tool_calls, total_cost, response = self._extract_stats(
            base_state=base_state,
            events=events,
        )
        snapshot = self._build_single_model_stats_snapshot(
            model_name=model_name,
            usage=usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=total_cost,
        )

        has_error_event = bool(
            isinstance(events, list)
            and (
                select_values(events, '$[?(@.kind == "ConversationErrorEvent")]')
                or select_values(events, '$[?(@.kind == "AgentErrorEvent")]')
            )
        )
        if conversation_id and conversation_dir and base_state and isinstance(events, list):
            payload = {
                "conversation_id": conversation_id,
                "conversation_dir": str(conversation_dir),
                "base_state": base_state,
                "events": events,
            }
            trajectory_content = format_trace_text(
                json.dumps(payload, ensure_ascii=True),
                source=str(conversation_dir),
            )
        else:
            trajectory_content = format_trace_text(output, source=str(self._conversations_root()))
        run_result = self.finalize_run(
            command_result=result,
            response=response,
            models_usage=snapshot.models_usage if snapshot is not None else {},
            llm_calls=snapshot.llm_calls if snapshot is not None else None,
            tool_calls=snapshot.tool_calls if snapshot is not None else None,
            total_cost=snapshot.total_cost if snapshot is not None else None,
            trajectory_content=trajectory_content,
        )
        run_result.cakit_exit_code = (
            1
            if result.exit_code == 0 and has_error_event
            else self._resolve_strict_run_exit_code(
                command_exit_code=result.exit_code,
                models_usage=run_result.models_usage,
                llm_calls=run_result.llm_calls,
                tool_calls=run_result.tool_calls,
                response=run_result.response,
            )
        )
        return run_result

    def _build_run_env(self, *, model_override: Optional[str] = None) -> tuple[Dict[str, str], Optional[str]]:
        api_key = self._resolve_openai_api_key("LLM_API_KEY")
        model = self._resolve_litellm_model(
            "LLM_MODEL",
            model_override=model_override,
            output_format="slash",
        )
        base_url = self._resolve_openai_base_url("LLM_BASE_URL")

        missing: list[tuple[str, str]] = []
        if not api_key:
            missing.append(("LLM_API_KEY", "OPENAI_API_KEY"))
        if not model:
            missing.append(("LLM_MODEL", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return {}, self._missing_env_with_fallback_message(missing)

        env: Dict[str, str] = {
            "LLM_API_KEY": api_key,
            "LLM_MODEL": model,
        }
        if base_url:
            env["LLM_BASE_URL"] = base_url
        return env, None

    def _load_conversation_artifacts(
        self, conversation_id: Optional[str]
    ) -> tuple[Optional[Path], Optional[Dict[str, Any]], Optional[list[Dict[str, Any]]]]:
        if not conversation_id:
            return None, None, None

        conversation_root = self._conversations_root() / conversation_id
        if not conversation_root.is_dir():
            return None, None, None

        base_state_path = conversation_root / "base_state.json"
        events_dir = conversation_root / "events"
        if not base_state_path.is_file() or not events_dir.is_dir():
            return conversation_root, None, None

        base_state = self._load_json_dict(base_state_path)
        if base_state is None:
            return conversation_root, None, None

        events: list[Dict[str, Any]] = []
        for event_path in sorted(events_dir.glob("event-*.json")):
            parsed = self._load_json_dict(event_path)
            if parsed is None:
                return conversation_root, base_state, None
            events.append(parsed)

        return conversation_root, base_state, events

    @staticmethod
    def _conversations_root() -> Path:
        conversations_dir = os.environ.get("OPENHANDS_CONVERSATIONS_DIR")
        if conversations_dir:
            return Path(conversations_dir).expanduser()
        persistence_dir = os.environ.get("OPENHANDS_PERSISTENCE_DIR")
        if persistence_dir:
            return Path(persistence_dir).expanduser() / "conversations"
        return Path.home() / ".openhands" / "conversations"

    def _extract_stats(
        self, *, base_state: Optional[Dict[str, Any]], events: Optional[list[Dict[str, Any]]]
    ) -> tuple[Optional[str], Optional[Dict[str, int]], Optional[int], Optional[int], Optional[float], Optional[str]]:
        model_name: Optional[str] = None
        usage: Optional[Dict[str, int]] = None
        llm_calls: Optional[int] = None
        total_cost: Optional[float] = None
        if isinstance(base_state, dict):
            model_name = req_str(base_state, "$.stats.usage_to_metrics.agent.model_name")
            accumulated = last_value(base_state, "$.stats.usage_to_metrics.agent.accumulated_token_usage")
            usage = parse_usage_by_model(accumulated, "prompt_completion") if isinstance(accumulated, dict) else None
            token_usages = select_values(base_state, "$.stats.usage_to_metrics.agent.token_usages[*]")
            llm_calls = len(token_usages) if isinstance(token_usages, list) else None
            total_cost = opt_float(base_state, "$.stats.usage_to_metrics.agent.accumulated_cost")

        action_tool_names = select_values(events, '$[?(@.kind == "ActionEvent")].tool_name')
        tool_calls = (
            sum(1 for value in action_tool_names if isinstance(value, str) and value.strip())
            if action_tool_names is not None
            else None
        )

        finish_texts = self._extract_content_texts(
            events,
            '$[?(@.observation.kind == "FinishObservation")].observation.content',
            allow_scalars=False,
        )
        assistant_texts = self._extract_content_texts(
            events,
            '$[?(@.llm_message.role == "assistant")].llm_message.content',
            allow_scalars=False,
        )
        response = None
        if finish_texts:
            response = finish_texts[-1]
        elif assistant_texts:
            response = assistant_texts[-1]

        return model_name, usage, llm_calls, tool_calls, total_cost, response
