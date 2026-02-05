from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agents import create_agent


PROMPT = "Please reply with OK only."
TEST_DIR = Path(__file__).resolve().parent


def _run_and_assert(use_oauth: bool) -> None:
    with patch.dict(os.environ, {"CODEX_USE_OAUTH": "1" if use_oauth else "0"}):
        agent = create_agent("codex", workdir=TEST_DIR)
        config_path = agent.configure()
        assert config_path, "codex configure failed"
        result = agent.run(PROMPT)
        result_dump = json.dumps(result.to_dict(), ensure_ascii=False, default=str)
        assert result.exit_code == 0, result_dump
        assert result.models_usage, f"models_usage should not be empty; result={result_dump}"
        for model_name, usage in result.models_usage.items():
            assert model_name, f"empty model_name; result={result_dump}"
            assert isinstance(
                usage.get("prompt_tokens"), int
            ), f"prompt_tokens is not int; result={result_dump}"
            assert isinstance(
                usage.get("completion_tokens"), int
            ), f"completion_tokens is not int; result={result_dump}"
            assert isinstance(
                usage.get("total_tokens"), int
            ), f"total_tokens is not int; result={result_dump}"
        assert result.tool_calls is not None, f"tool_calls should be present; result={result_dump}"
        assert result.llm_calls is not None, f"llm_calls should be present; result={result_dump}"
        assert result.response, f"response should be present; result={result_dump}"


class TestApiRun(unittest.TestCase):
    def test_codex_api_run(self) -> None:
        _run_and_assert(use_oauth=False)


class TestOauthRun(unittest.TestCase):
    def test_codex_oauth_run(self) -> None:
        _run_and_assert(use_oauth=True)
