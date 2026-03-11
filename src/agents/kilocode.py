from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodingAgent, InstallStrategy
from ..stats_extract import (
    StatsArtifacts,
    StatsSnapshot,
    build_single_model_stats_snapshot,
    extract_opencode_session_export_stats,
    last_value,
    merge_stats_snapshots,
    opt_float,
    parse_usage_by_model,
    req_str,
    select_values,
    sum_int,
)
from ..models import RunResult
from ..agent_runtime import env as runtime_env
from ..agent_runtime import parsing as runtime_parsing
from ..agent_runtime import trajectory as runtime_trajectory


class KiloCodeAgent(CodingAgent):
    name = "kilocode"
    display_name = "Kilo Code"
    binary = "kilocode"
    supports_images = True
    supports_videos = False
    install_strategy = InstallStrategy(kind="npm", package="@kilocode/cli")
    _ANSI_OSC_RE = re.compile(r"\x1b\][^\x07]*\x07")
    _ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
    _MODEL_TAG_RE = re.compile(r"<model>([^<]+)</model>")

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
        major: Optional[int] = None
        if isinstance(agent_version, str):
            value = agent_version.strip()
            if value:
                match = re.match(r"^(\d+)", value)
                if match:
                    major = int(match.group(1))
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
        images = images or []
        config_payload, config_error = self._build_runtime_config_payload(model_override=model_override)
        if config_payload is None:
            message = config_error or "missing required Kilo Code API settings"
            return self._build_error_run_result(
                message=message,
                cakit_exit_code=1,
                agent_version=agent_version,
            )

        run_home = self._make_temp_dir(prefix="cakit-kilocode-home-")
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

        global_state_path = run_home / ".kilocode" / "cli" / "global" / "global-state.json"
        loaded_global_state = runtime_parsing.load_json(global_state_path)
        global_state = loaded_global_state if isinstance(loaded_global_state, dict) else None

        task_item: Optional[Dict[str, Any]] = None
        if isinstance(global_state, dict):
            history = last_value(global_state, "$.taskHistory")
            if isinstance(history, list) and not any(not isinstance(item, dict) for item in history):
                workspace = str(self.workdir)
                workspace_only = [item for item in history if req_str(item, "$.workspace") == workspace]
                candidates = [item for item in workspace_only if req_str(item, "$.task") == prompt]
                if len(candidates) == 1:
                    task_item = candidates[0]
                elif len(workspace_only) == 1:
                    task_item = workspace_only[0]

        task_id = req_str(task_item, "$.id")
        task_dir = run_home / ".kilocode" / "cli" / "global" / "tasks" / task_id if task_id else None
        ui_messages = self._load_json_array(task_dir / "ui_messages.json") if task_dir else None
        api_history = self._load_json_array(task_dir / "api_conversation_history.json") if task_dir else None

        snapshot = self._extract_v0_stats_snapshot(
            task_item=task_item,
            global_state=global_state,
            ui_messages=ui_messages,
            api_history=api_history,
        )

        trajectory_payload: Dict[str, Any] = {}
        if isinstance(task_item, dict):
            trajectory_payload["task_history"] = task_item
        if isinstance(ui_messages, list):
            trajectory_payload["ui_messages"] = ui_messages
        if isinstance(api_history, list):
            trajectory_payload["api_conversation_history"] = api_history
        if payloads:
            trajectory_payload["stream_json"] = payloads

        if trajectory_payload:
            trajectory_source = str(task_dir) if task_dir else str(run_home)
            trajectory_content = runtime_trajectory.build_trajectory_from_raw(
                raw_text=json.dumps(trajectory_payload, ensure_ascii=True),
                output=output,
                source=trajectory_source,
            )
        else:
            trajectory_content = runtime_trajectory.build_trajectory_content(output=output, source=str(run_home))
        return self.finalize_run(
            command_result=result,
            response=self._extract_v0_response(payloads, ui_messages, api_history, output),
            models_usage=snapshot.models_usage if snapshot is not None else {},
            llm_calls=snapshot.llm_calls if snapshot is not None else None,
            tool_calls=snapshot.tool_calls if snapshot is not None else None,
            total_cost=snapshot.total_cost if snapshot is not None else None,
            agent_version=agent_version,
            trajectory_content=trajectory_content,
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
        images = images or []
        runtime_settings, runtime_error = self._resolve_runtime_provider_settings(model_override=model_override)
        if runtime_settings is None:
            message = runtime_error or "missing required Kilo Code API settings"
            return self._build_error_run_result(
                message=message,
                cakit_exit_code=1,
                agent_version=agent_version,
            )
        api_key = str(runtime_settings["api_key"])
        model = str(runtime_settings["model"])
        base_url = runtime_settings["base_url"]

        run_home = self._make_temp_dir(prefix="cakit-kilocode-home-")
        env = {
            "HOME": str(run_home),
            "OPENAI_API_KEY": api_key,
            "KILO_DISABLE_AUTOUPDATE": "true",
            "KILO_TELEMETRY": "false",
        }
        if base_url:
            env["OPENAI_BASE_URL"] = base_url

        cmd = ["kilocode", "run", "--auto", "--format", "json"]
        normalized_run_model = runtime_parsing.normalize_text(model)
        model_arg = (
            runtime_env.normalize_provider_model(
                normalized_run_model,
                default_provider="openai",
                colon_as_provider=False,
            )
            if normalized_run_model is not None
            else None
        )
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
        session_id = runtime_parsing.normalize_text(last_value(payloads, "$[*].sessionID"))
        export_payload: Optional[Dict[str, Any]] = None
        if isinstance(session_id, str) and session_id.strip():
            export_result = self._run(["kilocode", "export", session_id.strip()], env=env, base_env=base_env)
            if export_result.exit_code == 0:
                export_payload = runtime_parsing.parse_output_json_object(export_result.output)

        artifacts = StatsArtifacts(
            raw_output=output,
            session_payload=export_payload,
        )
        snapshot = merge_stats_snapshots(
            snapshots=[extract_opencode_session_export_stats(artifacts)]
        )

        trajectory_payload: Dict[str, Any] = {
            "stream_json": payloads,
        }
        if export_payload is not None:
            trajectory_payload["session_export"] = export_payload
        trajectory_content = runtime_trajectory.build_trajectory_from_raw(
            raw_text=json.dumps(trajectory_payload, ensure_ascii=True),
            output=output,
            source=str(run_home),
        )
        response = (
            runtime_parsing.last_nonempty_text(select_values(payloads, '$[?(@.type == "text")].part.text'))
            or runtime_parsing.last_nonempty_text(
                select_values(
                    export_payload,
                    '$.messages[?(@.info.role == "assistant")].parts[?(@.type == "text")].text',
                )
            )
            or runtime_parsing.last_nonempty_text(
                select_values(payloads, '$[?(@.type == "error")].error.data.message')
            )
            or runtime_parsing.last_stdout_line(output)
        )
        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=snapshot.models_usage,
            llm_calls=snapshot.llm_calls,
            tool_calls=snapshot.tool_calls,
            total_cost=snapshot.total_cost,
            agent_version=agent_version,
            trajectory_content=trajectory_content,
        )

    def _resolve_runtime_provider_settings(
        self,
        *,
        model_override: Optional[str],
    ) -> Tuple[Optional[Dict[str, Optional[str]]], Optional[str]]:
        api_key = runtime_env.resolve_openai_api_key("KILO_OPENAI_API_KEY")
        base_url = runtime_env.resolve_openai_base_url("KILO_OPENAI_BASE_URL")
        model = runtime_env.extract_model_id(
            runtime_env.resolve_openai_model("KILO_OPENAI_MODEL_ID", model_override=model_override),
            colon_as_provider=False,
        )

        missing: List[tuple[str, str]] = []
        if api_key is None:
            missing.append(("KILO_OPENAI_API_KEY", "OPENAI_API_KEY"))
        if model is None:
            missing.append(("KILO_OPENAI_MODEL_ID", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return None, runtime_env.missing_env_with_fallback_message(missing)
        return {"api_key": api_key, "base_url": base_url, "model": model}, None

    def _build_runtime_config_payload(self, model_override: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        runtime_settings, runtime_error = self._resolve_runtime_provider_settings(model_override=model_override)
        if runtime_settings is None:
            return None, runtime_error
        api_key = str(runtime_settings["api_key"])
        base_url = runtime_settings["base_url"]
        model = str(runtime_settings["model"])

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

    def _load_json_payloads_with_ansi_cleanup(self, output: str) -> List[Dict[str, Any]]:
        stdout = runtime_parsing.stdout_only(output)
        cleaned = self._ANSI_OSC_RE.sub("", stdout)
        cleaned = self._ANSI_CSI_RE.sub("", cleaned)
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
            parsed = runtime_parsing.parse_json(line)
            if isinstance(parsed, dict):
                payloads.append(parsed)
        return payloads

    def _load_json_array(self, path: Path) -> Optional[List[Dict[str, Any]]]:
        parsed = runtime_parsing.load_json(path)
        if not isinstance(parsed, list):
            return None
        if any(not isinstance(item, dict) for item in parsed):
            return None
        return [item for item in parsed if isinstance(item, dict)]

    def _extract_v0_stats_snapshot(
        self,
        *,
        task_item: Optional[Dict[str, Any]],
        global_state: Optional[Dict[str, Any]],
        ui_messages: Optional[List[Dict[str, Any]]],
        api_history: Optional[List[Dict[str, Any]]],
    ) -> Optional[StatsSnapshot]:
        model_name: Optional[str] = None
        config_name = req_str(task_item, "$.apiConfigName")
        if config_name is not None and isinstance(global_state, dict):
            filter_path = f"$.listApiConfigMeta[?(@.name == {json.dumps(config_name, ensure_ascii=True)})].modelId"
            model_name = runtime_parsing.normalize_text(last_value(global_state, filter_path))
        if model_name is None:
            for text in reversed(select_values(api_history, '$[?(@.role == "user")].content[*].text') or []):
                if not isinstance(text, str):
                    continue
                match = self._MODEL_TAG_RE.search(text)
                if match and match.group(1).strip():
                    model_name = match.group(1).strip()
                    break

        api_req_started_messages = [
            message
            for message in (select_values(ui_messages, '$[?(@.type == "say")][?(@.say == "api_req_started")]') or [])
            if isinstance(message, dict)
        ]
        usage_entries: List[Dict[str, int]] = []
        for message in api_req_started_messages:
            text = runtime_parsing.normalize_text(last_value(message, "$.text"))
            if text is None:
                continue
            usage_payload = runtime_parsing.parse_json(text)
            if not isinstance(usage_payload, dict):
                continue
            usage = parse_usage_by_model(usage_payload, "tokens_in_out")
            if usage is not None:
                usage_entries.append(usage)

        prompt_tokens = sum_int(usage_entries, "$[*].prompt_tokens")
        completion_tokens = sum_int(usage_entries, "$[*].completion_tokens")
        usage = (
            None
            if prompt_tokens is None or completion_tokens is None
            else {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
        )
        llm_calls = len(api_req_started_messages) if api_req_started_messages else None
        tool_call_values = select_values(api_history, '$[?(@.role == "assistant")].content[?(@.type == "tool_use")]')
        tool_calls = len(tool_call_values) if tool_call_values is not None else None
        return build_single_model_stats_snapshot(
            model_name=model_name,
            usage=usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=opt_float(task_item, "$.totalCost") if isinstance(task_item, dict) else None,
        )

    def _extract_v0_response(
        self,
        payloads: List[Dict[str, Any]],
        ui_messages: Optional[List[Dict[str, Any]]],
        api_history: Optional[List[Dict[str, Any]]],
        output: str,
    ) -> Optional[str]:
        for message in reversed(select_values(ui_messages, '$[?(@.type == "say")]') or []):
            if not isinstance(message, dict):
                continue
            if last_value(message, "$.partial") is True:
                continue
            if req_str(message, "$.say") not in {"completion_result", "text"}:
                continue
            text = runtime_parsing.normalize_text(last_value(message, "$.text"))
            if text is not None:
                return text
        return (
            runtime_parsing.last_nonempty_text(
                select_values(api_history, '$[?(@.role == "assistant")].content[?(@.type == "text")].text')
            )
            or runtime_parsing.last_nonempty_text(select_values(payloads, "$[*].content"))
            or runtime_parsing.last_stdout_line(output)
        )
