from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07]*\x07")
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_MODEL_TAG_RE = re.compile(r"<model>([^<]+)</model>")


class KiloCodeAgent(CodingAgent):
    name = "kilocode"
    display_name = "Kilo Code"
    binary = "kilocode"
    supports_images = True
    supports_videos = False

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        return self._install_with_npm(package="@kilocode/cli", scope=scope, version=version)

    def configure(self) -> Optional[str]:
        payload, _ = self._build_runtime_config_payload(model_override=None)
        if payload is None:
            return None
        config_path = Path.home() / ".kilocode" / "cli" / "config.json"
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
        agent_version = self.get_version()
        major = self._parse_major_version(agent_version)
        if major is not None and major >= 1:
            return self._run_impl_v1(
                prompt,
                images=images,
                videos=videos,
                reasoning_effort=reasoning_effort,
                model_override=model_override,
                base_env=base_env,
                agent_version=agent_version,
            )
        return self._run_impl_v0(
            prompt,
            images=images,
            videos=videos,
            reasoning_effort=reasoning_effort,
            model_override=model_override,
            base_env=base_env,
            agent_version=agent_version,
        )

    def _run_impl_v0(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
        agent_version: Optional[str] = None,
    ) -> RunResult:
        del videos, reasoning_effort
        images = images or []
        config_payload, config_error = self._build_runtime_config_payload(model_override=model_override)
        if config_payload is None:
            message = config_error or "missing required Kilo Code API settings"
            return self._build_error_run_result(
                message=message,
                cakit_exit_code=1,
                agent_version=agent_version,
            )

        run_home = Path(tempfile.mkdtemp(prefix="cakit-kilocode-home-"))
        runtime_config_path = run_home / ".kilocode" / "cli" / "config.json"
        self._write_text(runtime_config_path, json.dumps(config_payload, ensure_ascii=True, indent=2))

        env = {
            "HOME": str(run_home),
            "KILO_DISABLE_AUTOUPDATE": "true",
            "KILO_TELEMETRY": "false",
        }
        cmd = [
            "kilocode",
            "--auto",
            "--json",
            "--yolo",
            "--workspace",
            str(self.workdir),
            "--nosplash",
        ]
        for image_path in images:
            cmd.extend(["--attach", str(image_path)])
        if model_override:
            cmd.extend(["--model", model_override])
        cmd.append(prompt)

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        payloads = self._load_json_payloads_with_ansi_cleanup(output)

        global_state = self._load_global_state(run_home)
        task_item = self._extract_task_history_item(global_state, prompt)
        task_id = self._extract_task_id(task_item)
        task_dir = run_home / ".kilocode" / "cli" / "global" / "tasks" / task_id if task_id else None
        ui_messages = self._load_json_array(task_dir / "ui_messages.json") if task_dir else None
        api_history = self._load_json_array(task_dir / "api_conversation_history.json") if task_dir else None

        usage = self._extract_usage_from_ui_messages(ui_messages)
        model_name = self._extract_model_name(task_item, global_state, api_history)

        output_path = self._write_output(self.name, output)
        trajectory_payload = self._build_trajectory_payload(
            task_item=task_item,
            ui_messages=ui_messages,
            api_history=api_history,
            stream_payloads=payloads,
        )
        if trajectory_payload is not None:
            trajectory_source = str(task_dir) if task_dir else str(output_path)
            trajectory_content = format_trace_text(json.dumps(trajectory_payload, ensure_ascii=True), source=trajectory_source)
        else:
            trajectory_content = format_trace_text(output, source=str(output_path))
        trajectory_path = self._write_trajectory(self.name, trajectory_content)
        return RunResult(
            agent=self.name,
            agent_version=agent_version,
            runtime_seconds=result.duration_seconds,
            models_usage=self._ensure_models_usage({}, usage, model_name) if usage is not None and model_name else {},
            tool_calls=self._extract_tool_calls_from_api_history(api_history),
            llm_calls=self._extract_llm_calls_from_ui_messages(ui_messages),
            total_cost=self._extract_total_cost(task_item),
            response=self._extract_response(payloads, ui_messages, api_history, output),
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def _run_impl_v1(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
        agent_version: Optional[str] = None,
    ) -> RunResult:
        del videos, reasoning_effort
        images = images or []
        api_key = self._normalize_text(os.environ.get("KILO_OPENAI_API_KEY"))
        model = self._normalize_model_id(model_override) or self._normalize_model_id(os.environ.get("KILO_OPENAI_MODEL_ID"))
        base_url = self._normalize_text(os.environ.get("KILO_OPENAI_BASE_URL"))

        missing: List[str] = []
        if api_key is None:
            missing.append("KILO_OPENAI_API_KEY")
        if model is None:
            missing.append("KILO_OPENAI_MODEL_ID")
        if missing:
            message = self._missing_env_message(missing) or "missing required Kilo Code API settings"
            return self._build_error_run_result(
                message=message,
                cakit_exit_code=1,
                agent_version=agent_version,
            )

        run_home = Path(tempfile.mkdtemp(prefix="cakit-kilocode-home-"))
        env = {
            "HOME": str(run_home),
            "OPENAI_API_KEY": api_key,
            "KILO_DISABLE_AUTOUPDATE": "true",
            "KILO_TELEMETRY": "false",
        }
        if base_url:
            env["OPENAI_BASE_URL"] = base_url

        cmd = ["kilocode", "run", "--auto", "--format", "json"]
        model_arg = self._normalize_run_model_v1(model)
        if model_arg:
            cmd.extend(["--model", model_arg])
        for image_path in images:
            cmd.extend(["--file", str(image_path)])
        # `--file` is an array option; delimiter avoids prompt being parsed as another file.
        cmd.append("--")
        cmd.append(prompt)

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        payloads = self._load_json_payloads_with_ansi_cleanup(output)
        session_id = self._extract_session_id_from_stream(payloads)
        export_payload = self._export_v1_session_payload(session_id, env=env, base_env=base_env)

        usage = self._extract_v1_usage(export_payload)
        model_name = self._extract_v1_model_name(export_payload)

        output_path = self._write_output(self.name, output)
        trajectory_payload: Dict[str, Any] = {
            "stream_json": payloads,
        }
        if export_payload is not None:
            trajectory_payload["session_export"] = export_payload
        trajectory_content = format_trace_text(
            json.dumps(trajectory_payload, ensure_ascii=True),
            source=str(output_path),
        )
        trajectory_path = self._write_trajectory(self.name, trajectory_content)
        return RunResult(
            agent=self.name,
            agent_version=agent_version,
            runtime_seconds=result.duration_seconds,
            models_usage=self._ensure_models_usage({}, usage, model_name) if usage is not None and model_name else {},
            tool_calls=self._extract_v1_tool_calls(export_payload),
            llm_calls=self._extract_v1_llm_calls(export_payload),
            total_cost=self._extract_v1_total_cost(export_payload),
            response=self._extract_v1_response(payloads, export_payload, output),
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        return self._version_first_line(["kilocode", "--version"])

    def _build_runtime_config_payload(self, model_override: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        api_key = self._normalize_text(os.environ.get("KILO_OPENAI_API_KEY"))

        base_url = self._normalize_text(os.environ.get("KILO_OPENAI_BASE_URL"))

        model = self._normalize_model_id(model_override)
        if model is None:
            model = self._normalize_model_id(os.environ.get("KILO_OPENAI_MODEL_ID"))

        missing: List[str] = []
        if api_key is None:
            missing.append("KILO_OPENAI_API_KEY")
        if model is None:
            missing.append("KILO_OPENAI_MODEL_ID")
        if missing:
            return None, self._missing_env_message(missing)

        provider: Dict[str, Any] = {
            "id": "default",
            "provider": "openai",
            "openAiApiKey": api_key,
            "openAiModelId": model,
            "openAiLegacyFormat": False,
            "openAiUseAzure": False,
        }
        if base_url:
            provider["openAiBaseUrl"] = base_url

        payload: Dict[str, Any] = {
            "version": "1.0.0",
            "mode": "code",
            "telemetry": False,
            "provider": "default",
            "providers": [provider],
            "autoApproval": {
                "enabled": True,
                "read": {"enabled": True, "outside": False},
                "write": {"enabled": True, "outside": True, "protected": False},
                "browser": {"enabled": False},
                "retry": {"enabled": False, "delay": 10},
                "mcp": {"enabled": True},
                "mode": {"enabled": True},
                "subtasks": {"enabled": True},
                "execute": {
                    "enabled": True,
                    "allowed": ["ls", "cat", "echo", "pwd"],
                    "denied": ["rm -rf", "sudo rm", "mkfs", "dd if="],
                },
                "question": {"enabled": False, "timeout": 60},
                "todo": {"enabled": True},
            },
            "theme": "dark",
            "customThemes": {},
        }
        return payload, None

    @staticmethod
    def _parse_major_version(version: Optional[str]) -> Optional[int]:
        if not isinstance(version, str):
            return None
        value = version.strip()
        if not value:
            return None
        match = re.match(r"^(\d+)", value)
        if not match:
            return None
        return int(match.group(1))

    def _normalize_model_id(self, value: Optional[str]) -> Optional[str]:
        cleaned = self._normalize_text(value)
        if cleaned is None:
            return None
        if "/" in cleaned:
            parts = cleaned.split("/", 1)
            if len(parts) == 2 and parts[1].strip():
                return parts[1].strip()
        return cleaned

    def _load_json_payloads_with_ansi_cleanup(self, output: str) -> List[Dict[str, Any]]:
        stdout = self._stdout_only(output)
        cleaned = _ANSI_OSC_RE.sub("", stdout)
        cleaned = _ANSI_CSI_RE.sub("", cleaned)
        cleaned = cleaned.replace("\r", "")
        payloads: List[Dict[str, Any]] = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if "{" in line and not line.startswith("{"):
                line = line[line.find("{") :]
            if not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
            except Exception:
                continue
            if isinstance(parsed, dict):
                payloads.append(parsed)
        return payloads

    def _load_global_state(self, run_home: Path) -> Optional[Dict[str, Any]]:
        path = run_home / ".kilocode" / "cli" / "global" / "global-state.json"
        if not path.exists():
            return None
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if isinstance(parsed, dict):
            return parsed
        return None

    def _extract_task_history_item(self, global_state: Optional[Dict[str, Any]], prompt: str) -> Optional[Dict[str, Any]]:
        if not isinstance(global_state, dict):
            return None
        history = global_state.get("taskHistory")
        if not isinstance(history, list):
            return None
        candidates: List[Dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict):
                return None
            if item.get("workspace") != str(self.workdir):
                continue
            if item.get("task") == prompt:
                candidates.append(item)
        if len(candidates) == 1:
            return candidates[0]
        workspace_only = [item for item in history if isinstance(item, dict) and item.get("workspace") == str(self.workdir)]
        if len(workspace_only) == 1:
            return workspace_only[0]
        return None

    @staticmethod
    def _extract_task_id(task_item: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(task_item, dict):
            return None
        task_id = task_item.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            return None
        return task_id.strip()

    def _load_json_array(self, path: Path) -> Optional[List[Dict[str, Any]]]:
        if not path.exists():
            return None
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(parsed, list):
            return None
        items: List[Dict[str, Any]] = []
        for item in parsed:
            if not isinstance(item, dict):
                return None
            items.append(item)
        return items

    def _extract_usage_from_ui_messages(self, ui_messages: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, int]]:
        if not isinstance(ui_messages, list):
            return None
        prompt_tokens = 0
        completion_tokens = 0
        found = False
        for message in ui_messages:
            if message.get("type") != "say" or message.get("say") != "api_req_started":
                continue
            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                return None
            try:
                usage_payload = json.loads(text)
            except Exception:
                return None
            if not isinstance(usage_payload, dict):
                return None
            tokens_in = self._as_int(usage_payload.get("tokensIn"))
            tokens_out = self._as_int(usage_payload.get("tokensOut"))
            if tokens_in is None or tokens_out is None:
                return None
            prompt_tokens += tokens_in
            completion_tokens += tokens_out
            found = True
        if not found:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def _extract_llm_calls_from_ui_messages(self, ui_messages: Optional[List[Dict[str, Any]]]) -> Optional[int]:
        if not isinstance(ui_messages, list):
            return None
        llm_calls = 0
        for message in ui_messages:
            if message.get("type") != "say" or message.get("say") != "api_req_started":
                continue
            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                return None
            try:
                usage_payload = json.loads(text)
            except Exception:
                return None
            if not isinstance(usage_payload, dict):
                return None
            if self._as_int(usage_payload.get("tokensIn")) is None:
                return None
            if self._as_int(usage_payload.get("tokensOut")) is None:
                return None
            llm_calls += 1
        if llm_calls < 1:
            return None
        return llm_calls

    def _extract_tool_calls_from_api_history(self, api_history: Optional[List[Dict[str, Any]]]) -> Optional[int]:
        if not isinstance(api_history, list):
            return None
        total = 0
        for message in api_history:
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                return None
            for part in content:
                if not isinstance(part, dict):
                    return None
                if part.get("type") == "tool_use":
                    total += 1
        return total

    def _extract_model_name(
        self,
        task_item: Optional[Dict[str, Any]],
        global_state: Optional[Dict[str, Any]],
        api_history: Optional[List[Dict[str, Any]]],
    ) -> Optional[str]:
        if isinstance(task_item, dict) and isinstance(global_state, dict):
            config_name = task_item.get("apiConfigName")
            if isinstance(config_name, str) and config_name:
                configs = global_state.get("listApiConfigMeta")
                if isinstance(configs, list):
                    for entry in configs:
                        if not isinstance(entry, dict):
                            return None
                        if entry.get("name") != config_name:
                            continue
                        model_id = entry.get("modelId")
                        if isinstance(model_id, str) and model_id.strip():
                            return model_id.strip()

        if not isinstance(api_history, list):
            return None
        for message in api_history:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                return None
            for part in content:
                if not isinstance(part, dict):
                    return None
                text = part.get("text")
                if not isinstance(text, str):
                    continue
                match = _MODEL_TAG_RE.search(text)
                if match:
                    candidate = match.group(1).strip()
                    if candidate:
                        return candidate
        return None

    def _extract_response(
        self,
        payloads: List[Dict[str, Any]],
        ui_messages: Optional[List[Dict[str, Any]]],
        api_history: Optional[List[Dict[str, Any]]],
        output: str,
    ) -> Optional[str]:
        if isinstance(ui_messages, list):
            for message in reversed(ui_messages):
                if message.get("type") != "say":
                    continue
                if message.get("say") not in {"completion_result", "text"}:
                    continue
                if message.get("partial") is True:
                    continue
                text = message.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()

        if isinstance(api_history, list):
            for message in reversed(api_history):
                if message.get("role") != "assistant":
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for part in reversed(content):
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") != "text":
                        continue
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()

        for payload in reversed(payloads):
            content = payload.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()

        stdout = self._stdout_only(output)
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if lines:
            return lines[-1]
        return None

    def _extract_total_cost(self, task_item: Optional[Dict[str, Any]]) -> Optional[float]:
        if not isinstance(task_item, dict):
            return None
        value = task_item.get("totalCost")
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _build_trajectory_payload(
        self,
        *,
        task_item: Optional[Dict[str, Any]],
        ui_messages: Optional[List[Dict[str, Any]]],
        api_history: Optional[List[Dict[str, Any]]],
        stream_payloads: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        if isinstance(task_item, dict):
            payload["task_history"] = task_item
        if isinstance(ui_messages, list):
            payload["ui_messages"] = ui_messages
        if isinstance(api_history, list):
            payload["api_conversation_history"] = api_history
        if stream_payloads:
            payload["stream_json"] = stream_payloads
        if not payload:
            return None
        return payload

    def _normalize_run_model_v1(self, model: Optional[str]) -> Optional[str]:
        normalized = self._normalize_text(model)
        if normalized is None:
            return None
        if "/" in normalized:
            return normalized
        return f"openai/{normalized}"

    def _extract_session_id_from_stream(self, payloads: List[Dict[str, Any]]) -> Optional[str]:
        for payload in payloads:
            session_id = payload.get("sessionID")
            if isinstance(session_id, str) and session_id.strip():
                return session_id.strip()
        return None

    def _export_v1_session_payload(
        self,
        session_id: Optional[str],
        *,
        env: Dict[str, str],
        base_env: Optional[Dict[str, str]],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        result = self._run(["kilocode", "export", session_id.strip()], env=env, base_env=base_env)
        if result.exit_code != 0:
            return None
        stdout = self._stdout_only(result.output)
        parsed = self._extract_last_json_value(stdout)
        if not isinstance(parsed, dict):
            return None
        return parsed

    def _extract_v1_messages(self, export_payload: Optional[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(export_payload, dict):
            return None
        messages = export_payload.get("messages")
        if not isinstance(messages, list):
            return None
        items: List[Dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict):
                return None
            items.append(item)
        return items

    def _extract_v1_usage(self, export_payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        messages = self._extract_v1_messages(export_payload)
        if not isinstance(messages, list):
            return None
        prompt_tokens = 0
        completion_tokens = 0
        found = False
        for message in messages:
            info = message.get("info")
            if not isinstance(info, dict):
                return None
            if info.get("role") != "assistant":
                continue
            if info.get("summary") is True:
                continue
            tokens = info.get("tokens")
            if not isinstance(tokens, dict):
                return None
            input_tokens = self._as_int(tokens.get("input"))
            output_tokens = self._as_int(tokens.get("output"))
            if input_tokens is None or output_tokens is None:
                return None
            prompt_tokens += input_tokens
            completion_tokens += output_tokens
            found = True
        if not found:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def _extract_v1_llm_calls(self, export_payload: Optional[Dict[str, Any]]) -> Optional[int]:
        messages = self._extract_v1_messages(export_payload)
        if not isinstance(messages, list):
            return None
        count = 0
        for message in messages:
            info = message.get("info")
            if not isinstance(info, dict):
                return None
            if info.get("role") != "assistant":
                continue
            if info.get("summary") is True:
                continue
            tokens = info.get("tokens")
            if not isinstance(tokens, dict):
                return None
            if self._as_int(tokens.get("input")) is None:
                return None
            if self._as_int(tokens.get("output")) is None:
                return None
            count += 1
        if count < 1:
            return None
        return count

    def _extract_v1_tool_calls(self, export_payload: Optional[Dict[str, Any]]) -> Optional[int]:
        messages = self._extract_v1_messages(export_payload)
        if not isinstance(messages, list):
            return None
        total = 0
        for message in messages:
            info = message.get("info")
            if not isinstance(info, dict):
                return None
            if info.get("role") != "assistant":
                continue
            parts = message.get("parts")
            if not isinstance(parts, list):
                return None
            for part in parts:
                if not isinstance(part, dict):
                    return None
                if part.get("type") != "tool":
                    continue
                state = part.get("state")
                if not isinstance(state, dict):
                    return None
                status = state.get("status")
                if status not in {"completed", "error"}:
                    continue
                total += 1
        return total

    def _extract_v1_model_name(self, export_payload: Optional[Dict[str, Any]]) -> Optional[str]:
        messages = self._extract_v1_messages(export_payload)
        if not isinstance(messages, list):
            return None
        for message in reversed(messages):
            info = message.get("info")
            if not isinstance(info, dict):
                return None
            if info.get("role") != "assistant":
                continue
            if info.get("summary") is True:
                continue
            provider_id = info.get("providerID")
            model_id = info.get("modelID")
            if isinstance(provider_id, str) and provider_id.strip() and isinstance(model_id, str) and model_id.strip():
                return f"{provider_id.strip()}/{model_id.strip()}"
            if isinstance(model_id, str) and model_id.strip():
                return model_id.strip()
            return None
        return None

    def _extract_v1_response(
        self,
        payloads: List[Dict[str, Any]],
        export_payload: Optional[Dict[str, Any]],
        output: str,
    ) -> Optional[str]:
        for payload in reversed(payloads):
            if payload.get("type") != "text":
                continue
            part = payload.get("part")
            if not isinstance(part, dict):
                return None
            if part.get("type") != "text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()

        messages = self._extract_v1_messages(export_payload)
        if isinstance(messages, list):
            for message in reversed(messages):
                info = message.get("info")
                if not isinstance(info, dict):
                    return None
                if info.get("role") != "assistant":
                    continue
                parts = message.get("parts")
                if not isinstance(parts, list):
                    return None
                for part in reversed(parts):
                    if not isinstance(part, dict):
                        return None
                    if part.get("type") != "text":
                        continue
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()

        for payload in reversed(payloads):
            if payload.get("type") != "error":
                continue
            error = payload.get("error")
            if not isinstance(error, dict):
                return None
            data = error.get("data")
            if not isinstance(data, dict):
                return None
            message = data.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()

        stdout = self._stdout_only(output)
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if lines:
            return lines[-1]
        return None

    def _extract_v1_total_cost(self, export_payload: Optional[Dict[str, Any]]) -> Optional[float]:
        messages = self._extract_v1_messages(export_payload)
        if not isinstance(messages, list):
            return None
        total = 0.0
        found = False
        for message in messages:
            info = message.get("info")
            if not isinstance(info, dict):
                return None
            if info.get("role") != "assistant":
                continue
            value = info.get("cost")
            if value is None:
                continue
            if isinstance(value, bool):
                return None
            if not isinstance(value, (int, float)):
                return None
            total += float(value)
            found = True
        if not found:
            return None
        return total
