from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class SweAgent(CodingAgent):
    name = "swe-agent"
    display_name = "SWE-agent"
    binary = "sweagent"

    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        result = self._run(["sweagent", "--version"], env=self._runtime_asset_env(create_if_missing=True))
        return result.exit_code == 0 and bool(result.output.strip())

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        version = self._resolve_version(version)
        url = f"https://github.com/SWE-agent/SWE-agent/archive/refs/tags/{version}.tar.gz"
        if self._ensure_uv():
            result = self._run(["uv", "pip", "install", url])
        else:
            result = self._run(["python", "-m", "pip", "install", "--no-cache-dir", url])
        assets_ok = self._prepare_runtime_assets(version)
        config_path = self.configure()
        ok = result.exit_code == 0 and assets_ok
        details = result.output
        if result.exit_code == 0 and not assets_ok:
            details = f"{details}\n[install] failed to prepare SWE-agent runtime assets."
        return InstallResult(
            agent=self.name,
            version=version,
            ok=ok,
            details=details,
            config_path=config_path,
        )

    def configure(self) -> Optional[str]:
        runtime_env = self._runtime_asset_env(create_if_missing=True)
        tools_dir = runtime_env.get("SWE_AGENT_TOOLS_DIR")
        if not tools_dir:
            return None

        def yaml_quote(value: str) -> str:
            return json.dumps(value)

        config = (
            "agent:\n"
            "  templates:\n"
            "    system_template: |-\n"
            "      You are a helpful assistant that can interact with a computer to solve tasks.\n"
            "    instance_template: |-\n"
            "      {{problem_statement}}\n"
            "  tools:\n"
            "    bundles:\n"
            f"      - path: {yaml_quote(str(Path(tools_dir) / 'registry'))}\n"
            f"      - path: {yaml_quote(str(Path(tools_dir) / 'submit'))}\n"
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
        env = {
            "SWE_AGENT_API_KEY": os.environ.get("SWE_AGENT_API_KEY"),
            "SWE_AGENT_API_BASE": os.environ.get("SWE_AGENT_API_BASE"),
            "OPENAI_API_KEY": os.environ.get("SWE_AGENT_API_KEY"),
            "OPENAI_API_BASE": os.environ.get("SWE_AGENT_API_BASE"),
            "OPENAI_BASE_URL": os.environ.get("SWE_AGENT_API_BASE"),
        }
        env.update(self._runtime_asset_env(create_if_missing=True))
        model = model_override or os.environ.get("SWE_AGENT_MODEL")
        if isinstance(model, str):
            model = model.strip() or None
        self._cleanup_local_tools_root()
        repo_path = self._resolve_repo_path(base_env=base_env)
        output_dir = Path(tempfile.mkdtemp(prefix="cakit-sweagent-"))
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

        trajectory_file = self._find_latest_trajectory_file(output_dir)
        trajectory_payload = self._load_trajectory_payload(trajectory_file)
        usage = self._extract_usage_from_trajectory(trajectory_payload)
        llm_calls = self._extract_llm_calls_from_trajectory(trajectory_payload)
        tool_calls = self._extract_tool_calls_from_trajectory(trajectory_payload)
        model_name = self._extract_model_name_from_trajectory(trajectory_payload)
        response = self._extract_response(output, trajectory_payload)

        output_path = self._write_output(self.name, output)
        models_usage = self._ensure_models_usage({}, usage, model_name)
        trajectory_content = self._format_trajectory_trace(
            trajectory_file=trajectory_file,
            raw_output=output,
            output_path=output_path,
        )
        trajectory_path = self._write_trajectory(self.name, trajectory_content)
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
            response=response,
            exit_code=run_exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["sweagent", "--version"], env=self._runtime_asset_env(create_if_missing=False))
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _resolve_version(self, requested: Optional[str]) -> str:
        if requested:
            normalized = requested.strip()
            if normalized:
                return normalized
        configured = os.environ.get("CAKIT_SWE_AGENT_VERSION")
        if configured:
            return configured
        url = "https://api.github.com/repos/SWE-agent/SWE-agent/releases/latest"
        request = urllib.request.Request(url, headers=self._github_headers())
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        tag = (payload.get("tag_name") or "").strip()
        if not tag:
            raise RuntimeError("Failed to resolve latest SWE-agent release tag from GitHub.")
        os.environ["CAKIT_SWE_AGENT_VERSION"] = tag
        return tag

    def _github_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("CAKIT_SWE_AGENT_GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _runtime_asset_env(self, *, create_if_missing: bool) -> Dict[str, str]:
        versions: list[str] = []
        configured = os.environ.get("CAKIT_SWE_AGENT_VERSION")
        if isinstance(configured, str):
            configured = configured.strip()
            if configured:
                versions.append(configured)

        installed = self._installed_package_version()
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

    def _installed_package_version(self) -> Optional[str]:
        try:
            return metadata.version("sweagent")
        except Exception:
            return None

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

        root = paths["root"]
        root.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz") as archive:
                for member in archive.getmembers():
                    parts = member.name.split("/", 1)
                    if len(parts) != 2:
                        continue
                    relative = parts[1]
                    if not (
                        relative == "config"
                        or relative.startswith("config/")
                        or relative == "tools"
                        or relative.startswith("tools/")
                        or relative == "trajectories"
                        or relative.startswith("trajectories/")
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
        return self._runtime_assets_ready(paths)

    def _resolve_repo_path(self, *, base_env: Optional[Dict[str, str]]) -> Path:
        if self._is_git_repository(self.workdir, base_env=base_env):
            return self.workdir

        repo_path = Path(tempfile.mkdtemp(prefix="cakit-swe-repo-"))
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

    def _is_git_repository(self, path: Path, *, base_env: Optional[Dict[str, str]]) -> bool:
        result = self._run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            base_env=base_env,
        )
        if result.exit_code != 0:
            return False
        return result.stdout.strip().lower() == "true"

    def _cleanup_local_tools_root(self) -> None:
        tools_root = Path("/root/tools")
        if tools_root.exists():
            shutil.rmtree(tools_root, ignore_errors=True)

    def _run_sweagent_command(
        self, args: list[str], *, env: Optional[Dict[str, str]], base_env: Optional[Dict[str, str]]
    ):
        original_workdir = self.workdir
        self.workdir = Path("/")
        try:
            return self._run(args, env=env, base_env=base_env)
        finally:
            self.workdir = original_workdir

    def _find_latest_trajectory_file(self, output_dir: Path) -> Optional[Path]:
        if not output_dir.exists():
            return None
        candidates = [path for path in output_dir.rglob("*.traj") if path.is_file()]
        if not candidates:
            return None
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return candidates[0]

    def _load_trajectory_payload(self, trajectory_file: Optional[Path]) -> Optional[Dict[str, Any]]:
        if trajectory_file is None or not trajectory_file.exists():
            return None
        try:
            data = json.loads(trajectory_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        if isinstance(data, dict):
            return data
        return None

    def _extract_usage_from_trajectory(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        model_stats = self._extract_model_stats(payload)
        if model_stats is None:
            return None
        prompt_tokens = model_stats["tokens_sent"]
        completion_tokens = model_stats["tokens_received"]
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def _extract_llm_calls_from_trajectory(self, payload: Optional[Dict[str, Any]]) -> Optional[int]:
        model_stats = self._extract_model_stats(payload)
        if model_stats is None:
            return None
        return model_stats["api_calls"]

    def _extract_tool_calls_from_trajectory(self, payload: Optional[Dict[str, Any]]) -> Optional[int]:
        if not isinstance(payload, dict):
            return None

        attempts = payload.get("attempts")
        if isinstance(attempts, list) and attempts:
            total = 0
            for attempt in attempts:
                if not isinstance(attempt, dict):
                    return None
                count = self._count_trajectory_actions(attempt.get("trajectory"))
                if count is None:
                    return None
                total += count
            return total

        return self._count_trajectory_actions(payload.get("trajectory"))

    def _extract_response(self, output: str, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        response = self._extract_response_from_trajectory(payload)
        if response:
            return response
        stdout = self._stdout_only(output)
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if lines:
            return lines[-1]
        return None

    def _extract_response_from_trajectory(self, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(payload, dict):
            return None

        attempts = payload.get("attempts")
        if isinstance(attempts, list):
            for attempt in reversed(attempts):
                if not isinstance(attempt, dict):
                    continue
                response = self._extract_response_from_steps(attempt.get("trajectory"))
                if response:
                    return response

        response = self._extract_response_from_steps(payload.get("trajectory"))
        if response:
            return response

        info = payload.get("info")
        if isinstance(info, dict):
            submission = info.get("submission")
            if isinstance(submission, str):
                cleaned = submission.strip()
                if cleaned:
                    return cleaned
        return None

    def _extract_response_from_steps(self, steps: Any) -> Optional[str]:
        if not isinstance(steps, list):
            return None
        for step in reversed(steps):
            if not isinstance(step, dict):
                continue
            for key in ("response", "thought", "observation"):
                value = step.get(key)
                if not isinstance(value, str):
                    continue
                cleaned = value.strip()
                if cleaned:
                    return cleaned
        return None

    def _extract_model_stats(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        if not isinstance(payload, dict):
            return None

        info = payload.get("info")
        if isinstance(info, dict):
            stats = self._parse_model_stats_dict(info.get("model_stats"))
            if stats is not None:
                return stats

        attempts = payload.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            return None

        total = {
            "tokens_sent": 0,
            "tokens_received": 0,
            "api_calls": 0,
        }
        for attempt in attempts:
            if not isinstance(attempt, dict):
                return None
            attempt_info = attempt.get("info")
            if not isinstance(attempt_info, dict):
                return None
            stats = self._parse_model_stats_dict(attempt_info.get("model_stats"))
            if stats is None:
                return None
            total["tokens_sent"] += stats["tokens_sent"]
            total["tokens_received"] += stats["tokens_received"]
            total["api_calls"] += stats["api_calls"]
        return total

    def _parse_model_stats_dict(self, raw: Any) -> Optional[Dict[str, int]]:
        if not isinstance(raw, dict):
            return None
        tokens_sent = self._as_int(raw.get("tokens_sent"))
        tokens_received = self._as_int(raw.get("tokens_received"))
        api_calls = self._as_int(raw.get("api_calls"))
        if tokens_sent is None or tokens_received is None or api_calls is None:
            return None
        return {
            "tokens_sent": tokens_sent,
            "tokens_received": tokens_received,
            "api_calls": api_calls,
        }

    def _count_trajectory_actions(self, trajectory: Any) -> Optional[int]:
        if not isinstance(trajectory, list):
            return None
        count = 0
        for step in trajectory:
            if not isinstance(step, dict):
                return None
            action = step.get("action")
            if action is None:
                continue
            if not isinstance(action, str):
                return None
            if action.strip():
                count += 1
        return count

    def _extract_model_name_from_trajectory(self, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(payload, dict):
            return None

        model_name = self._extract_model_name_from_replay_config(payload.get("replay_config"))
        if model_name:
            return model_name

        attempts = payload.get("attempts")
        if not isinstance(attempts, list):
            return None
        for attempt in reversed(attempts):
            if not isinstance(attempt, dict):
                continue
            model_name = self._extract_model_name_from_replay_config(attempt.get("replay_config"))
            if model_name:
                return model_name
        return None

    def _extract_model_name_from_replay_config(self, replay_config: Any) -> Optional[str]:
        parsed: Optional[Dict[str, Any]] = None
        if isinstance(replay_config, str):
            try:
                decoded = json.loads(replay_config)
            except Exception:
                return None
            if isinstance(decoded, dict):
                parsed = decoded
        elif isinstance(replay_config, dict):
            parsed = replay_config

        if parsed is None:
            return None

        agent = parsed.get("agent")
        if isinstance(agent, dict):
            model = agent.get("model")
            if isinstance(model, dict):
                name = model.get("name")
                if isinstance(name, str):
                    cleaned = name.strip()
                    if cleaned:
                        return cleaned

        agent_configs = parsed.get("agent_configs")
        if not isinstance(agent_configs, list):
            return None
        names: list[str] = []
        for agent_config in agent_configs:
            if not isinstance(agent_config, dict):
                continue
            model = agent_config.get("model")
            if not isinstance(model, dict):
                continue
            name = model.get("name")
            if not isinstance(name, str):
                continue
            cleaned = name.strip()
            if cleaned:
                names.append(cleaned)
        unique_names = list(dict.fromkeys(names))
        if len(unique_names) == 1:
            return unique_names[0]
        return None

    def _format_trajectory_trace(self, *, trajectory_file: Optional[Path], raw_output: str, output_path: Path) -> str:
        if trajectory_file is not None and trajectory_file.exists():
            try:
                trajectory_raw = trajectory_file.read_text(encoding="utf-8")
            except Exception:
                trajectory_raw = ""
            if trajectory_raw.strip():
                return format_trace_text(trajectory_raw, source=str(trajectory_file))
        return format_trace_text(raw_output, source=str(output_path))
