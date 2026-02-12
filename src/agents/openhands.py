from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class OpenHandsAgent(CodingAgent):
    name = "openhands"
    display_name = "OpenHands"
    binary = "openhands"
    _CONVERSATION_ID_RE = re.compile(r"Conversation ID:\s*([0-9a-fA-F-]{32,36})")

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        package_spec = "openhands"
        if version:
            normalized = version.strip()
            if normalized:
                package_spec = f"openhands=={normalized}"
        if self._ensure_uv():
            result = self._run(["uv", "tool", "install", package_spec, "--python", "3.12"])
        else:
            result = self._run(["python", "-m", "pip", "install", package_spec])
        ok = result.exit_code == 0
        return InstallResult(
            agent=self.name,
            version=self.get_version(),
            ok=ok,
            details=result.output,
            config_path=None,
        )

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
        env, env_error = self._build_run_env(model_override=model_override)
        if env_error is not None:
            output_path = self._write_output(self.name, env_error)
            trajectory_path = self._write_trajectory(
                self.name,
                format_trace_text(env_error, source=str(output_path)),
            )
            return RunResult(
                agent=self.name,
                agent_version=self.get_version(),
                runtime_seconds=0.0,
                models_usage={},
                tool_calls=None,
                llm_calls=None,
                response=env_error,
                exit_code=1,
                output_path=str(output_path),
                raw_output=env_error,
                trajectory_path=str(trajectory_path) if trajectory_path else None,
            )

        cmd = [
            "openhands",
            "--headless",
            "--json",
            "--override-with-envs",
            "-t",
            prompt,
        ]
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        output_path = self._write_output(self.name, output)
        conversation_id = self._extract_conversation_id(output)
        conversation_dir, base_state, events = self._load_conversation_artifacts(conversation_id)

        model_name, usage, llm_calls, total_cost = self._extract_metrics_from_base_state(base_state)
        models_usage = self._ensure_models_usage({}, usage, model_name)
        tool_calls = self._count_tool_calls(events)
        response = self._extract_response_from_events(events)

        has_error_event = self._has_error_event(events)
        run_exit_code = self._resolve_run_exit_code(
            command_exit_code=result.exit_code,
            has_error_event=has_error_event,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            response=response,
        )
        trajectory_content = self._build_trajectory_content(
            output=output,
            output_path=output_path,
            conversation_id=conversation_id,
            conversation_dir=conversation_dir,
            base_state=base_state,
            events=events,
        )
        trajectory_path = self._write_trajectory(self.name, trajectory_content)

        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            total_cost=total_cost,
            response=response,
            exit_code=run_exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["openhands", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _build_run_env(self, *, model_override: Optional[str] = None) -> tuple[Dict[str, str], Optional[str]]:
        api_key = os.environ.get("LLM_API_KEY")
        model = model_override or os.environ.get("LLM_MODEL")
        base_url = os.environ.get("LLM_BASE_URL")

        missing: list[str] = []
        if not api_key:
            missing.append("LLM_API_KEY")
        if not model:
            missing.append("LLM_MODEL")
        if missing:
            return {}, f"missing required environment variable(s): {', '.join(missing)}"

        resolved_model = self._normalize_model(model=model, base_url=base_url)
        env: Dict[str, str] = {
            "LLM_API_KEY": api_key,
            "LLM_MODEL": resolved_model,
        }
        if base_url:
            env["LLM_BASE_URL"] = base_url
        return env, None

    @staticmethod
    def _normalize_model(*, model: str, base_url: Optional[str]) -> str:
        normalized = model.strip()
        if "/" in normalized:
            return normalized
        if base_url:
            return f"openai/{normalized}"
        return normalized

    def _extract_conversation_id(self, output: str) -> Optional[str]:
        match = self._CONVERSATION_ID_RE.search(output)
        if not match:
            return None
        raw_value = match.group(1).strip().lower().replace("-", "")
        if len(raw_value) != 32:
            return None
        return raw_value

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

        try:
            base_state = json.loads(base_state_path.read_text(encoding="utf-8"))
        except Exception:
            return conversation_root, None, None
        if not isinstance(base_state, dict):
            return conversation_root, None, None

        events: list[Dict[str, Any]] = []
        event_paths = sorted(events_dir.glob("event-*.json"))
        for event_path in event_paths:
            try:
                parsed = json.loads(event_path.read_text(encoding="utf-8"))
            except Exception:
                return conversation_root, base_state, None
            if not isinstance(parsed, dict):
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

    def _extract_metrics_from_base_state(
        self, base_state: Optional[Dict[str, Any]]
    ) -> tuple[Optional[str], Optional[Dict[str, int]], Optional[int], Optional[float]]:
        if not isinstance(base_state, dict):
            return None, None, None, None

        stats = base_state.get("stats")
        if not isinstance(stats, dict):
            return None, None, None, None

        usage_to_metrics = stats.get("usage_to_metrics")
        if not isinstance(usage_to_metrics, dict):
            return None, None, None, None

        agent_metrics = usage_to_metrics.get("agent")
        if not isinstance(agent_metrics, dict):
            return None, None, None, None

        model_name = agent_metrics.get("model_name")
        if not isinstance(model_name, str) or not model_name.strip():
            model_name = None

        accumulated = agent_metrics.get("accumulated_token_usage")
        usage: Optional[Dict[str, int]] = None
        if isinstance(accumulated, dict):
            prompt_tokens = self._as_int(accumulated.get("prompt_tokens"))
            completion_tokens = self._as_int(accumulated.get("completion_tokens"))
            if prompt_tokens is not None and completion_tokens is not None:
                usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                }

        token_usages = agent_metrics.get("token_usages")
        llm_calls: Optional[int] = None
        if isinstance(token_usages, list) and all(isinstance(item, dict) for item in token_usages):
            llm_calls = len(token_usages)

        accumulated_cost = agent_metrics.get("accumulated_cost")
        total_cost: Optional[float] = None
        if isinstance(accumulated_cost, (int, float)) and not isinstance(accumulated_cost, bool):
            total_cost = float(accumulated_cost)

        return model_name, usage, llm_calls, total_cost

    def _count_tool_calls(self, events: Optional[list[Dict[str, Any]]]) -> Optional[int]:
        if not isinstance(events, list):
            return None
        count = 0
        for event in events:
            if not isinstance(event, dict):
                return None
            if event.get("kind") != "ActionEvent":
                continue
            tool_name = event.get("tool_name")
            if not isinstance(tool_name, str) or not tool_name:
                return None
            count += 1
        return count

    def _extract_response_from_events(self, events: Optional[list[Dict[str, Any]]]) -> Optional[str]:
        if not isinstance(events, list):
            return None

        finish_candidates: list[str] = []
        message_candidates: list[str] = []
        for event in events:
            if not isinstance(event, dict):
                return None
            kind = event.get("kind")
            if kind == "ObservationEvent":
                observation = event.get("observation")
                if not isinstance(observation, dict):
                    continue
                if observation.get("kind") != "FinishObservation":
                    continue
                content = observation.get("content")
                if not isinstance(content, list):
                    return None
                parts: list[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        return None
                    if item.get("type") != "text":
                        continue
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                if parts:
                    finish_candidates.append("\n".join(parts))
                continue

            if kind != "MessageEvent":
                continue
            if event.get("source") != "agent":
                continue
            llm_message = event.get("llm_message")
            if not isinstance(llm_message, dict):
                continue
            if llm_message.get("role") != "assistant":
                continue
            content = llm_message.get("content")
            if not isinstance(content, list):
                return None
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    return None
                if item.get("type") != "text":
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            if parts:
                message_candidates.append("\n".join(parts))

        if finish_candidates:
            return finish_candidates[-1]
        if message_candidates:
            return message_candidates[-1]
        return None

    def _has_error_event(self, events: Optional[list[Dict[str, Any]]]) -> bool:
        if not isinstance(events, list):
            return False
        for event in events:
            if not isinstance(event, dict):
                continue
            kind = event.get("kind")
            if kind in {"ConversationErrorEvent", "AgentErrorEvent"}:
                return True
        return False

    def _resolve_run_exit_code(
        self,
        *,
        command_exit_code: int,
        has_error_event: bool,
        models_usage: Dict[str, Dict[str, int]],
        llm_calls: Optional[int],
        tool_calls: Optional[int],
        response: Optional[str],
    ) -> int:
        if command_exit_code != 0:
            return command_exit_code
        if has_error_event:
            return 1
        if not models_usage:
            return 1
        if llm_calls is None or llm_calls < 1:
            return 1
        if tool_calls is None:
            return 1
        if not isinstance(response, str) or not response.strip():
            return 1
        return 0

    def _build_trajectory_content(
        self,
        *,
        output: str,
        output_path: Path,
        conversation_id: Optional[str],
        conversation_dir: Optional[Path],
        base_state: Optional[Dict[str, Any]],
        events: Optional[list[Dict[str, Any]]],
    ) -> str:
        if conversation_id and conversation_dir and base_state and isinstance(events, list):
            payload = {
                "conversation_id": conversation_id,
                "conversation_dir": str(conversation_dir),
                "base_state": base_state,
                "events": events,
            }
            payload_text = json.dumps(payload, ensure_ascii=True)
            return format_trace_text(payload_text, source=str(conversation_dir))
        return format_trace_text(output, source=str(output_path))
