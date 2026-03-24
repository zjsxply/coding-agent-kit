from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from src.agents.swe_agent import SweAgent


def _run(command: list[str], *, cwd):
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path):
    _run(["git", "init"], cwd=path)
    _run(["git", "config", "user.email", "tests@example.com"], cwd=path)
    _run(["git", "config", "user.name", "tests"], cwd=path)


def test_swe_agent_uses_repo_root_for_clean_git_worktree(tmp_path):
    repo_root = tmp_path / "repo"
    workdir = repo_root / "nested" / "dir"
    workdir.mkdir(parents=True)
    _init_repo(repo_root)
    (repo_root / "README.md").write_text("clean repo\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=repo_root)
    _run(["git", "commit", "-m", "Initial commit"], cwd=repo_root)

    resolved = SweAgent(workdir=workdir)._resolve_repo_path(base_env=None)

    assert resolved == repo_root.resolve()


def test_swe_agent_snapshots_dirty_git_worktree(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)
    (repo_root / "tracked.txt").write_text("before\n", encoding="utf-8")
    (repo_root / "delete-me.txt").write_text("delete me\n", encoding="utf-8")
    _run(["git", "add", "tracked.txt", "delete-me.txt"], cwd=repo_root)
    _run(["git", "commit", "-m", "Initial commit"], cwd=repo_root)

    (repo_root / "tracked.txt").write_text("after\n", encoding="utf-8")
    (repo_root / "new.txt").write_text("new file\n", encoding="utf-8")
    (repo_root / "delete-me.txt").unlink()

    resolved = SweAgent(workdir=repo_root)._resolve_repo_path(base_env=None)

    assert resolved != repo_root.resolve()
    assert resolved.is_dir()
    assert (resolved / "tracked.txt").read_text(encoding="utf-8") == "after\n"
    assert (resolved / "new.txt").read_text(encoding="utf-8") == "new file\n"
    assert not (resolved / "delete-me.txt").exists()
    status = _run(["git", "status", "--short"], cwd=resolved)
    assert status.stdout.strip() == ""


def test_swe_agent_config_uses_official_default_agent_defaults(tmp_path):
    default_config_path = tmp_path / "default.yaml"
    for bundle_name in ("registry", "edit_anthropic", "review_on_submit_m"):
        (tmp_path / "tools" / bundle_name).mkdir(parents=True)
    default_config_path.write_text(
        yaml.safe_dump(
            {
                "agent": {
                    "templates": {
                        "system_template": "official system",
                        "instance_template": "official instance",
                        "next_step_template": "official next step",
                        "next_step_no_output_template": "official no output",
                    },
                    "tools": {
                        "bundles": [
                            {"path": "tools/registry"},
                            {"path": "tools/edit_anthropic"},
                            {"path": "tools/review_on_submit_m"},
                        ],
                        "registry_variables": {
                            "USE_FILEMAP": "true",
                        },
                        "enable_bash_tool": True,
                        "parse_function": {"type": "function_calling"},
                    },
                    "history_processors": [
                        {
                            "type": "cache_control",
                            "last_n_messages": 2,
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    agent = SweAgent()
    config = agent._build_config_payload(
        tools_root=tmp_path / "tools",
        api_base=None,
        model_name="openai/test-model",
        default_config_path=default_config_path,
    )

    assert config["agent"]["templates"] == {
        "system_template": "official system",
        "instance_template": "official instance",
        "next_step_template": "official next step",
        "next_step_no_output_template": "official no output",
    }
    assert config["agent"]["tools"] == {
        "bundles": [
            {"path": str(tmp_path / "tools" / "registry")},
            {"path": str(tmp_path / "tools" / "edit_anthropic")},
            {"path": str(tmp_path / "tools" / "review_on_submit_m")},
        ],
        "registry_variables": {
            "USE_FILEMAP": "true",
        },
        "enable_bash_tool": True,
        "parse_function": {"type": "function_calling"},
    }
    assert config["agent"]["history_processors"] == [
        {
            "type": "cache_control",
            "last_n_messages": 2,
        }
    ]


def test_swe_agent_prefers_last_non_submit_observation_for_response():
    agent = SweAgent()
    payload = {
        "trajectory": [
            {
                "action": "echo \"CAKIT_HEALTHCHECK_OK\"",
                "response": "DISCUSSION\nI will print the requested text.\n\n```\necho \"CAKIT_HEALTHCHECK_OK\"\n```",
                "observation": "CAKIT_HEALTHCHECK_OK\n",
            },
            {
                "action": "submit",
                "response": "DISCUSSION\nThe task is complete, so I will submit.\n\n```\nsubmit\n```",
                "observation": "submitted",
            },
        ]
    }

    response = agent._extract_single_trajectory_stats(payload).response

    assert response == "CAKIT_HEALTHCHECK_OK"
