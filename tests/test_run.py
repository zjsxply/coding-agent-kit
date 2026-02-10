from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agents import create_agent


PROMPT = "Please reply with OK only."
TEST_DIR = Path(__file__).resolve().parent


def _assert_run_result(result) -> None:
    result_dump = json.dumps(result.to_dict(), ensure_ascii=False, default=str)
    assert result.exit_code == 0, result_dump
    assert result.models_usage, f"models_usage should not be empty; result={result_dump}"
    for model_name, usage in result.models_usage.items():
        assert model_name, f"empty model_name; result={result_dump}"
        assert isinstance(usage.get("prompt_tokens"), int), f"prompt_tokens is not int; result={result_dump}"
        assert isinstance(usage.get("completion_tokens"), int), f"completion_tokens is not int; result={result_dump}"
        assert isinstance(usage.get("total_tokens"), int), f"total_tokens is not int; result={result_dump}"
    assert result.tool_calls is not None, f"tool_calls should be present; result={result_dump}"
    assert result.llm_calls is not None, f"llm_calls should be present; result={result_dump}"
    assert result.response, f"response should be present; result={result_dump}"


def _run_and_assert_codex(use_oauth: bool) -> None:
    real_auth_path = Path.home() / ".codex" / "auth.json"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        codex_home = tmp_path / ".codex"
        codex_home.mkdir(parents=True, exist_ok=True)
        if real_auth_path.exists():
            shutil.copyfile(real_auth_path, codex_home / "auth.json")
        elif use_oauth:
            assert real_auth_path.exists(), f"missing OAuth auth file at {real_auth_path}; run `codex login`."
        env_updates = {
            "CAKIT_CODEX_USE_OAUTH": "1" if use_oauth else "0",
            "CODEX_HOME": str(codex_home),
        }
        if use_oauth:
            env_updates["CODEX_API_KEY"] = "sk-invalid-for-oauth-test"
            env_updates["OPENAI_API_KEY"] = "sk-invalid-for-oauth-test"
        with patch.dict(os.environ, env_updates):
            agent = create_agent("codex", workdir=TEST_DIR)
            config_path = agent.configure()
            assert config_path, "codex configure failed"
            result = agent.run(PROMPT)
            _assert_run_result(result)


class TestRunApi(unittest.TestCase):
    def test_codex_api_run(self) -> None:
        _run_and_assert_codex(use_oauth=False)


class TestRunOauth(unittest.TestCase):
    def test_codex_oauth_run(self) -> None:
        _run_and_assert_codex(use_oauth=True)


class TestRunClaudeApi(unittest.TestCase):
    def test_claude_api_run(self) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            self.skipTest("missing ANTHROPIC_API_KEY")
        agent = create_agent("claude", workdir=TEST_DIR)
        self.assertTrue(agent.is_installed(), "claude CLI is not installed")
        result = agent.run(PROMPT)
        _assert_run_result(result)
