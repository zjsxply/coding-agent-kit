from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class ContinueAgent(CodingAgent):
    name = "continue"
    display_name = "Continue"
    binary = "cn"
    supports_images = False
    supports_videos = False

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        result = self._npm_install("@continuedev/cli", scope, version=version)
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
        resolved, error = self._resolve_openai_auth(model_override=None)
        if error is not None:
            return None
        api_key = resolved.get("api_key")
        model = resolved.get("model")
        base_url = resolved.get("base_url")
        if not api_key or not model:
            return None
        config_path = self._continue_home() / "config.yaml"
        self._write_text(config_path, self._build_config_yaml(api_key=api_key, model=model, base_url=base_url))
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
        del reasoning_effort
        resolved, env_error = self._resolve_openai_auth(model_override=model_override)
        if env_error is not None:
            output_path = self._write_output(self.name, env_error)
            trajectory_path = self._write_trajectory(self.name, format_trace_text(env_error, source=str(output_path)))
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

        run_home = Path("/tmp") / f"cakit-continue-{uuid.uuid4().hex}"
        run_home.mkdir(parents=True, exist_ok=True)
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
        cmd = [
            "cn",
            "-p",
            "--auto",
            "--config",
            str(config_path),
            prompt,
        ]
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))

        session_payload = self._load_session(run_home / "sessions")
        models_usage, llm_calls, tool_calls = self._extract_stats(session_payload)
        response = self._extract_response(output, session_payload)
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
            telemetry_log=str(run_home / "logs" / "cn.log"),
            response=response,
            exit_code=run_exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["cn", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _continue_home(self) -> Path:
        root = os.environ.get("CONTINUE_GLOBAL_DIR")
        if root:
            return Path(root).expanduser()
        return Path.home() / ".continue"

    def _resolve_openai_auth(self, *, model_override: Optional[str]) -> tuple[Dict[str, str], Optional[str]]:
        api_key = os.environ.get("CAKIT_CONTINUE_OPENAI_API_KEY")
        model = model_override or os.environ.get("CAKIT_CONTINUE_OPENAI_MODEL")
        base_url = os.environ.get("CAKIT_CONTINUE_OPENAI_BASE_URL")

        missing: list[str] = []
        if not api_key:
            missing.append("CAKIT_CONTINUE_OPENAI_API_KEY")
        if not model:
            missing.append("CAKIT_CONTINUE_OPENAI_MODEL")
        if missing:
            return {}, f"missing required environment variable(s): {', '.join(missing)}"

        resolved: Dict[str, str] = {
            "api_key": api_key,
            "model": model,
        }
        if base_url:
            resolved["base_url"] = base_url
        return resolved, None

    def _build_config_yaml(self, *, api_key: str, model: str, base_url: Optional[str]) -> str:
        lines = [
            "name: CAKIT Continue Config",
            "version: 1.0.0",
            "schema: v1",
            "models:",
            "  - name: cakit-openai",
            "    provider: openai",
            f"    model: {json.dumps(model)}",
            f"    apiKey: {json.dumps(api_key)}",
        ]
        if base_url:
            lines.append(f"    apiBase: {json.dumps(base_url)}")
        lines.extend(
            [
                "    roles:",
                "      - chat",
            ]
        )
        return "\n".join(lines) + "\n"

    def _load_session(self, sessions_dir: Path) -> Optional[Dict[str, Any]]:
        if not sessions_dir.is_dir():
            return None
        session_id = self._resolve_session_id(sessions_dir)
        if not session_id:
            return None
        path = sessions_dir / f"{session_id}.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if isinstance(payload, dict):
            return payload
        return None

    def _resolve_session_id(self, sessions_dir: Path) -> Optional[str]:
        manifest_path = sessions_dir / "sessions.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                return None
            if not isinstance(manifest, list) or not manifest:
                return None
            last_item = manifest[-1]
            if not isinstance(last_item, dict):
                return None
            value = last_item.get("sessionId")
            if isinstance(value, str) and value:
                return value
            return None

        candidates = sorted(path for path in sessions_dir.glob("*.json") if path.name != "sessions.json")
        if len(candidates) != 1:
            return None
        return candidates[0].stem

    def _extract_stats(
        self, session_payload: Optional[Dict[str, Any]]
    ) -> Tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int]]:
        if not isinstance(session_payload, dict):
            return {}, None, None
        history = session_payload.get("history")
        if not isinstance(history, list):
            return {}, None, None

        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls = 0
        tool_calls = 0

        for item in history:
            if not isinstance(item, dict):
                return {}, None, None
            message = item.get("message")
            if not isinstance(message, dict):
                return {}, None, None
            if message.get("role") != "assistant":
                continue

            raw_tool_calls = message.get("toolCalls")
            if raw_tool_calls is not None:
                if not isinstance(raw_tool_calls, list):
                    return {}, None, None
                tool_calls += len(raw_tool_calls)

            usage = message.get("usage")
            if usage is None:
                continue
            if not isinstance(usage, dict):
                return {}, None, None

            model = usage.get("model")
            prompt_tokens = self._as_int(usage.get("prompt_tokens"))
            completion_tokens = self._as_int(usage.get("completion_tokens"))
            total_tokens = self._as_int(usage.get("total_tokens"))
            if not isinstance(model, str) or not model.strip():
                return {}, None, None
            if prompt_tokens is None or completion_tokens is None:
                return {}, None, None
            if total_tokens is None:
                total_tokens = prompt_tokens + completion_tokens

            entry = models_usage.setdefault(
                model,
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            entry["prompt_tokens"] += prompt_tokens
            entry["completion_tokens"] += completion_tokens
            entry["total_tokens"] += total_tokens
            llm_calls += 1

        if llm_calls < 1:
            return {}, None, None
        return models_usage, llm_calls, tool_calls

    def _extract_response(self, output: str, session_payload: Optional[Dict[str, Any]]) -> Optional[str]:
        stdout = self._stdout_only(output).strip()
        if stdout:
            return stdout
        if not isinstance(session_payload, dict):
            return None
        history = session_payload.get("history")
        if not isinstance(history, list):
            return None
        for item in reversed(history):
            if not isinstance(item, dict):
                return None
            message = item.get("message")
            if not isinstance(message, dict):
                return None
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, str):
                cleaned = content.strip()
                if cleaned:
                    return cleaned
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        return None
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                if parts:
                    return "\n".join(parts)
        return None
