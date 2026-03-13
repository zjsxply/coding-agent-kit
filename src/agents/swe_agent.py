from __future__ import annotations

import io
import json
import os
import re
import shutil
import tarfile
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent, InstallStrategy, ParsedStats, VersionCommandTemplate
from ..models import InstallResult, RunResult
from ..stats_extract import (
    build_single_model_stats_snapshot,
    last_value,
    req_str,
    select_values,
    sum_int,
    sum_usage_entries,
)
from ..agent_runtime import env as runtime_env
from ..agent_runtime import parsing as runtime_parsing
from ..agent_runtime import trajectory as runtime_trajectory
from ..io_helpers import dump_yaml


class SweAgent(CodingAgent):
    name = "swe-agent"
    display_name = "SWE-agent"
    binary = "sweagent"
    required_runtimes = ("uv",)
    install_strategy = InstallStrategy(
        kind="uv_tool",
        package="git+https://github.com/SWE-agent/SWE-agent",
        version_style="git_ref",
    )
    _install_runtime_asset_version: Optional[str] = None
    version_template = VersionCommandTemplate(
        args=("sweagent", "-h"),
        parse_mode="regex_first_line",
        regex=r"\bversion\s+([A-Za-z0-9._-]+)\b",
        env_mode="runtime_assets",
    )

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        resolved_version = self._resolve_version(version)
        normalized_version = self._normalize_release_tag(resolved_version)
        self._install_runtime_asset_version = normalized_version
        try:
            result = super().install(scope=scope, version=resolved_version)
        finally:
            self._install_runtime_asset_version = None
        if result.ok:
            self._write_runtime_assets_version_marker(normalized_version)
        return result

    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        result = self._run(["sweagent", "-h"], env=self._runtime_asset_env(create_if_missing=False))
        return result.exit_code == 0 and bool(result.output.strip())

    def configure(self) -> Optional[str]:
        runtime_env = self._runtime_asset_env(create_if_missing=True)
        tools_dir = runtime_env.get("SWE_AGENT_TOOLS_DIR")
        if not tools_dir:
            return None
        config = self._build_config_payload(
            registry_bundle=Path(tools_dir) / "registry",
            submit_bundle=Path(tools_dir) / "submit",
            model_name=None,
        )
        config_dir = self._resolve_writable_dir(
            Path.home() / ".config" / "sweagent",
            Path("/tmp") / "cakit" / "sweagent-config",
            purpose="SWE-agent config",
        )
        path = config_dir / "config.yaml"
        self._write_text(path, dump_yaml(config))
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
        api_key = runtime_env.resolve_openai_api_key("SWE_AGENT_API_KEY")
        api_base = runtime_env.resolve_openai_base_url("SWE_AGENT_API_BASE")
        env = {
            "SWE_AGENT_API_KEY": api_key,
            "SWE_AGENT_API_BASE": api_base,
            "OPENAI_API_KEY": api_key,
            "OPENAI_API_BASE": api_base,
            "OPENAI_BASE_URL": api_base,
        }
        env.update(self._runtime_asset_env(create_if_missing=True))
        run_home = self._make_temp_dir(prefix="cakit-sweagent-home-")
        env["HOME"] = str(run_home)
        model = runtime_env.normalize_litellm_model(
            runtime_env.resolve_openai_model("SWE_AGENT_MODEL", model_override=model_override),
            output_format="slash",
        )
        repo_path = self._resolve_repo_path(base_env=base_env)
        registry_bundle, submit_bundle = self._prepare_run_tool_bundles(Path(env["SWE_AGENT_TOOLS_DIR"]))
        output_dir = self._make_temp_dir(prefix="cakit-sweagent-")
        supports_output_dir = self._supports_output_dir(env=env, base_env=base_env)
        cmd = [
            "sweagent",
            "run",
            "--env.deployment.type=local",
            "--env.repo.type=local",
            f"--env.repo.path={repo_path}",
            "--problem_statement.text",
            prompt,
        ]
        if supports_output_dir:
            cmd.append(f"--output_dir={output_dir}")
        config_dir = self._resolve_writable_dir(
            Path.home() / ".config" / "sweagent",
            Path("/tmp") / "cakit" / "sweagent-config",
            purpose="SWE-agent config",
        )
        config_path = config_dir / "config.yaml"
        if model:
            config = self._build_config_payload(
                registry_bundle=registry_bundle,
                submit_bundle=submit_bundle,
                model_name=model,
            )
            self._write_text(config_path, dump_yaml(config))
        elif not config_path.exists():
            config = self._build_config_payload(
                registry_bundle=registry_bundle,
                submit_bundle=submit_bundle,
                model_name=runtime_env.resolve_openai_model("SWE_AGENT_MODEL"),
            )
            self._write_text(config_path, dump_yaml(config))
        if config_path.exists():
            cmd.extend(["--config", str(config_path)])
        result = self._run_sweagent_command(cmd, env=env, base_env=base_env)
        output = result.output

        trajectory_files: list[Path] = []
        if supports_output_dir and output_dir.exists():
            trajectory_files = sorted(path for path in output_dir.rglob("*.traj") if path.is_file())
        if not trajectory_files:
            trajectory_payloads = None
        else:
            loaded_payloads: list[Dict[str, Any]] = []
            for trajectory_file in trajectory_files:
                if not trajectory_file.exists():
                    loaded_payloads = []
                    break
                data = runtime_parsing.load_json(trajectory_file)
                if not isinstance(data, dict):
                    loaded_payloads = []
                    break
                loaded_payloads.append(data)
            trajectory_payloads = loaded_payloads or None
        parsed_stats = self._extract_trajectory_stats(trajectory_payloads)
        snapshot = build_single_model_stats_snapshot(
            model_name=parsed_stats.model_name,
            usage=parsed_stats.usage,
            llm_calls=parsed_stats.llm_calls,
            tool_calls=parsed_stats.tool_calls,
            total_cost=None,
        )

        trajectory_payload: Optional[str] = None
        if trajectory_files:
            entries: list[dict[str, str]] = []
            for trajectory_file in trajectory_files:
                trajectory_raw = self._read_text(trajectory_file) or ""
                if not trajectory_raw.strip():
                    continue
                entries.append({"path": str(trajectory_file), "content": trajectory_raw})
            if entries:
                trajectory_payload = json.dumps({"trajectory_files": entries}, ensure_ascii=True)
        trajectory_content = runtime_trajectory.build_trajectory_from_raw(
            raw_text=trajectory_payload,
            output=output,
            source=str(output_dir),
        )
        response = parsed_stats.response or runtime_parsing.last_stdout_line(output)
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

    def _build_config_payload(
        self,
        *,
        registry_bundle: Path,
        submit_bundle: Path,
        model_name: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "agent": {
                "model": {
                    "name": model_name or "swe-agent-required-model",
                    "per_instance_cost_limit": 0.0,
                    "total_cost_limit": 0.0,
                },
                "templates": {
                    "system_template": "You are a helpful assistant that can interact with a computer to solve tasks.",
                    "instance_template": "{{problem_statement}}",
                },
                "tools": {
                    "bundles": [
                        {"path": str(registry_bundle)},
                        {"path": str(submit_bundle)},
                    ],
                    "enable_bash_tool": True,
                    "parse_function": {"type": "thought_action"},
                },
                "history_processors": [
                    {
                        "type": "cache_control",
                        "last_n_messages": 2,
                    }
                ],
            }
        }

    def _prepare_run_tool_bundles(self, tools_dir: Path) -> tuple[Path, Path]:
        run_bundle_root = self._make_temp_dir(prefix="cakit-sweagent-bundles-")
        suffix = run_bundle_root.name.rsplit("-", 1)[-1]
        registry_source = tools_dir / "registry"
        submit_source = tools_dir / "submit"
        registry_target = run_bundle_root / f"registry-{suffix}"
        submit_target = run_bundle_root / f"submit-{suffix}"
        shutil.copytree(registry_source, registry_target)
        shutil.copytree(submit_source, submit_target)
        return registry_target, submit_target

    def _installed_version(self) -> Optional[str]:
        try:
            installed = metadata.version("sweagent")
        except Exception:
            installed = None
        normalized = runtime_parsing.normalize_text(installed)
        if normalized is not None:
            return normalized
        marker_version = self._read_runtime_assets_version_marker()
        if marker_version is not None:
            return marker_version
        result = self._run(["sweagent", "-h"], env=self._runtime_asset_env(create_if_missing=False))
        if result.exit_code != 0:
            return None
        text = runtime_parsing.first_nonempty_line(result.output)
        if text is None:
            return None
        match = re.search(r"\bv?\d+\.\d+\.\d+(?:[A-Za-z0-9.+-]*)?\b", text)
        if match:
            return match.group(0)
        return runtime_parsing.normalize_text(text)

    def _runtime_asset_env(self, *, create_if_missing: bool) -> Dict[str, str]:
        versions: list[str] = []
        install_version = self._install_runtime_asset_version
        if install_version and install_version not in versions:
            versions.append(install_version)
        installed = self._installed_version()
        if installed and installed not in versions:
            versions.append(installed)

        for version in versions:
            normalized = self._normalize_release_tag(version)
            paths = self._runtime_asset_paths(normalized)
            ready = self._runtime_assets_ready(paths)
            if not ready and create_if_missing:
                ready = self._prepare_runtime_assets(normalized)
                if ready:
                    self._write_runtime_assets_version_marker(normalized)
            if ready or not create_if_missing:
                paths["config"].mkdir(parents=True, exist_ok=True)
                paths["tools"].mkdir(parents=True, exist_ok=True)
                paths["trajectories"].mkdir(parents=True, exist_ok=True)
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
                self._write_runtime_assets_version_marker(normalized)
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

    def _runtime_assets_cache_root(self) -> Path:
        candidates = [
            Path.home() / ".cache" / "cakit" / "swe-agent-assets",
            Path("/tmp") / "cakit" / "swe-agent-assets",
        ]
        for directory in candidates:
            if (directory / ".current-version").is_file():
                return directory
        for directory in candidates:
            if directory.is_dir():
                for child in directory.iterdir():
                    if child.is_dir():
                        return directory
        return self._resolve_writable_dir(*candidates, purpose="SWE-agent runtime assets")

    def _runtime_assets_version_marker(self) -> Path:
        return self._runtime_assets_cache_root() / ".current-version"

    def _runtime_asset_paths(self, version: str) -> Dict[str, Path]:
        root = self._runtime_assets_cache_root() / version
        return {
            "root": root,
            "config": root / "config",
            "tools": root / "tools",
            "trajectories": root / "trajectories",
        }

    def _read_runtime_assets_version_marker(self) -> Optional[str]:
        marker_text = runtime_parsing.normalize_text(self._read_text(self._runtime_assets_version_marker()))
        if marker_text is None:
            return None
        return self._normalize_release_tag(marker_text)

    def _write_runtime_assets_version_marker(self, version: str) -> None:
        marker_path = self._runtime_assets_version_marker()
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(f"{self._normalize_release_tag(version)}\n", encoding="utf-8")

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

    def _supports_output_dir(self, *, env: Optional[Dict[str, str]], base_env: Optional[Dict[str, str]]) -> bool:
        help_result = self._run_sweagent_command(["sweagent", "run", "--help"], env=env, base_env=base_env)
        if help_result.exit_code != 0:
            return False
        return "--output_dir" in help_result.output or "output_dir:" in help_result.output

    def _extract_single_trajectory_stats(
        self,
        payload: Dict[str, Any],
    ) -> ParsedStats:
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

        response = next(
            (
                text
                for text in (
                    runtime_parsing.last_nonempty_text(select_values(payload, path))
                    for path in (
                        "$.attempts[*].trajectory[*].response",
                        "$.attempts[*].trajectory[*].thought",
                        "$.attempts[*].trajectory[*].observation",
                        "$.trajectory[*].response",
                        "$.trajectory[*].thought",
                        "$.trajectory[*].observation",
                        "$.info.submission",
                    )
                )
                if text is not None
            ),
            None,
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
        return ParsedStats(
            model_name=model_name,
            usage=usage,
            llm_calls=api_calls,
            tool_calls=tool_calls,
            response=response,
        )

    def _extract_trajectory_stats(
        self,
        payloads: Optional[list[Dict[str, Any]]],
    ) -> ParsedStats:
        if not isinstance(payloads, list):
            return ParsedStats()

        parsed = [self._extract_single_trajectory_stats(payload) for payload in payloads if isinstance(payload, dict)]
        if not parsed:
            return ParsedStats()

        usage_items = [item.usage for item in parsed if item.usage is not None]
        usage = sum_usage_entries(usage_items)
        llm_call_values = [item.llm_calls for item in parsed if item.llm_calls is not None]
        llm_calls = sum(llm_call_values) if llm_call_values else None
        tool_call_values = [item.tool_calls for item in parsed if item.tool_calls is not None]
        tool_calls = sum(tool_call_values) if tool_call_values else None

        model_names = [item.model_name for item in parsed if item.model_name]
        unique_names = list(dict.fromkeys(model_names))
        model_name = unique_names[0] if len(unique_names) == 1 else None

        response_candidates = [item.response for item in parsed if item.response]
        response = response_candidates[-1] if response_candidates else None
        return ParsedStats(
            model_name=model_name,
            usage=usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            response=response,
        )

    def _extract_model_name_from_replay_config(self, replay_config: Any) -> Optional[str]:
        if isinstance(replay_config, str):
            decoded = runtime_parsing.parse_json(replay_config)
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
