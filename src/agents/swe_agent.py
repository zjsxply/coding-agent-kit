from __future__ import annotations

import copy
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

import yaml

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
        python_version="3.12",
        force=True,
        with_packages=("pip", "tree-sitter==0.21.3", "tree-sitter-languages"),
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
            result = super().install(scope=scope, version=normalized_version)
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
        runtime_assets_env = self._runtime_asset_env(create_if_missing=True)
        tools_dir = runtime_assets_env.get("SWE_AGENT_TOOLS_DIR")
        if not tools_dir:
            return None
        config = self._build_config_payload(
            tools_root=Path(tools_dir),
            api_base=runtime_env.resolve_openai_base_url("SWE_AGENT_BASE_URL"),
            model_name=None,
            default_config_path=Path(runtime_assets_env["SWE_AGENT_CONFIG_DIR"]) / "default.yaml",
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
        api_base = runtime_env.resolve_openai_base_url("SWE_AGENT_BASE_URL")
        env = {
            "SWE_AGENT_API_KEY": api_key,
            "SWE_AGENT_BASE_URL": api_base,
            "OPENAI_API_KEY": api_key,
            "OPENAI_BASE_URL": api_base,
        }
        tool_bin_dir = self._installed_tool_bin_dir()
        if tool_bin_dir is not None:
            current_path = os.environ.get("PATH", "")
            env["PATH"] = (
                os.pathsep.join((str(tool_bin_dir), current_path))
                if current_path
                else str(tool_bin_dir)
            )
        env.update(self._runtime_asset_env(create_if_missing=True))
        run_home = self._make_temp_dir(prefix="cakit-sweagent-home-")
        env["HOME"] = str(run_home)
        model = runtime_env.normalize_litellm_model(
            runtime_env.resolve_openai_model("SWE_AGENT_MODEL", model_override=model_override),
            output_format="slash",
        )
        repo_path = self._resolve_repo_path(base_env=base_env)
        run_bundle_paths = self._prepare_run_tool_bundle_paths(
            tools_dir=Path(env["SWE_AGENT_TOOLS_DIR"]),
            default_config_path=Path(env["SWE_AGENT_CONFIG_DIR"]) / "default.yaml",
        )
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
                tools_root=Path(env["SWE_AGENT_TOOLS_DIR"]),
                api_base=api_base,
                model_name=model,
                default_config_path=Path(env["SWE_AGENT_CONFIG_DIR"]) / "default.yaml",
                bundle_path_overrides=run_bundle_paths,
            )
            self._write_text(config_path, dump_yaml(config))
        elif not config_path.exists():
            config = self._build_config_payload(
                tools_root=Path(env["SWE_AGENT_TOOLS_DIR"]),
                api_base=api_base,
                model_name=runtime_env.resolve_openai_model("SWE_AGENT_MODEL"),
                default_config_path=Path(env["SWE_AGENT_CONFIG_DIR"]) / "default.yaml",
                bundle_path_overrides=run_bundle_paths,
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
        tools_root: Path,
        api_base: Optional[str],
        model_name: Optional[str],
        default_config_path: Path,
        bundle_path_overrides: Optional[Dict[str, Path]] = None,
    ) -> Dict[str, Any]:
        agent_config = self._load_official_default_agent(default_config_path)
        model_config: Dict[str, Any] = {
            "name": model_name or "swe-agent-required-model",
            "per_instance_cost_limit": 0.0,
            "total_cost_limit": 0.0,
        }
        if api_base:
            model_config["api_base"] = api_base
        agent_config["model"] = model_config
        self._rewrite_tool_bundle_paths(
            agent_config=agent_config,
            tools_root=tools_root,
            bundle_path_overrides=bundle_path_overrides,
        )
        return {"agent": agent_config}

    def _load_official_default_agent(self, default_config_path: Path) -> Dict[str, Any]:
        payload = yaml.safe_load(default_config_path.read_text(encoding="utf-8"))
        agent_payload = payload.get("agent") if isinstance(payload, dict) else None
        if not isinstance(agent_payload, dict) or not agent_payload:
            raise RuntimeError(f"Failed to load official SWE-agent default agent config from {default_config_path}.")
        return copy.deepcopy(agent_payload)

    def _rewrite_tool_bundle_paths(
        self,
        *,
        agent_config: Dict[str, Any],
        tools_root: Path,
        bundle_path_overrides: Optional[Dict[str, Path]],
    ) -> None:
        tools_config = agent_config.get("tools")
        if not isinstance(tools_config, dict):
            raise RuntimeError("Official SWE-agent default config is missing agent.tools.")
        bundles = tools_config.get("bundles")
        if not isinstance(bundles, list) or not bundles:
            raise RuntimeError("Official SWE-agent default config is missing agent.tools.bundles.")

        rewritten_bundles: list[Dict[str, Any]] = []
        for bundle in bundles:
            if not isinstance(bundle, dict):
                raise RuntimeError("Official SWE-agent bundle entry is not an object.")
            bundle_path = runtime_parsing.normalize_text(bundle.get("path"))
            if bundle_path is None:
                raise RuntimeError("Official SWE-agent bundle entry is missing path.")
            resolved_bundle = (
                bundle_path_overrides.get(bundle_path)
                if isinstance(bundle_path_overrides, dict)
                else None
            )
            if resolved_bundle is None:
                resolved_bundle = self._resolve_tool_bundle_path(bundle_path=bundle_path, tools_root=tools_root)
            rewritten_bundle = copy.deepcopy(bundle)
            rewritten_bundle["path"] = str(resolved_bundle)
            rewritten_bundles.append(rewritten_bundle)
        tools_config["bundles"] = rewritten_bundles

    def _resolve_tool_bundle_path(self, *, bundle_path: str, tools_root: Path) -> Path:
        candidate = Path(bundle_path)
        if candidate.is_absolute():
            return candidate
        direct_path = tools_root / candidate
        if direct_path.exists():
            return direct_path
        named_path = tools_root / candidate.name
        if named_path.exists():
            return named_path
        raise RuntimeError(f"Failed to resolve official SWE-agent tool bundle {bundle_path!r} in {tools_root}.")

    def _prepare_run_tool_bundle_paths(
        self,
        *,
        tools_dir: Path,
        default_config_path: Path,
    ) -> Dict[str, Path]:
        agent_config = self._load_official_default_agent(default_config_path)
        tools_config = agent_config.get("tools")
        bundles = tools_config.get("bundles") if isinstance(tools_config, dict) else None
        if not isinstance(bundles, list) or not bundles:
            raise RuntimeError("Official SWE-agent default config is missing agent.tools.bundles.")

        run_bundle_root = self._make_temp_dir(prefix="cakit-sweagent-bundles-")
        suffix = run_bundle_root.name.rsplit("-", 1)[-1]
        bundle_paths: Dict[str, Path] = {}
        for bundle in bundles:
            if not isinstance(bundle, dict):
                raise RuntimeError("Official SWE-agent bundle entry is not an object.")
            bundle_path = runtime_parsing.normalize_text(bundle.get("path"))
            if bundle_path is None:
                raise RuntimeError("Official SWE-agent bundle entry is missing path.")
            source_path = self._resolve_tool_bundle_path(bundle_path=bundle_path, tools_root=tools_dir)
            target_path = run_bundle_root / f"{Path(bundle_path).name}-{suffix}"
            shutil.copytree(source_path, target_path)
            bundle_paths[bundle_path] = target_path
        return bundle_paths

    def _installed_tool_bin_dir(self) -> Optional[Path]:
        binary_path = shutil.which("sweagent")
        if not binary_path:
            return None
        shebang = runtime_parsing.first_nonempty_line(self._read_text(Path(binary_path)))
        if isinstance(shebang, str) and shebang.startswith("#!"):
            python_path = Path(shebang[2:].strip())
            if python_path.exists():
                return python_path.parent
        resolved = Path(binary_path).expanduser().resolve()
        return resolved.parent if resolved.exists() else None

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
        known_env = self._runtime_asset_env_for_versions(
            self._candidate_runtime_asset_versions(),
            create_if_missing=False,
        )
        result = self._run(["sweagent", "-h"], env=known_env or None)
        if result.exit_code != 0:
            return None
        text = runtime_parsing.first_nonempty_line(result.output)
        if text is None:
            return None
        match = re.search(r"\bv?\d+\.\d+\.\d+(?:[A-Za-z0-9.+-]*)?\b", text)
        if match:
            return match.group(0)
        return runtime_parsing.normalize_text(text)

    def _candidate_runtime_asset_versions(self) -> list[str]:
        versions: list[str] = []
        install_version = self._install_runtime_asset_version
        if install_version and install_version not in versions:
            versions.append(install_version)
        marker_version = self._read_runtime_assets_version_marker()
        if marker_version and marker_version not in versions:
            versions.append(marker_version)
        return versions

    def _runtime_asset_env(self, *, create_if_missing: bool) -> Dict[str, str]:
        versions = self._candidate_runtime_asset_versions()
        installed = self._installed_version()
        if installed and installed not in versions:
            versions.append(installed)
        return self._runtime_asset_env_for_versions(versions, create_if_missing=create_if_missing)

    def _runtime_asset_env_for_versions(self, versions: list[str], *, create_if_missing: bool) -> Dict[str, str]:
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
        if result.exit_code != 0 or result.stdout.strip().lower() != "true":
            return self._create_temporary_repo(base_env=base_env)

        repo_root_result = self._run(
            ["git", "-C", str(self.workdir), "rev-parse", "--show-toplevel"],
            base_env=base_env,
        )
        repo_root_text = runtime_parsing.normalize_text(repo_root_result.stdout) if repo_root_result.exit_code == 0 else None
        if repo_root_text is None:
            return self._create_temporary_repo(base_env=base_env)
        repo_root = Path(repo_root_text).expanduser().resolve()

        status_result = self._run(
            ["git", "-C", str(repo_root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            base_env=base_env,
        )
        if status_result.exit_code != 0:
            return repo_root
        if not status_result.stdout:
            return repo_root
        return self._create_repo_snapshot(source_root=repo_root, status_output=status_result.stdout, base_env=base_env)

    def _create_temporary_repo(self, *, base_env: Optional[Dict[str, str]]) -> Path:
        repo_path = self._make_temp_dir(prefix="cakit-swe-repo-")
        self._write_text(repo_path / "README.md", "Temporary repository for cakit swe-agent run.\n")
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

    def _create_repo_snapshot(self, *, source_root: Path, status_output: str, base_env: Optional[Dict[str, str]]) -> Path:
        snapshot_root = self._make_temp_dir(prefix="cakit-swe-repo-")
        clone_result = self._run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(source_root), str(snapshot_root)],
            base_env=base_env,
        )
        if clone_result.exit_code != 0:
            return source_root

        entries = status_output.split("\0")
        index = 0
        # Apply the current working tree state on top of a clean clone so SWE-agent sees a clean repo.
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            status_code = entry[:2]
            current_path = entry[3:] if len(entry) > 3 else ""
            previous_path = None
            if "R" in status_code or "C" in status_code:
                previous_path = current_path
                if index < len(entries):
                    current_path = entries[index]
                    index += 1
            if previous_path is not None and "R" in status_code:
                self._remove_snapshot_path(snapshot_root / previous_path)
            if not current_path:
                continue
            source_path = source_root / current_path
            target_path = snapshot_root / current_path
            if source_path.exists() or source_path.is_symlink():
                self._replace_snapshot_path(source=source_path, target=target_path)
            else:
                self._remove_snapshot_path(target_path)

        finalize_commands = [
            ["git", "-C", str(snapshot_root), "config", "user.email", "cakit@example.com"],
            ["git", "-C", str(snapshot_root), "config", "user.name", "cakit"],
            ["git", "-C", str(snapshot_root), "add", "-A"],
            ["git", "-C", str(snapshot_root), "commit", "-m", "Snapshot working tree for cakit swe-agent run"],
        ]
        for command in finalize_commands:
            result = self._run(command, base_env=base_env)
            if result.exit_code != 0 and command[-2:] != ["add", "-A"] and "nothing to commit" not in result.output.lower():
                return source_root
        return snapshot_root

    def _replace_snapshot_path(self, *, source: Path, target: Path) -> None:
        self._remove_snapshot_path(target)
        if source.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.readlink(source), target)
            return
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)

    def _remove_snapshot_path(self, path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
            return
        if path.exists() or path.is_symlink():
            path.unlink()

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

    def _extract_response_from_trajectory(self, payload: Dict[str, Any]) -> Optional[str]:
        entries = select_values(payload, "$.attempts[*].trajectory[*]")
        if entries is None:
            entries = select_values(payload, "$.trajectory[*]")
        if entries is not None:
            for entry in reversed(entries):
                if not isinstance(entry, dict):
                    continue
                action = runtime_parsing.normalize_text(entry.get("action"))
                if action is not None and action.splitlines()[0].strip().lower().startswith("submit"):
                    continue
                for key in ("observation", "response", "thought"):
                    text = runtime_parsing.normalize_text(entry.get(key))
                    if text is not None:
                        return text
        return req_str(payload, "$.info.submission")

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

        response = self._extract_response_from_trajectory(payload)

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
