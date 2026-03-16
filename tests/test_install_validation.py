from __future__ import annotations

import json
import os

from src.agents.aider import AiderAgent
from src.agents.base import CodingAgent, CommandResult, InstallStrategy
from src.agents.codex import CodexAgent
from src.agents.copilot import CopilotAgent
from src.agents.cursor import CursorAgent
from src.agents.deepagents import DeepAgentsAgent
from src.agents.factory import FactoryAgent
from src.agents.goose import GooseAgent
from src.agents.trae_oss import TraeOssAgent
from src.models import InstallResult
from src.cli import install as install_cli


class _DummyInstallAgent(CodingAgent):
    name = "dummy"
    display_name = "Dummy"
    binary = None

    def __init__(self, *, strategies, observed_versions):
        super().__init__()
        self.install_strategy = strategies
        self._observed_versions = list(observed_versions)
        self.install_calls = 0

    def _install_with_custom_strategy(self, *, scope: str, version: str | None) -> CommandResult:
        self.install_calls += 1
        return CommandResult(
            exit_code=0,
            stdout=f"attempt-{self.install_calls}",
            stderr="",
            duration_seconds=0.0,
        )

    def get_version(self) -> str | None:
        index = self.install_calls - 1
        if index < 0 or index >= len(self._observed_versions):
            return None
        return self._observed_versions[index]


def test_install_retries_next_strategy_when_version_verification_fails():
    agent = _DummyInstallAgent(
        strategies=(
            InstallStrategy(kind="custom"),
            InstallStrategy(kind="custom"),
        ),
        observed_versions=(None, "1.2.3"),
    )

    result = agent.install(version="1.2.3")

    assert result.ok is True
    assert result.version == "1.2.3"
    assert agent.install_calls == 2


def test_install_fails_when_observed_version_does_not_match_requested_version():
    agent = _DummyInstallAgent(
        strategies=InstallStrategy(kind="custom"),
        observed_versions=("9.9.9",),
    )

    result = agent.install(version="1.2.3")

    assert result.ok is False
    assert result.version is None
    assert agent.install_calls == 1
    assert result.details is not None
    assert "did not match requested version" in result.details


def test_python_build_runtime_packages_cover_alpine_native_extension_builds():
    assert install_cli.system_runtime_package_names("python-build", "apk") == [
        "gcc",
        "musl-dev",
        "python3-dev",
        "linux-headers",
    ]


def test_aider_runtime_dependencies_include_python_build_and_uv():
    assert AiderAgent().runtime_dependencies() == ("python-build", "uv")


def test_install_strategy_minimum_node_version_uses_maximum_declared_requirement():
    agent = _DummyInstallAgent(
        strategies=(
            InstallStrategy(kind="npm", package="one", minimum_node_version=(16, 0, 0)),
            InstallStrategy(kind="shell"),
            InstallStrategy(kind="npm", package="two", minimum_node_version=(20, 0, 0)),
        ),
        observed_versions=(),
    )

    assert agent.minimum_node_version() == (20, 0, 0)


def test_node_tools_ready_accepts_existing_node_when_no_minimum_version_is_requested(monkeypatch):
    monkeypatch.setattr(install_cli, "_candidate_runtime_binary", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(install_cli, "_installed_node_version", lambda: (16, 20, 2))

    assert install_cli._node_tools_ready() is True
    assert install_cli._node_tools_ready(minimum_version=(20, 0, 0)) is False


def test_ensure_dependencies_passes_agent_specific_node_minimum(monkeypatch):
    agent = _DummyInstallAgent(
        strategies=InstallStrategy(kind="npm", package="@openai/codex", minimum_node_version=(16, 0, 0)),
        observed_versions=(),
    )
    captured: dict[str, object] = {}

    def fake_ensure_runtime_dependencies(runtimes, *, minimum_node_version=None, output_stream=None):
        del output_stream
        captured["runtimes"] = tuple(runtimes)
        captured["minimum_node_version"] = minimum_node_version
        return {"node": True}

    monkeypatch.setattr(install_cli, "create_agent", lambda name: agent)
    monkeypatch.setattr(install_cli, "ensure_runtime_dependencies", fake_ensure_runtime_dependencies)

    assert install_cli.ensure_dependencies("dummy") is True
    assert captured == {
        "runtimes": ("node",),
        "minimum_node_version": (16, 0, 0),
    }


def test_codex_declares_upstream_minimum_node_requirement():
    assert CodexAgent().minimum_node_version() == (16, 0, 0)


class _FakeUrlopenResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class _FakeBytesResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._payload


def test_aider_builds_alpine_tree_sitter_workaround_requirements(monkeypatch):
    payload = {
        "info": {
            "requires_dist": [
                "aiohttp==3.12.15",
                'tree-sitter==0.24.0; python_version >= "3.10"',
                "tree-sitter-yaml==0.7.1",
                "tree-sitter-language-pack==0.9.0",
                'pytest==8.4.1; extra == "dev"',
            ]
        }
    }
    monkeypatch.setattr(
        "src.agents.aider.urlrequest.urlopen",
        lambda url, timeout=30: _FakeUrlopenResponse(payload),
    )

    requirements = AiderAgent()._build_alpine_tree_sitter_workaround_requirements(version="0.86.1")

    assert requirements == [
        "aiohttp==3.12.15",
        "tree-sitter-language-pack==0.13.0",
    ]


def test_aider_only_retries_alpine_workaround_for_tree_sitter_musl_failure(monkeypatch):
    monkeypatch.setattr("src.agents.aider.shutil.which", lambda name: "/sbin/apk" if name == "apk" else None)
    result = InstallResult(
        agent="aider",
        version=None,
        ok=False,
        details=(
            "tree-sitter-language-pack==0.9.0\n"
            "The built wheel is not compatible with the current Python on musllinux x86_64\n"
        ),
        config_path=None,
    )

    should_retry = AiderAgent()._should_retry_alpine_tree_sitter_install(result=result, version="0.86.1")

    assert should_retry is True


def test_copilot_reads_version_from_npm_package_json(monkeypatch, tmp_path):
    package_dir = tmp_path / "node_modules" / "@github" / "copilot"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(json.dumps({"version": "0.0.400"}), encoding="utf-8")
    loader_path = package_dir / "npm-loader.js"
    loader_path.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    symlink_path = tmp_path / "copilot"
    symlink_path.symlink_to(loader_path)

    monkeypatch.setattr(
        "src.agents.base.runtime_command.resolve_binary",
        lambda **kwargs: os.fspath(symlink_path),
    )

    assert CopilotAgent().get_version() == "0.0.400"


def test_manifest_version_lookup_also_supports_non_npm_package_roots(monkeypatch, tmp_path):
    package_dir = tmp_path / "cursor-agent" / "versions" / "2026.01.28-fd13201"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(json.dumps({"version": "2026.01.28-fd13201"}), encoding="utf-8")
    binary_path = package_dir / "cursor-agent"
    binary_path.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(
        "src.agents.base.runtime_command.resolve_binary",
        lambda **kwargs: os.fspath(binary_path),
    )

    class _BinaryManifestAgent(CodingAgent):
        name = "manifest-agent"
        display_name = "Manifest Agent"
        binary = "manifest-agent"

    assert _BinaryManifestAgent().get_version() == "2026.01.28-fd13201"


def test_cursor_reads_bundled_release_id_when_package_json_has_no_version(monkeypatch, tmp_path):
    install_dir = tmp_path / "cursor-agent"
    install_dir.mkdir(parents=True)
    (install_dir / "package.json").write_text(json.dumps({"name": "@anysphere/agent-cli-runtime"}), encoding="utf-8")
    (install_dir / "972.index.js").write_text('globalThis.SENTRY_RELEASE={id:"agent-cli@2026.01.28-fd13201"}\n', encoding="utf-8")
    binary_path = install_dir / "cursor-agent"
    binary_path.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(
        "src.agents.cursor.runtime_command.resolve_binary",
        lambda **kwargs: os.fspath(binary_path),
    )

    assert CursorAgent().get_version() == "2026.01.28-fd13201"


def test_deepagents_builds_alpine_sqlite_vec_workaround_requirements(monkeypatch):
    payload = {
        "info": {
            "version": "0.0.15",
            "requires_dist": [
                "deepagents==0.3.9",
                "langgraph-checkpoint-sqlite<4.0.0,>=3.0.0",
                "rich>=13.0.0",
                'langchain-google-vertexai>=3.0.0; extra == "vertexai"',
            ],
        }
    }
    monkeypatch.setattr(
        "src.agents.deepagents.urlrequest.urlopen",
        lambda url, timeout=30: _FakeUrlopenResponse(payload),
    )

    resolved_version, requirements, sqlite_checkpoint_requirement = DeepAgentsAgent()._build_alpine_sqlite_vec_requirements(
        version="0.0.15"
    )

    assert resolved_version == "0.0.15"
    assert requirements == ["deepagents==0.3.9", "rich>=13.0.0"]
    assert sqlite_checkpoint_requirement == "langgraph-checkpoint-sqlite<4.0.0,>=3.0.0"


def test_factory_installs_alpine_glibc_compatibility_before_binary_install(monkeypatch, tmp_path):
    commands: list[list[str]] = []

    def fake_urlopen(url, timeout=30):
        del timeout
        raw_url = url.full_url if hasattr(url, "full_url") else str(url)
        payload = b"key" if raw_url.endswith(".pub") else b"apk"
        return _FakeBytesResponse(payload)

    def fake_run(self, args, **kwargs):
        del kwargs
        commands.append(list(args))
        return CommandResult(exit_code=0, stdout="", stderr="", duration_seconds=0.0)

    monkeypatch.setattr(FactoryAgent, "_should_install_alpine_glibc_compat", staticmethod(lambda: True))
    monkeypatch.setattr("src.agents.factory.os.geteuid", lambda: 0)
    monkeypatch.setattr("src.agents.factory.shutil.which", lambda name: "/sbin/apk" if name == "apk" else None)
    def fake_mkdtemp(prefix=""):
        del prefix
        staging_root = tmp_path / "glibc"
        staging_root.mkdir(parents=True, exist_ok=True)
        return str(staging_root)

    monkeypatch.setattr("src.agents.factory.tempfile.mkdtemp", fake_mkdtemp)
    monkeypatch.setattr("src.agents.factory.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(FactoryAgent, "_run", fake_run)

    result = FactoryAgent()._ensure_alpine_glibc_compat()

    assert result is not None
    assert result.exit_code == 0
    assert commands == [
        ["/sbin/apk", "add", "--no-cache", "ca-certificates"],
        ["mkdir", "-p", "/etc/apk/keys"],
        ["cp", str(tmp_path / "glibc" / "sgerrand.rsa.pub"), "/etc/apk/keys/sgerrand.rsa.pub"],
        ["/sbin/apk", "add", "--no-cache", str(tmp_path / "glibc" / "glibc-2.35-r1.apk")],
        ["mkdir", "-p", "/lib64"],
        ["ln", "-sf", "/usr/glibc-compat/lib64/ld-linux-x86-64.so.2", "/lib64/ld-linux-x86-64.so.2"],
    ]


def test_goose_reads_version_from_installed_binary_when_cli_cannot_execute(monkeypatch, tmp_path):
    binary_path = tmp_path / "goose"
    binary_path.write_bytes(b"\x00goose Version:1.22.0Paths:\x00")

    monkeypatch.setattr(
        "src.agents.goose.runtime_command.resolve_binary",
        lambda **kwargs: os.fspath(binary_path),
    )

    agent = GooseAgent()
    monkeypatch.setattr(
        agent,
        "_run",
        lambda args, **kwargs: CommandResult(exit_code=127, stdout="", stderr="not found", duration_seconds=0.0),
    )

    assert agent.get_version() == "1.22.0"


def test_trae_oss_reads_uv_receipt_by_matching_installed_entrypoint_path(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    tool_dir = tmp_path / "tools" / "trae-agent"
    bin_dir.mkdir(parents=True)
    tool_dir.mkdir(parents=True)
    binary_path = bin_dir / "trae-cli"
    binary_path.write_text("#!/bin/sh\n", encoding="utf-8")
    receipt_path = tool_dir / "uv-receipt.toml"
    receipt_path.write_text(
        """
[tool]
requirements = [
    { name = "trae-agent", git = "https://github.com/bytedance/trae-agent.git?rev=e839e559ac61bdd0e057c375dd1dee391fee797d" },
]
entrypoints = [
    { name = "trae-cli", install-path = "%s", from = "trae-agent" },
]
"""
        % os.fspath(binary_path),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "src.agents.trae_oss.runtime_command.resolve_binary",
        lambda **kwargs: os.fspath(binary_path),
    )
    monkeypatch.setenv("UV_TOOL_DIR", os.fspath(tmp_path / "tools"))

    assert TraeOssAgent().get_version() == "e839e559ac61bdd0e057c375dd1dee391fee797d"


def test_trae_oss_reads_commit_from_direct_url_for_unversioned_uv_git_install(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    tool_dir = tmp_path / "tools" / "trae-agent"
    dist_info_dir = tool_dir / "lib" / "python3.12" / "site-packages" / "trae_agent-0.1.0.dist-info"
    bin_dir.mkdir(parents=True)
    dist_info_dir.mkdir(parents=True)
    binary_path = bin_dir / "trae-cli"
    binary_path.write_text("#!/bin/sh\n", encoding="utf-8")
    (tool_dir / "uv-receipt.toml").write_text(
        """
[tool]
requirements = [
    { name = "trae-agent", git = "https://github.com/bytedance/trae-agent.git" },
]
entrypoints = [
    { name = "trae-cli", install-path = "%s", from = "trae-agent" },
]
"""
        % os.fspath(binary_path),
        encoding="utf-8",
    )
    (dist_info_dir / "direct_url.json").write_text(
        json.dumps(
            {
                "url": "https://github.com/bytedance/trae-agent.git",
                "vcs_info": {
                    "vcs": "git",
                    "commit_id": "e839e559ac61bdd0e057c375dd1dee391fee797d",
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "src.agents.trae_oss.runtime_command.resolve_binary",
        lambda **kwargs: os.fspath(binary_path),
    )
    monkeypatch.setenv("UV_TOOL_DIR", os.fspath(tmp_path / "tools"))

    assert TraeOssAgent().get_version() == "e839e559ac61bdd0e057c375dd1dee391fee797d"


def test_install_all_preinstalls_dependencies_before_parallel_agent_install(monkeypatch):
    emitted_payloads: list[dict] = []
    install_calls: list[tuple[str, bool]] = []
    runtime_batches: list[tuple[str, ...]] = []

    class _FakeRuntimeAgent:
        def __init__(self, runtimes):
            self._runtimes = runtimes

        def runtime_dependencies(self):
            return self._runtimes

        def minimum_node_version(self):
            return None

    monkeypatch.setattr(install_cli, "_resolve_targets_or_emit", lambda agent_name: ["alpha", "beta"])
    monkeypatch.setattr(
        install_cli,
        "create_agent",
        lambda agent_name: _FakeRuntimeAgent(("python-build", "node") if agent_name == "alpha" else ("uv",)),
    )
    monkeypatch.setattr(
        install_cli,
        "ensure_runtime_dependencies",
        lambda runtimes, minimum_node_version=None, output_stream=None: (
            runtime_batches.append(tuple(runtimes))
            or {"python-build": True, "node": True, "uv": False}
        ),
    )
    monkeypatch.setattr(
        install_cli,
        "_run_targets",
        lambda targets, target_runner, parallel: [target_runner(target) for target in targets],
    )
    monkeypatch.setattr(
        install_cli,
        "install_agent",
        lambda agent_name, scope, version=None, output_stream=None, skip_dependencies=False: (
            install_calls.append((agent_name, skip_dependencies))
            or InstallResult(
                agent=agent_name,
                version="1.0.0",
                ok=True,
                details=None,
                config_path=None,
            )
        ),
    )
    monkeypatch.setattr("src.cli.install.emit_json", lambda payload: emitted_payloads.append(payload))

    exit_code = install_cli.run_install_command("all", scope="user", version=None)

    assert exit_code == 1
    assert runtime_batches == [("python-build", "node", "uv")]
    assert install_calls == [("alpha", True)]
    assert emitted_payloads == [
        {
            "agent": "all",
            "resolved_agents": ["alpha", "beta"],
            "ok": False,
            "parallel": True,
            "successful_agents": ["alpha"],
            "failed_agents": ["beta"],
            "results": [
                {
                    "agent": "alpha",
                    "ok": True,
                    "version": "1.0.0",
                    "config_path": None,
                    "details": None,
                },
                {
                    "agent": "beta",
                    "ok": False,
                    "version": None,
                    "config_path": None,
                    "details": "dependency install failed",
                },
            ],
        }
    ]


def test_ensure_runtime_dependencies_batches_system_package_install(monkeypatch):
    installed_binaries: set[str] = set()
    package_installs: list[list[str]] = []

    monkeypatch.setattr(install_cli, "detect_package_manager", lambda: "dnf")
    monkeypatch.setattr(
        "src.cli.install.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in installed_binaries else None,
    )

    def fake_install_system_packages(packages, *, quiet_success=False, output_stream=None, refresh_package_index=True):
        del quiet_success, output_stream, refresh_package_index
        package_installs.append(list(packages))
        installed_binaries.update({"curl", "git"})
        return True

    monkeypatch.setattr(install_cli, "install_system_packages_linux", fake_install_system_packages)
    monkeypatch.setattr(install_cli, "ensure_node_tools", lambda **kwargs: True)
    monkeypatch.setattr(install_cli, "ensure_modern_cmake", lambda **kwargs: True)
    monkeypatch.setattr("src.cli.install.runtime_install.resolve_uv_binary", lambda: "/usr/local/bin/uv")
    monkeypatch.setattr(install_cli, "_cmake_ready", lambda: True)
    monkeypatch.setattr(install_cli, "_installed_cmake_version", lambda: (3, 30, 0))

    statuses = install_cli.ensure_runtime_dependencies(
        ("curl", "git", "python-build", "libgomp", "uv", "cmake")
    )

    assert package_installs == [["curl", "git-core", "gcc", "glibc-devel", "python3-devel", "libgomp"]]
    assert statuses == {
        "curl": True,
        "git": True,
        "python-build": True,
        "libgomp": True,
        "uv": True,
        "cmake": True,
    }


def test_deepagents_reads_version_from_installed_dist_info_when_cli_version_probe_fails(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    site_packages_dir = tmp_path / "tools" / "deepagents-cli" / "lib" / "python3.12" / "site-packages"
    dist_info_dir = site_packages_dir / "deepagents_cli-0.0.12.dist-info"
    bin_dir.mkdir(parents=True)
    dist_info_dir.mkdir(parents=True)
    binary_path = bin_dir / "deepagents"
    tool_binary_path = tmp_path / "tools" / "deepagents-cli" / "bin" / "deepagents"
    tool_binary_path.parent.mkdir(parents=True)
    tool_binary_path.write_text("#!/bin/sh\n", encoding="utf-8")
    binary_path.symlink_to(tool_binary_path)
    (dist_info_dir / "METADATA").write_text("Name: deepagents-cli\nVersion: 0.0.12\n", encoding="utf-8")

    monkeypatch.setattr(
        "src.agents.deepagents.runtime_command.resolve_binary",
        lambda **kwargs: os.fspath(binary_path),
    )

    agent = DeepAgentsAgent()
    monkeypatch.setattr(
        agent,
        "_run",
        lambda args, **kwargs: CommandResult(exit_code=127, stdout="", stderr="not found", duration_seconds=0.0),
    )

    assert agent.get_version() == "0.0.12"
