from __future__ import annotations
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import (
    CodingAgent,
    InstallStrategy,
)
from ..io_helpers import dump_toml
from ..models import RunResult
from ..agent_runtime import parsing as runtime_parsing
from ..agent_runtime import env as runtime_env
from ..agent_runtime import trajectory as runtime_trajectory
from ..stats_extract import (
    last_value,
    merge_model_usage,
    merge_stats_snapshots,
    normalize_stats_snapshot,
    select_values,
    sum_int,
)


class CodexAgent(CodingAgent):
    name = "codex"
    display_name = "OpenAI Codex"
    binary = "codex"
    supports_images = True
    supports_videos = False
    install_strategy = InstallStrategy(
        kind="npm",
        package="@openai/codex",
        require_config=True,
        configure_failure_message="codex configure failed",
    )

    def configure(self) -> Optional[str]:
        use_oauth, api_key, base_url, model = self._resolve_runtime_auth(model_override=None)
        config: Dict[str, Any] = {"project_root_markers": []}
        if model:
            config["model"] = model
        if not use_oauth and api_key:
            provider_config: Dict[str, str] = {
                "name": "custom",
                "env_key": "CODEX_API_KEY",
                "wire_api": "responses",
            }
            if base_url:
                provider_config["base_url"] = base_url
            config["model_provider"] = "custom"
            config["model_providers"] = {"custom": provider_config}
        otel_exporter, otel_endpoint, otel_protocol, otel_env, otel_log_prompt = self._resolve_otel_settings()
        otel_config = self._build_otel_config(
            exporter_name=otel_exporter,
            endpoint=otel_endpoint,
            protocol=otel_protocol,
            environment=otel_env,
            log_user_prompt=otel_log_prompt,
        )
        if otel_config is not None:
            config["otel"] = otel_config
        config_path = self._config_path()
        self._write_text(config_path, dump_toml(config))
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
        images = images or []
        if self._use_oauth() and not self._auth_path().exists():
            message = f"codex OAuth is enabled but auth file not found at {self._auth_path()}; run `codex login`."
            return self._build_error_run_result(message=message, cakit_exit_code=2)
        use_oauth, api_key, api_base, model = self._resolve_runtime_auth(model_override=model_override)
        env = {
            "OPENAI_BASE_URL": api_base,
        }
        if api_key:
            env["CODEX_API_KEY"] = api_key
            env["OPENAI_API_KEY"] = api_key
        output_root = os.environ.get("CAKIT_OUTPUT_DIR")
        if output_root:
            output_dir = Path(output_root)
        else:
            output_dir = Path.home() / ".cache" / "cakit"
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        unique = uuid.uuid4().hex
        last_message_path = output_dir / f"{self.name}-{stamp}-{unique}-last-message.txt"
        cmd = [
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--output-last-message",
            str(last_message_path),
        ]
        if model:
            cmd.extend(["--model", model])
        if reasoning_effort:
            cmd.extend(["-c", f"model_reasoning_effort={reasoning_effort}"])
        if images:
            for image_path in images:
                cmd.extend(["--image", str(image_path)])
        input_text: Optional[str]
        if images:
            cmd.extend(["--", prompt])
            input_text = None
        else:
            cmd.append("-")
            input_text = prompt
        unset_env = None
        if use_oauth or (not api_key):
            unset_env = ["OPENAI_API_KEY", "CODEX_API_KEY"]
        result = self._run(cmd, env, input_text=input_text, unset_env=unset_env, base_env=base_env)
        output = result.output
        payloads = runtime_parsing.load_output_json_payloads(output, stdout_only_output=False)
        rollout_family = self._resolve_rollout_family_paths(payloads)
        rollout_stats = self._extract_rollout_stats(payloads)
        if rollout_stats is None:
            usage, llm_calls = self._extract_turn_completed_metrics(payloads)
            model_name = self._extract_model_name(payloads)
            models_usage: Dict[str, Dict[str, int]] = {}
            if model_name is not None and usage is not None:
                models_usage[model_name] = usage
            tool_calls = self._extract_stream_tool_calls(payloads)
        else:
            models_usage, llm_calls, tool_calls = rollout_stats
        extracted_stats = normalize_stats_snapshot(
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
        )
        stats = merge_stats_snapshots([extracted_stats])
        message_text = self._read_text(last_message_path)
        response = message_text.strip() if isinstance(message_text, str) and message_text.strip() else None
        trajectory_content = self._build_family_trajectory_content(output, rollout_family)
        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=stats.models_usage,
            llm_calls=stats.llm_calls,
            tool_calls=stats.tool_calls,
            total_cost=stats.total_cost,
            telemetry_log=self._resolve_otel_settings()[1],
            trajectory_content=trajectory_content,
            trajectory_source=str(self._sessions_root()),
        )

    def _resolve_runtime_auth(
        self,
        *,
        model_override: Optional[str],
    ) -> tuple[bool, Optional[str], Optional[str], Optional[str]]:
        use_oauth = self._use_oauth()
        api_key = None if use_oauth else runtime_env.resolve_openai_api_key("CODEX_API_KEY")
        api_base = runtime_env.resolve_openai_base_url("CODEX_BASE_URL")
        model = runtime_env.resolve_openai_model("CODEX_MODEL", model_override=model_override)
        return use_oauth, api_key, api_base, model

    def _extract_rollout_stats(
        self,
        payloads: List[Dict[str, Any]],
    ) -> Optional[tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int]]]:
        rollout_family = self._resolve_rollout_family_paths(payloads)
        if not rollout_family:
            return None

        models_usage: Dict[str, Dict[str, int]] = {}
        total_llm_calls = 0
        has_llm_calls = False
        total_tool_calls = 0
        has_tool_calls = False
        for thread_id, rollout_path in rollout_family:
            records = self._load_rollout_records(rollout_path)
            if records is None:
                return None
            model_name = self._extract_rollout_model_name(records)
            usage, llm_calls = self._extract_rollout_thread_metrics(records)
            if model_name is not None and usage is not None:
                merge_model_usage(models_usage, model_name, usage)
            if llm_calls is not None:
                total_llm_calls += llm_calls
                has_llm_calls = True
            tool_calls = self._extract_rollout_tool_calls(records)
            if tool_calls is not None:
                total_tool_calls += tool_calls
                has_tool_calls = True
        return (
            models_usage,
            total_llm_calls if has_llm_calls else None,
            total_tool_calls if has_tool_calls else None,
        )

    def _resolve_rollout_family_paths(
        self,
        payloads: List[Dict[str, Any]],
    ) -> Optional[list[tuple[str, Path]]]:
        main_thread_id = last_value(payloads, '$[?(@.type=="thread.started")].thread_id')
        if not isinstance(main_thread_id, str):
            return None
        normalized_main_thread_id = main_thread_id.strip()
        if not normalized_main_thread_id:
            return None
        sessions_root = self._sessions_root()
        if not sessions_root.exists():
            return None

        rollout_index: Dict[str, Path] = {}
        children_by_parent: Dict[str, list[str]] = {}
        for _ in range(10):
            rollout_index, children_by_parent = self._collect_rollout_index(sessions_root)
            if normalized_main_thread_id in rollout_index:
                break
            time.sleep(0.1)
        if normalized_main_thread_id not in rollout_index:
            return None
        family_thread_ids = self._collect_thread_family(
            normalized_main_thread_id,
            rollout_index=rollout_index,
            children_by_parent=children_by_parent,
        )
        if not family_thread_ids:
            return None
        return [
            (thread_id, rollout_index[thread_id])
            for thread_id in family_thread_ids
            if thread_id in rollout_index
        ]

    def _build_family_trajectory_content(
        self,
        output: str,
        rollout_family: Optional[list[tuple[str, Path]]],
    ) -> Optional[str]:
        if not rollout_family:
            return None
        sections: list[tuple[str, str, Optional[str]]] = [("stdout", output, None)]
        for thread_id, rollout_path in rollout_family:
            rollout_text = self._read_text(rollout_path)
            if not rollout_text:
                continue
            sections.append((f"rollout:{thread_id}", rollout_text, str(rollout_path)))
        content = runtime_trajectory.build_family_trajectory_content(
            source=str(self._sessions_root()),
            sections=sections,
        )
        return content or None

    def _sessions_root(self) -> Path:
        codex_home = os.environ.get("CODEX_HOME")
        root = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
        return root / "sessions"

    def _collect_rollout_index(
        self,
        sessions_root: Path,
    ) -> tuple[Dict[str, Path], Dict[str, list[str]]]:
        rollout_index: Dict[str, Path] = {}
        children_by_parent: Dict[str, list[str]] = {}
        for rollout_path in sessions_root.rglob("rollout-*.jsonl"):
            records = self._load_rollout_records(rollout_path, max_lines=1)
            if not records:
                continue
            session_meta = records[0]
            thread_id = runtime_parsing.normalize_text(last_value(session_meta, "$.payload.id"))
            if thread_id is None:
                continue
            rollout_index[thread_id] = rollout_path
            parent_thread_id = runtime_parsing.normalize_text(
                last_value(session_meta, "$.payload.source.subagent.thread_spawn.parent_thread_id")
            )
            if parent_thread_id is not None:
                children = children_by_parent.setdefault(parent_thread_id, [])
                if thread_id not in children:
                    children.append(thread_id)
        return rollout_index, children_by_parent

    def _collect_thread_family(
        self,
        main_thread_id: str,
        *,
        rollout_index: Dict[str, Path],
        children_by_parent: Dict[str, list[str]],
    ) -> list[str]:
        thread_ids: list[str] = []
        pending = [main_thread_id]
        seen: set[str] = set()
        while pending:
            thread_id = pending.pop()
            if thread_id in seen:
                continue
            seen.add(thread_id)
            if thread_id in rollout_index:
                thread_ids.append(thread_id)
            pending.extend(children_by_parent.get(thread_id, []))
        return thread_ids

    def _load_rollout_records(
        self,
        rollout_path: Path,
        *,
        max_lines: Optional[int] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        records_text = self._read_text(rollout_path)
        if records_text is None:
            return None
        if max_lines is not None:
            records_text = "\n".join(records_text.splitlines()[:max_lines])
        return runtime_parsing.load_output_json_payloads(records_text, stdout_only_output=False)

    def _extract_rollout_model_name(self, records: List[Dict[str, Any]]) -> Optional[str]:
        model_name = last_value(records, '$[?(@.type=="turn_context")].payload.model')
        if isinstance(model_name, str):
            normalized = model_name.strip()
            if normalized:
                return normalized
        return None

    def _extract_rollout_thread_metrics(
        self,
        records: List[Dict[str, Any]],
    ) -> tuple[Optional[Dict[str, int]], Optional[int]]:
        raw_usages = select_values(
            records,
            '$[?(@.type=="event_msg" && @.payload.type=="token_count" && @.payload.info != null)].payload.info.total_token_usage',
        )
        if raw_usages is None:
            return None, None
        final_usage: Optional[Dict[str, int]] = None
        llm_calls = 0
        previous_snapshot: Optional[tuple[int, int, int]] = None
        for raw_usage in raw_usages:
            if not isinstance(raw_usage, dict):
                continue
            input_tokens = runtime_parsing.as_int(last_value(raw_usage, "$.input_tokens"))
            cached_input_tokens = runtime_parsing.as_int(last_value(raw_usage, "$.cached_input_tokens"))
            output_tokens = runtime_parsing.as_int(last_value(raw_usage, "$.output_tokens"))
            total_tokens = runtime_parsing.as_int(last_value(raw_usage, "$.total_tokens"))
            if input_tokens is None or cached_input_tokens is None or output_tokens is None:
                continue
            snapshot = (input_tokens, cached_input_tokens, output_tokens)
            if snapshot != previous_snapshot:
                llm_calls += 1
                previous_snapshot = snapshot
            final_usage = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": total_tokens if total_tokens is not None else input_tokens + output_tokens,
            }
        if final_usage is None:
            return None, None
        return final_usage, llm_calls

    def _extract_rollout_tool_calls(self, records: List[Dict[str, Any]]) -> Optional[int]:
        values = select_values(records, '$[?(@.type=="response_item" && @.payload.type=="function_call")]')
        if values is None:
            return 0 if records else None
        return len(values)

    def _extract_stream_tool_calls(self, payloads: List[Dict[str, Any]]) -> Optional[int]:
        function_calls = select_values(payloads, '$[?(@.type=="response_item" && @.payload.type=="function_call")]')
        if function_calls is not None:
            return len(function_calls)
        tool_item_ids: set[str] = set()
        for tool_type in {"mcp_tool_call", "collab_tool_call", "command_execution", "web_search"}:
            ids = select_values(payloads, f'$[?(@.item.type=="{tool_type}")].item.id')
            if ids is None:
                continue
            for item_id in ids:
                if isinstance(item_id, str):
                    normalized_id = item_id.strip()
                    if normalized_id:
                        tool_item_ids.add(normalized_id)
        if not tool_item_ids and not payloads:
            return None
        return len(tool_item_ids)

    def _resolve_otel_settings(
        self,
    ) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        return (
            os.environ.get("CODEX_OTEL_EXPORTER"),
            os.environ.get("CODEX_OTEL_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
            os.environ.get("CODEX_OTEL_PROTOCOL"),
            os.environ.get("CODEX_OTEL_ENVIRONMENT"),
            os.environ.get("CODEX_OTEL_LOG_USER_PROMPT"),
        )

    def _config_path(self) -> Path:
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            return Path(codex_home).expanduser() / "config.toml"
        return Path(os.path.expanduser("~/.codex/config.toml"))

    def _use_oauth(self) -> bool:
        value = os.environ.get("CAKIT_CODEX_USE_OAUTH")
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "y"}

    def _auth_path(self) -> Path:
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            return Path(codex_home).expanduser() / "auth.json"
        return Path.home() / ".codex" / "auth.json"

    def _build_otel_config(
        self,
        *,
        exporter_name: Optional[str],
        endpoint: Optional[str],
        protocol: Optional[str],
        environment: Optional[str],
        log_user_prompt: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        exporter_config = self._build_otel_exporter_config(
            exporter_name=exporter_name,
            endpoint=endpoint,
            protocol=protocol,
        )
        if exporter_config is None:
            return None
        otel_config: Dict[str, Any] = {"exporter": exporter_config}
        if environment:
            otel_config["environment"] = environment
        if log_user_prompt is not None:
            otel_config["log_user_prompt"] = str(log_user_prompt).strip().lower() in {"1", "true", "yes", "y"}
        return otel_config

    def _build_otel_exporter_config(
        self,
        *,
        exporter_name: Optional[str],
        endpoint: Optional[str],
        protocol: Optional[str],
    ) -> Optional[object]:
        if not exporter_name:
            return None
        normalized_exporter = exporter_name.strip().lower()
        if not normalized_exporter:
            return None
        if normalized_exporter in {"none", "statsig"}:
            return normalized_exporter
        if normalized_exporter == "otlp-grpc":
            if not endpoint:
                return None
            return {"otlp-grpc": {"endpoint": endpoint}}
        if normalized_exporter == "otlp-http":
            if not endpoint:
                return None
            normalized_protocol = protocol.strip().lower() if isinstance(protocol, str) and protocol.strip() else "binary"
            if normalized_protocol not in {"binary", "json"}:
                return None
            return {
                "otlp-http": {
                    "endpoint": endpoint,
                    "protocol": normalized_protocol,
                }
            }
        return None

    def _extract_turn_completed_metrics(
        self, payloads: List[Dict[str, Any]]
    ) -> tuple[Optional[Dict[str, int]], Optional[int]]:
        turn_completed = select_values(payloads, '$[?(@.type=="turn.completed")]')
        if turn_completed is None:
            return None, None
        llm_calls = len(turn_completed)
        input_values = select_values(payloads, '$[?(@.type=="turn.completed")].usage.input_tokens')
        cached_values = select_values(payloads, '$[?(@.type=="turn.completed")].usage.cached_input_tokens')
        output_values = select_values(payloads, '$[?(@.type=="turn.completed")].usage.output_tokens')
        if input_values is None or cached_values is None or output_values is None:
            return None, llm_calls
        if len(input_values) != llm_calls or len(cached_values) != llm_calls or len(output_values) != llm_calls:
            return None, llm_calls
        input_tokens = sum_int(payloads, '$[?(@.type=="turn.completed")].usage.input_tokens')
        cached_input_tokens = sum_int(payloads, '$[?(@.type=="turn.completed")].usage.cached_input_tokens')
        output_tokens = sum_int(payloads, '$[?(@.type=="turn.completed")].usage.output_tokens')
        total_tokens = sum_int(payloads, '$[?(@.type=="turn.completed")].usage.total_tokens')
        if input_tokens is None or cached_input_tokens is None or output_tokens is None:
            return None, llm_calls
        usage = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": total_tokens if total_tokens is not None else input_tokens + output_tokens,
        }
        return usage, llm_calls

    def _extract_model_name(self, payloads: List[Dict[str, Any]]) -> Optional[str]:
        thread_id = last_value(payloads, '$[?(@.type=="thread.started")].thread_id')
        if not isinstance(thread_id, str):
            return None
        thread_id = thread_id.strip()
        if not thread_id:
            return None
        sessions_root = self._sessions_root()
        if not sessions_root.exists():
            return None
        for _ in range(5):
            matches = sorted(sessions_root.rglob(f"rollout-*{thread_id}.jsonl"))
            if len(matches) != 1:
                time.sleep(0.1)
                continue
            records_text = self._read_text(matches[0])
            if records_text is None:
                time.sleep(0.1)
                continue
            records = runtime_parsing.load_output_json_payloads(records_text, stdout_only_output=False)
            model_name = last_value(records, '$[?(@.type=="turn_context")].payload.model')
            if isinstance(model_name, str):
                normalized = model_name.strip()
                if normalized:
                    return normalized
            time.sleep(0.1)
        return None
