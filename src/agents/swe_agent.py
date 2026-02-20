from __future__ import annotations

import io
import json
import os
import tarfile
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent, CommandResult, InstallStrategy, VersionCommandTemplate
from ..models import RunResult
from ..stats_extract import last_value, req_str, select_values, sum_int
from ..utils import format_trace_text


class SweAgent(CodingAgent):
    name = "swe-agent"
    display_name = "SWE-agent"
    binary = "sweagent"
    required_runtimes = ("uv",)
    install_strategy = InstallStrategy(kind="custom")
    version_template = VersionCommandTemplate(
        args=("sweagent", "--version"),
        parse_mode="text",
        env_mode="runtime_assets",
    )

    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        result = self._run(["sweagent", "--version"], env=self._runtime_asset_env(create_if_missing=True))
        return result.exit_code == 0 and bool(result.output.strip())

    def _install_with_custom_strategy(
        self,
        *,
        strategy: InstallStrategy,
        scope: str,
        version: Optional[str],
    ) -> CommandResult:
        try:
            resolved_version = self._resolve_version(version)
        except Exception as exc:
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr=str(exc),
                duration_seconds=0.0,
            )
        url = f"https://github.com/SWE-agent/SWE-agent/archive/refs/tags/{resolved_version}.tar.gz"
        result = self._uv_pip_install([url], no_cache_dir=True)
        assets_ok = self._prepare_runtime_assets(resolved_version)
        details = result.output
        exit_code = result.exit_code
        if result.exit_code == 0 and not assets_ok:
            details = f"{details}\n[install] failed to prepare SWE-agent runtime assets."
            exit_code = 1
        return CommandResult(
            exit_code=exit_code,
            stdout=details,
            stderr="",
            duration_seconds=result.duration_seconds,
        )

    def configure(self) -> Optional[str]:
        runtime_env = self._runtime_asset_env(create_if_missing=True)
        tools_dir = runtime_env.get("SWE_AGENT_TOOLS_DIR")
        if not tools_dir:
            return None

        config = (
            "agent:\n"
            "  templates:\n"
            "    system_template: |-\n"
            "      You are a helpful assistant that can interact with a computer to solve tasks.\n"
            "    instance_template: |-\n"
            "      {{problem_statement}}\n"
            "  tools:\n"
            "    bundles:\n"
            f"      - path: {json.dumps(str(Path(tools_dir) / 'registry'))}\n"
            f"      - path: {json.dumps(str(Path(tools_dir) / 'submit'))}\n"
            "    enable_bash_tool: true\n"
            "    parse_function:\n"
            "      type: thought_action\n"
            "  history_processors:\n"
            "    - type: cache_control\n"
            "      last_n_messages: 2\n"
        )
        path = Path.home() / ".config" / "sweagent" / "config.yaml"
        self._write_text(path, config)
        return str(path)

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        api_key = self._resolve_openai_api_key("SWE_AGENT_API_KEY")
        api_base = self._resolve_openai_base_url("SWE_AGENT_API_BASE")
        env = {
            "SWE_AGENT_API_KEY": api_key,
            "SWE_AGENT_API_BASE": api_base,
            "OPENAI_API_KEY": api_key,
            "OPENAI_API_BASE": api_base,
            "OPENAI_BASE_URL": api_base,
        }
        env.update(self._runtime_asset_env(create_if_missing=True))
        model = self._resolve_openai_model("SWE_AGENT_MODEL", model_override=model_override)
        repo_path = self._resolve_repo_path(base_env=base_env)
        output_dir = self._make_temp_dir(prefix="cakit-sweagent-")
        cmd = [
            "sweagent",
            "run",
            "--env.deployment.type=local",
            "--env.repo.type=local",
            f"--env.repo.path={repo_path}",
            "--problem_statement.text",
            prompt,
            f"--output_dir={output_dir}",
        ]
        config_path = Path.home() / ".config" / "sweagent" / "config.yaml"
        if config_path.exists():
            cmd.extend(["--config", str(config_path)])
        if model:
            cmd.extend(["--agent.model.name", model])
        result = self._run_sweagent_command(cmd, env=env, base_env=base_env)
        output = result.output
        if result.exit_code != 0 and "--output_dir" in output and "unrecognized" in output:
            cmd = [
                "sweagent",
                "run",
                "--env.deployment.type=local",
                "--env.repo.type=local",
                f"--env.repo.path={repo_path}",
                "--problem_statement.text",
                prompt,
            ]
            if config_path.exists():
                cmd.extend(["--config", str(config_path)])
            if model:
                cmd.extend(["--agent.model.name", model])
            result = self._run_sweagent_command(cmd, env=env, base_env=base_env)
            output = result.output

        trajectory_files: list[Path] = []
        if output_dir.exists():
            trajectory_files = sorted(path for path in output_dir.rglob("*.traj") if path.is_file())
        if not trajectory_files:
            trajectory_payloads = None
        else:
            loaded_payloads: list[Dict[str, Any]] = []
            for trajectory_file in trajectory_files:
                if not trajectory_file.exists():
                    loaded_payloads = []
                    break
                data = self._load_json(trajectory_file)
                if not isinstance(data, dict):
                    loaded_payloads = []
                    break
                loaded_payloads.append(data)
            trajectory_payloads = loaded_payloads or None
        usage, model_name, llm_calls, tool_calls, response = self._extract_trajectory_stats(trajectory_payloads)
        snapshot = self._build_single_model_stats_snapshot(
            model_name=model_name,
            usage=usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=None,
        )

        if trajectory_files:
            entries: list[dict[str, str]] = []
            for trajectory_file in trajectory_files:
                trajectory_raw = self._read_text(trajectory_file) or ""
                if not trajectory_raw.strip():
                    continue
                entries.append({"path": str(trajectory_file), "content": trajectory_raw})
            if entries:
                trajectory_content = format_trace_text(
                    json.dumps({"trajectory_files": entries}, ensure_ascii=True),
                    source=str(output_dir),
                )
            else:
                trajectory_content = format_trace_text(output, source=str(output_dir))
        else:
            trajectory_content = format_trace_text(output, source=str(output_dir))
        if response is None:
            response = self._last_stdout_line(output)
        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=snapshot.models_usage if snapshot is not None else {},
            llm_calls=snapshot.llm_calls if snapshot is not None else None,
            tool_calls=snapshot.tool_calls if snapshot is not None else None,
            trajectory_content=trajectory_content,
        )

    def _resolve_version(self, requested: Optional[str]) -> str:
        if requested:
            normalized = requested.strip()
            if normalized:
                return normalized
        url = "https://api.github.com/repos/SWE-agent/SWE-agent/releases/latest"
        request = urllib.request.Request(url, headers=self._github_headers())
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        tag = (payload.get("tag_name") or "").strip()
        if not tag:
            raise RuntimeError("Failed to resolve latest SWE-agent release tag from GitHub.")
        return tag

    def _github_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("CAKIT_SWE_AGENT_GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _runtime_asset_env(self, *, create_if_missing: bool) -> Dict[str, str]:
        versions: list[str] = []
        try:
            installed = metadata.version("sweagent")
        except Exception:
            installed = None
        if installed and installed not in versions:
            versions.append(installed)

        for version in versions:
            normalized = self._normalize_release_tag(version)
            paths = self._runtime_asset_paths(normalized)
            ready = self._runtime_assets_ready(paths)
            if not ready and create_if_missing:
                ready = self._prepare_runtime_assets(normalized)
            if ready:
                return {
                    "SWE_AGENT_CONFIG_DIR": str(paths["config"]),
                    "SWE_AGENT_TOOLS_DIR": str(paths["tools"]),
                    "SWE_AGENT_TRAJECTORY_DIR": str(paths["trajectories"]),
                }

        if create_if_missing:
            resolved_version = self._resolve_version(None)
            normalized = self._normalize_release_tag(resolved_version)
            paths = self._runtime_asset_paths(normalized)
            if self._prepare_runtime_assets(normalized):
                return {
                    "SWE_AGENT_CONFIG_DIR": str(paths["config"]),
                    "SWE_AGENT_TOOLS_DIR": str(paths["tools"]),
                    "SWE_AGENT_TRAJECTORY_DIR": str(paths["trajectories"]),
                }
        return {}

    def _normalize_release_tag(self, version: str) -> str:
        normalized = version.strip()
        if not normalized:
            return normalized
        if normalized.startswith("v"):
            return normalized
        return f"v{normalized}"

    def _runtime_asset_paths(self, version: str) -> Dict[str, Path]:
        root = Path.home() / ".cache" / "cakit" / "swe-agent-assets" / version
        return {
            "root": root,
            "config": root / "config",
            "tools": root / "tools",
            "trajectories": root / "trajectories",
        }

    def _runtime_assets_ready(self, paths: Dict[str, Path]) -> bool:
        config_default = paths["config"] / "default.yaml"
        tools_dir = paths["tools"]
        trajectories_dir = paths["trajectories"]
        if not config_default.is_file():
            return False
        if not tools_dir.is_dir():
            return False
        try:
            has_tools = any(tools_dir.iterdir())
        except Exception:
            return False
        if not has_tools:
            return False
        trajectories_dir.mkdir(parents=True, exist_ok=True)
        return True

    def _extract_runtime_assets_archive(self, archive_data: bytes, root: Path) -> bool:
        root.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz") as archive:
                for member in archive.getmembers():
                    parts = member.name.split("/", 1)
                    if len(parts) != 2:
                        continue
                    relative = parts[1]
                    if not any(
                        relative == prefix or relative.startswith(f"{prefix}/")
                        for prefix in ("config", "tools", "trajectories")
                    ):
                        continue
                    target = root / relative
                    try:
                        target.resolve().relative_to(root.resolve())
                    except Exception:
                        continue
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if not member.isfile():
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        return False
                    target.write_bytes(extracted.read())
                    try:
                        target.chmod(member.mode & 0o777)
                    except Exception:
                        pass
        except Exception:
            return False
        return True

    def _prepare_runtime_assets(self, version: str) -> bool:
        normalized = self._normalize_release_tag(version)
        paths = self._runtime_asset_paths(normalized)
        if self._runtime_assets_ready(paths):
            return True
        url = f"https://github.com/SWE-agent/SWE-agent/archive/refs/tags/{normalized}.tar.gz"
        request = urllib.request.Request(url, headers=self._github_headers())
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                archive_data = response.read()
        except Exception:
            return False
        if not self._extract_runtime_assets_archive(archive_data, paths["root"]):
            return False
        return self._runtime_assets_ready(paths)

    def _resolve_repo_path(self, *, base_env: Optional[Dict[str, str]]) -> Path:
        result = self._run(
            ["git", "-C", str(self.workdir), "rev-parse", "--is-inside-work-tree"],
            base_env=base_env,
        )
        if result.exit_code == 0 and result.stdout.strip().lower() == "true":
            return self.workdir

        repo_path = self._make_temp_dir(prefix="cakit-swe-repo-")
        (repo_path / "README.md").write_text("Temporary repository for cakit swe-agent run.\n", encoding="utf-8")
        init_commands = [
            ["git", "-C", str(repo_path), "init"],
            ["git", "-C", str(repo_path), "config", "user.email", "cakit@example.com"],
            ["git", "-C", str(repo_path), "config", "user.name", "cakit"],
            ["git", "-C", str(repo_path), "add", "README.md"],
            ["git", "-C", str(repo_path), "commit", "-m", "Initial commit"],
        ]
        for command in init_commands:
            result = self._run(command, base_env=base_env)
            if result.exit_code != 0:
                return self.workdir
        return repo_path

    def _run_sweagent_command(
        self, args: list[str], *, env: Optional[Dict[str, str]], base_env: Optional[Dict[str, str]]
    ):
        original_workdir = self.workdir
        self.workdir = Path("/")
        try:
            return self._run(args, env=env, base_env=base_env)
        finally:
            self.workdir = original_workdir

    def _extract_single_trajectory_stats(
        self, payload: Dict[str, Any]
    ) -> tuple[Optional[Dict[str, int]], Optional[str], Optional[int], Optional[int], Optional[str]]:
        tokens_sent = sum_int(payload, "$.info.model_stats.tokens_sent")
        tokens_received = sum_int(payload, "$.info.model_stats.tokens_received")
        api_calls = sum_int(payload, "$.info.model_stats.api_calls")
        if tokens_sent is None or tokens_received is None or api_calls is None:
            tokens_sent = sum_int(payload, "$.attempts[*].info.model_stats.tokens_sent")
            tokens_received = sum_int(payload, "$.attempts[*].info.model_stats.tokens_received")
            api_calls = sum_int(payload, "$.attempts[*].info.model_stats.api_calls")

        actions = select_values(payload, "$.attempts[*].trajectory[*].action")
        if actions is None:
            actions = select_values(payload, "$.trajectory[*].action")
        tool_calls = (
            sum(1 for action in actions if isinstance(action, str) and action.strip())
            if actions is not None
            else None
        )

        response = self._first_selected_text(
            payload,
            (
                "$.attempts[*].trajectory[*].response",
                "$.attempts[*].trajectory[*].thought",
                "$.attempts[*].trajectory[*].observation",
                "$.trajectory[*].response",
                "$.trajectory[*].thought",
                "$.trajectory[*].observation",
                "$.info.submission",
            ),
        )

        usage = (
            {
                "prompt_tokens": tokens_sent,
                "completion_tokens": tokens_received,
                "total_tokens": tokens_sent + tokens_received,
            }
            if tokens_sent is not None and tokens_received is not None
            else None
        )
        model_name = self._extract_model_name_from_replay_config(last_value(payload, "$.replay_config"))
        if not model_name:
            attempts = select_values(payload, "$.attempts[*].replay_config")
            if attempts is not None:
                for attempt in reversed(attempts):
                    model_name = self._extract_model_name_from_replay_config(attempt)
                    if model_name:
                        break
        return usage, model_name, api_calls, tool_calls, response

    def _extract_trajectory_stats(
        self, payloads: Optional[list[Dict[str, Any]]]
    ) -> tuple[Optional[Dict[str, int]], Optional[str], Optional[int], Optional[int], Optional[str]]:
        if not isinstance(payloads, list):
            return None, None, None, None, None

        parsed = [self._extract_single_trajectory_stats(payload) for payload in payloads if isinstance(payload, dict)]
        if not parsed:
            return None, None, None, None, None

        usage_items = [item_usage for item_usage, _, _, _, _ in parsed if item_usage is not None]
        usage = self._sum_usage_entries(usage_items)
        llm_call_values = [item_llm_calls for _, _, item_llm_calls, _, _ in parsed if item_llm_calls is not None]
        llm_calls = sum(llm_call_values) if llm_call_values else None
        tool_call_values = [item_tool_calls for _, _, _, item_tool_calls, _ in parsed if item_tool_calls is not None]
        tool_calls = sum(tool_call_values) if tool_call_values else None

        model_names = [item_model_name for _, item_model_name, _, _, _ in parsed if item_model_name]
        unique_names = list(dict.fromkeys(model_names))
        model_name = unique_names[0] if len(unique_names) == 1 else None

        response_candidates = [item_response for _, _, _, _, item_response in parsed if item_response]
        response = response_candidates[-1] if response_candidates else None
        return usage, model_name, llm_calls, tool_calls, response

    def _extract_model_name_from_replay_config(self, replay_config: Any) -> Optional[str]:
        if isinstance(replay_config, str):
            decoded = self._parse_json(replay_config)
            parsed = decoded if isinstance(decoded, dict) else None
        elif isinstance(replay_config, dict):
            parsed = replay_config
        else:
            parsed = None
        if parsed is None:
            return None

        model_name = req_str(parsed, "$.agent.model.name")
        if model_name:
            return model_name

        names = select_values(parsed, "$.agent_configs[*].model.name")
        if names is None:
            return None
        cleaned_names = [name.strip() for name in names if isinstance(name, str) and name.strip()]
        unique_names = list(dict.fromkeys(cleaned_names))
        if len(unique_names) == 1:
            return unique_names[0]
        return None
