from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import request as urlrequest

from .base import (
    CodingAgent,
    InstallStrategy,
    RunCommandTemplate,
    RunParseResult,
    RunPlan,
    VersionCommandTemplate,
)
from ..models import InstallResult
from ..stats_extract import last_value, merge_model_usage, parse_usage_by_model, req_str, select_values, sum_int
from ..agent_runtime import command_exec as runtime_command
from ..agent_runtime import env as runtime_env
from ..agent_runtime import install_version as runtime_install
from ..agent_runtime import parsing as runtime_parsing


class DeepAgentsAgent(CodingAgent):
    name = "deepagents"
    display_name = "Deep Agents"
    binary = "deepagents"
    install_strategy = InstallStrategy(
        kind="uv_tool",
        package="deepagents-cli",
        version_style="pep440",
        python_version="3.12",
        force=True,
    )
    run_template = RunCommandTemplate(
        base_args=("--no-stream",),
        prompt_mode="flag",
        prompt_flag="-n",
        model_flag="--model",
        media_injection="none",
    )
    version_template = VersionCommandTemplate(
        args=("deepagents", "--version"),
        parse_mode="regex_first_line",
        regex=r"^(?:deepagents(?:-cli)?\s+)?([A-Za-z0-9._-]+)$",
    )
    _THREAD_ID_RE = re.compile(r"Thread:\s*([0-9a-fA-F]{8})")

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        result = super().install(scope=scope, version=version)
        if result.ok or not self._should_retry_alpine_sqlite_vec_install(result=result):
            return result
        return self._install_with_alpine_sqlite_vec_workaround(version=version)

    def get_version(self) -> Optional[str]:
        version = super().get_version()
        if version is not None:
            return version
        return self._installed_package_version()

    def _build_run_plan(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> Optional[RunPlan]:
        env, env_error, selected_model = self._build_run_env(model_override=model_override)
        if env_error is not None:
            self._raise_config_error(env_error)
        return self._build_templated_run_plan(
            prompt=prompt,
            model=selected_model,
            env=env,
            template=self.run_template,
            parse_output=lambda output, command_result: self._parse_pipeline_output(
                output,
                command_result,
                base_env=base_env,
            ),
        )

    def _parse_pipeline_output(
        self,
        output: str,
        command_result: Any,
        *,
        base_env: Optional[Dict[str, str]],
    ) -> RunParseResult:
        match = self._THREAD_ID_RE.search(output)
        thread_id = match.group(1).lower() if match else None

        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls: Optional[int] = None
        tool_calls: Optional[int] = None
        response: Optional[str] = None
        if thread_id:
            stats_payload = self._extract_checkpoint_stats(thread_id=thread_id, base_env=base_env)
            parsed = self._parse_checkpoint_stats_payload(stats_payload)
            if parsed is not None:
                models_usage, llm_calls, tool_calls, response = parsed
        if response is None:
            response = runtime_parsing.last_stdout_line(
                output,
                skip_prefixes=(
                    "Running task non-interactively",
                    "Agent:",
                    "Thread:",
                    "✓ Task completed",
                    "🔧 Calling tool:",
                    "✓ Auto-approved:",
                ),
            )
        return RunParseResult(
            response=response,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
        )

    def _parse_checkpoint_stats_payload(
        self,
        payload: Optional[Dict[str, Any]],
    ) -> Optional[tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int], Optional[str]]]:
        if not isinstance(payload, dict):
            return None
        assistant_messages = [
            item
            for item in (select_values(payload, '$.messages[?(@.type == "ai")]') or [])
            if isinstance(item, dict)
        ]
        models_usage: Dict[str, Dict[str, int]] = {}
        for message in assistant_messages:
            model_name = req_str(message, "$.response_metadata.model_name")
            usage_raw = last_value(message, "$.usage_metadata")
            usage = parse_usage_by_model(usage_raw, "input_output") if isinstance(usage_raw, dict) else None
            if model_name is None or usage is None:
                continue
            merge_model_usage(models_usage, model_name, usage)

        nested_tool_call_values = select_values(payload, '$.messages[?(@.type == "ai")].tool_calls[*]')
        nested_tool_calls = len(nested_tool_call_values) if nested_tool_call_values is not None else None
        scalar_tool_calls = sum_int(payload, '$.messages[?(@.type == "ai")].tool_calls')
        tool_calls = (
            None
            if nested_tool_calls is None and scalar_tool_calls is None
            else (nested_tool_calls or 0) + (scalar_tool_calls or 0)
        )
        if tool_calls is None and assistant_messages:
            tool_calls = 0

        response = next(
            (
                text
                for text in (
                    runtime_parsing.extract_content_text(last_value(message, "$.content"), allow_scalars=True)
                    for message in reversed(assistant_messages)
                )
                if text is not None
            ),
            None,
        )
        return (
            models_usage,
            (len(assistant_messages) if assistant_messages else None),
            tool_calls,
            response,
        )

    def _build_run_env(
        self, *, model_override: Optional[str]
    ) -> tuple[Dict[str, str], Optional[str], str]:
        resolved, error = runtime_env.resolve_openai_env(
            api_key_env="DEEPAGENTS_OPENAI_API_KEY",
            model_env="DEEPAGENTS_OPENAI_MODEL",
            base_url_env="DEEPAGENTS_OPENAI_BASE_URL",
            model_override=model_override,
            normalize_text=runtime_parsing.normalize_text,
        )
        if error is not None:
            return {}, error, ""
        model_raw = resolved.get("model")
        model = (
            runtime_env.normalize_litellm_model(model_raw, output_format="colon")
            if isinstance(model_raw, str)
            else None
        )
        if not model:
            return {}, runtime_env.missing_env_with_fallback_message([("DEEPAGENTS_OPENAI_MODEL", "OPENAI_DEFAULT_MODEL")]), ""

        env: Dict[str, str] = {
            "OPENAI_API_KEY": str(resolved.get("api_key")),
        }
        base_url = resolved.get("base_url")
        if base_url:
            env["OPENAI_BASE_URL"] = base_url
        return env, None, model

    def _should_retry_alpine_sqlite_vec_install(self, *, result: InstallResult) -> bool:
        if shutil.which("apk") is None:
            return False
        details = runtime_parsing.normalize_text(result.details)
        if details is None:
            return False
        return "sqlite-vec" in details and "langgraph-checkpoint-sqlite" in details

    def _install_with_alpine_sqlite_vec_workaround(self, *, version: Optional[str]) -> InstallResult:
        resolved_version, requirements, sqlite_checkpoint_requirement = self._build_alpine_sqlite_vec_requirements(
            version=version
        )
        if resolved_version is None or requirements is None or sqlite_checkpoint_requirement is None:
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details="deepagents Alpine compatibility workaround could not resolve package metadata",
                config_path=None,
            )
        if runtime_install.resolve_uv_binary() is None and not runtime_install.ensure_uv(self._run):
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details="uv is required for the Alpine deepagents compatibility install path",
                config_path=None,
            )
        uv_binary = runtime_install.resolve_uv_binary() or "uv"
        install_root = self._deepagents_install_root()
        version_key = resolved_version.replace("/", "-")
        venv_dir = install_root / "tools" / f"deepagents-{version_key}-alpine"
        requirements_path = install_root / "cache" / f"deepagents-{version_key}-alpine.requirements.txt"
        if venv_dir.exists():
            shutil.rmtree(venv_dir, ignore_errors=True)
        self._write_text(requirements_path, "\n".join(requirements) + "\n")

        command_outputs: list[str] = []
        venv_python = str(venv_dir / "bin" / "python")
        for command in (
            [uv_binary, "venv", "--python", "3.12", str(venv_dir)],
            [uv_binary, "pip", "install", "--python", venv_python, "-r", str(requirements_path)],
            [uv_binary, "pip", "install", "--python", venv_python, "--no-deps", sqlite_checkpoint_requirement],
            [uv_binary, "pip", "install", "--python", venv_python, "--no-deps", f"deepagents-cli=={resolved_version}"],
        ):
            result = self._run(command)
            if result.output:
                command_outputs.append(result.output)
            if result.exit_code != 0:
                details = "\n\n".join(command_outputs).strip() or result.output
                return InstallResult(
                    agent=self.name,
                    version=None,
                    ok=False,
                    details=details,
                    config_path=None,
                )

        binary_path = venv_dir / "bin" / "deepagents"
        if not binary_path.exists():
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details=f"deepagents compatibility install did not create {binary_path}",
                config_path=None,
            )
        link_error = self._link_compatibility_deepagents_binary(binary_path)
        if link_error is not None:
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details=link_error,
                config_path=None,
            )
        installed_version = self.get_version()
        if not self._installed_version_matches_requested(
            requested_version=version or resolved_version,
            observed_version=installed_version,
        ):
            details = self._build_install_verification_message(
                requested_version=version or resolved_version,
                observed_version=installed_version,
            )
            return InstallResult(
                agent=self.name,
                version=None,
                ok=False,
                details=details,
                config_path=None,
            )
        return InstallResult(
            agent=self.name,
            version=installed_version,
            ok=True,
            details=None,
            config_path=self.configure(),
        )

    def _build_alpine_sqlite_vec_requirements(
        self, *, version: Optional[str]
    ) -> tuple[Optional[str], Optional[list[str]], Optional[str]]:
        metadata_url = (
            f"https://pypi.org/pypi/deepagents-cli/{version}/json"
            if version
            else "https://pypi.org/pypi/deepagents-cli/json"
        )
        with urlrequest.urlopen(metadata_url, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        info = payload.get("info", {})
        resolved_version = runtime_parsing.normalize_text(info.get("version"))
        requires_dist = info.get("requires_dist")
        if resolved_version is None or not isinstance(requires_dist, list):
            return None, None, None

        requirements: list[str] = []
        sqlite_checkpoint_requirement: Optional[str] = None
        for raw_requirement in requires_dist:
            if not isinstance(raw_requirement, str):
                continue
            requirement = raw_requirement.strip()
            if not requirement or "extra ==" in requirement:
                continue
            if requirement.startswith("langgraph-checkpoint-sqlite"):
                sqlite_checkpoint_requirement = requirement
                continue
            requirements.append(requirement)
        return resolved_version, requirements, sqlite_checkpoint_requirement

    def _deepagents_install_root(self) -> Path:
        install_home = os.environ.get("CAKIT_INSTALL_HOME")
        candidates = [Path(install_home).expanduser()] if install_home else []
        candidates.extend(
            [
                Path("/opt") / "cakit",
                Path.home() / ".local" / "share" / "cakit",
                Path("/tmp") / "cakit",
            ]
        )
        return self._resolve_writable_dir(*candidates, purpose="DeepAgents install")

    def _deepagents_bin_dir(self) -> Path:
        uv_tool_bin = os.environ.get("UV_TOOL_BIN_DIR")
        xdg_bin_home = os.environ.get("XDG_BIN_HOME")
        candidates = []
        if uv_tool_bin:
            candidates.append(Path(uv_tool_bin).expanduser())
        if xdg_bin_home:
            candidates.append(Path(xdg_bin_home).expanduser())
        candidates.extend([Path("/usr/local/bin"), Path.home() / ".local" / "bin", Path("/tmp") / "cakit" / "bin"])
        return self._resolve_writable_dir(*candidates, purpose="DeepAgents bin")

    def _link_compatibility_deepagents_binary(self, binary_path: Path) -> Optional[str]:
        bin_dir = self._deepagents_bin_dir()
        target = bin_dir / "deepagents"
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink()
        target.symlink_to(binary_path)
        return None

    def _installed_package_version(self) -> Optional[str]:
        for dist_info_dir in self._dist_info_dirs():
            metadata_text = self._read_text(dist_info_dir / "METADATA")
            if metadata_text is None:
                continue
            match = re.search(r"^Version:\s*(\S+)\s*$", metadata_text, flags=re.MULTILINE)
            if match is not None:
                return runtime_parsing.normalize_text(match.group(1))
        return None

    def _dist_info_dirs(self) -> tuple[Path, ...]:
        binary_path = runtime_command.resolve_binary(
            agent_name=self.name,
            binary=self.binary,
            npm_prefix=self._npm_prefix(),
            env_source=os.environ,
        )
        if binary_path is None:
            return ()
        roots: list[Path] = []
        raw_binary_path = Path(binary_path).expanduser()
        if raw_binary_path.parent.name == "bin":
            roots.append(raw_binary_path.parent.parent)
        try:
            resolved_binary_path = raw_binary_path.resolve()
        except OSError:
            resolved_binary_path = None
        if resolved_binary_path is not None and resolved_binary_path.parent.name == "bin":
            roots.append(resolved_binary_path.parent.parent)

        dist_info_dirs: list[Path] = []
        for root in dict.fromkeys(roots):
            for site_packages_dir in sorted(root.glob("lib/python*/site-packages")):
                dist_info_dirs.extend(sorted(site_packages_dir.glob("deepagents_cli-*.dist-info")))
        return tuple(dict.fromkeys(dist_info_dirs))

    def _extract_checkpoint_stats(
        self, *, thread_id: str, base_env: Optional[Dict[str, str]]
    ) -> Optional[Dict[str, Any]]:
        binary = runtime_command.resolve_binary(
            agent_name=self.name,
            binary=self.binary,
            npm_prefix=self._npm_prefix(),
            env_source=os.environ,
        )
        if not binary:
            return None
        binary_path = Path(binary).expanduser().resolve()
        python_executable = runtime_install.resolve_python_executable(search_dirs=(binary_path.parent,))
        if python_executable is None:
            return None
        parser_code = r"""
import json
import sqlite3
import sys
from pathlib import Path
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

thread_id = sys.argv[1]
db_path = Path.home() / ".deepagents" / "sessions.db"
if not db_path.exists():
    print("{}")
    raise SystemExit(0)

conn = sqlite3.connect(str(db_path))
cur = conn.cursor()
cur.execute(
    "select type, checkpoint from checkpoints where thread_id=? order by checkpoint_id desc limit 1",
    (thread_id,),
)
row = cur.fetchone()
conn.close()
if not row:
    print("{}")
    raise SystemExit(0)

serde = JsonPlusSerializer()
try:
    checkpoint = serde.loads_typed((row[0], row[1]))
except Exception:
    print("{}")
    raise SystemExit(0)

def to_jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "dict"):
        try:
            return to_jsonable(value.dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return {
                str(key): to_jsonable(item)
                for key, item in vars(value).items()
                if not str(key).startswith("_")
            }
        except Exception:
            pass
    return str(value)

channel_values = checkpoint["channel_values"] if isinstance(checkpoint, dict) and "channel_values" in checkpoint else None
messages = channel_values["messages"] if isinstance(channel_values, dict) and "messages" in channel_values else []
if not isinstance(messages, list):
    messages = []

serialized_messages = [
    to_jsonable(
        {
            "type": getattr(message, "type", None),
            "usage_metadata": getattr(message, "usage_metadata", None),
            "response_metadata": getattr(message, "response_metadata", None),
            "tool_calls": getattr(message, "tool_calls", None),
            "content": getattr(message, "content", None),
        }
    )
    for message in messages
]

print(
    json.dumps(
        {
            "messages": serialized_messages,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
)
        """
        return runtime_parsing.run_json_dict_command(
            args=[python_executable, "-c", parser_code, thread_id],
            run=self._run,
            base_env=base_env,
        )
