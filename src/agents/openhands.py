from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .base import (
    CodingAgent,
    InstallStrategy,
    ParsedStats,
    RunCommandTemplate,
    RunParseResult,
    RunPlan,
)
from ..stats_extract import (
    build_single_model_stats_snapshot,
    last_value,
    opt_float,
    parse_usage_by_model,
    req_str,
    select_values,
)
from ..agent_runtime import env as runtime_env
from ..agent_runtime import parsing as runtime_parsing
from ..agent_runtime import trajectory as runtime_trajectory


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

    def _build_run_plan(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> Optional[RunPlan]:
        env = self._build_run_env(model_override=model_override)
        run_root = self._make_temp_dir(prefix="cakit-openhands-")
        persistence_dir = run_root / "persistence"
        conversations_root = persistence_dir / "conversations"
        env.update(
            {
                "HOME": str(run_root / "home"),
                "OPENHANDS_PERSISTENCE_DIR": str(persistence_dir),
            }
        )

        has_error_event = {"value": False}
        return self._build_templated_run_plan(
            prompt=prompt,
            env=env,
            template=self.run_template,
            parse_output=lambda output, command_result: self._parse_pipeline_output(
                output,
                command_result,
                has_error_event=has_error_event,
                conversations_root=conversations_root,
            ),
            post_finalize=lambda run_result, parsed, command_result: self._post_finalize_pipeline(
                run_result=run_result,
                command_result=command_result,
                has_error_event=has_error_event["value"],
            ),
        )

    def _parse_pipeline_output(
        self,
        output: str,
        command_result: Any,
        *,
        has_error_event: Dict[str, bool],
        conversations_root: Path,
    ) -> RunParseResult:
        match = self._CONVERSATION_ID_RE.search(output)
        if match:
            normalized_conversation = match.group(1).strip().lower().replace("-", "")
            conversation_id = normalized_conversation if len(normalized_conversation) == 32 else None
        else:
            conversation_id = None
        conversation_dir, base_state, events = self._load_conversation_artifacts(
            conversation_id,
            conversations_root=conversations_root,
        )

        parsed_stats = self._extract_stats(base_state=base_state, events=events)
        snapshot = build_single_model_stats_snapshot(
            model_name=parsed_stats.model_name,
            usage=parsed_stats.usage,
            llm_calls=parsed_stats.llm_calls,
            tool_calls=parsed_stats.tool_calls,
            total_cost=parsed_stats.total_cost,
        )

        has_error_event["value"] = bool(
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
            trajectory_content = runtime_trajectory.build_trajectory_from_raw(
                raw_text=json.dumps(payload, ensure_ascii=True),
                output=output,
                source=str(conversation_dir),
            )
        else:
            trajectory_content = runtime_trajectory.build_trajectory_content(
                output=output,
                source=str(conversations_root),
            )
        return RunParseResult(
            response=parsed_stats.response,
            models_usage=snapshot.models_usage if snapshot is not None else {},
            llm_calls=snapshot.llm_calls if snapshot is not None else None,
            tool_calls=snapshot.tool_calls if snapshot is not None else None,
            total_cost=snapshot.total_cost if snapshot is not None else None,
            trajectory_content=trajectory_content,
        )

    def _post_finalize_pipeline(
        self,
        *,
        run_result,
        command_result: Any,
        has_error_event: bool,
    ):
        if command_result.exit_code == 0 and has_error_event:
            run_result.cakit_exit_code = 1
        return run_result

    def _build_run_env(self, *, model_override: Optional[str] = None) -> Dict[str, str]:
        resolved, error = runtime_env.resolve_openai_env(
            api_key_env="LLM_API_KEY",
            model_env="LLM_MODEL",
            base_url_env="LLM_BASE_URL",
            model_override=model_override,
            require_api_key=True,
            require_model=True,
            normalize_text=runtime_parsing.normalize_text,
        )
        if error is not None:
            self._raise_config_error(error)

        model_raw = resolved.get("model")
        model = (
            runtime_env.normalize_litellm_model(model_raw, output_format="slash")
            if isinstance(model_raw, str)
            else None
        )
        if not model:
            message = runtime_env.missing_env_with_fallback_message([("LLM_MODEL", "OPENAI_DEFAULT_MODEL")])
            self._raise_config_error(message or "missing required environment variable(s): LLM_MODEL")
        env: Dict[str, str] = {
            "LLM_API_KEY": str(resolved.get("api_key")),
            "LLM_MODEL": model,
        }
        base_url = resolved.get("base_url")
        if isinstance(base_url, str) and base_url.strip():
            env["LLM_BASE_URL"] = base_url
        return env

    def _load_conversation_artifacts(
        self,
        conversation_id: Optional[str],
        *,
        conversations_root: Optional[Path] = None,
    ) -> tuple[Optional[Path], Optional[Dict[str, Any]], Optional[list[Dict[str, Any]]]]:
        if not conversation_id:
            return None, None, None

        conversation_root = (conversations_root or self._conversations_root()) / conversation_id
        if not conversation_root.is_dir():
            return None, None, None

        base_state_path = conversation_root / "base_state.json"
        events_dir = conversation_root / "events"
        if not base_state_path.is_file() or not events_dir.is_dir():
            return conversation_root, None, None

        base_state = runtime_parsing.load_json_dict(base_state_path)
        if base_state is None:
            return conversation_root, None, None

        events: list[Dict[str, Any]] = []
        for event_path in sorted(events_dir.glob("event-*.json")):
            parsed = runtime_parsing.load_json_dict(event_path)
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
        self,
        *,
        base_state: Optional[Dict[str, Any]],
        events: Optional[list[Dict[str, Any]]],
    ) -> ParsedStats:
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
        if isinstance(events, list):
            tool_calls = sum(1 for value in (action_tool_names or []) if isinstance(value, str) and value.strip())
        else:
            tool_calls = None

        finish_texts = runtime_parsing.extract_content_texts(
            events,
            '$[?(@.observation.kind == "FinishObservation")].observation.content',
            allow_scalars=False,
        )
        assistant_texts = runtime_parsing.extract_content_texts(
            events,
            '$[?(@.llm_message.role == "assistant")].llm_message.content',
            allow_scalars=False,
        )
        response = None
        if finish_texts:
            response = finish_texts[-1]
        elif assistant_texts:
            response = assistant_texts[-1]

        return ParsedStats(
            model_name=model_name,
            usage=usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=total_cost,
            response=response,
        )
