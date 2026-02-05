from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from src.agents import create_agent, list_agents


TEST_DIR = Path(__file__).resolve().parent
IMAGE_1 = TEST_DIR / "image1.png"
IMAGE_2 = TEST_DIR / "image2.png"
PROMPT = "这两张图片分别有啥？"

IMAGE_SUPPORT = {"codex", "gemini", "qwen"}


def _run_cli(agent: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    cmd = [
        sys.executable,
        "-m",
        "src.cli",
        "run",
        agent,
        PROMPT,
        "--image",
        f"{IMAGE_1},{IMAGE_2}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"non-JSON output: {result.stdout}\n{result.stderr}") from exc
    return result, payload


class TestImageInput(unittest.TestCase):
    def test_codex_cli_image_input(self) -> None:
        agent = create_agent("codex", workdir=TEST_DIR)
        self.assertTrue(agent.is_installed(), "codex CLI is not installed")
        result, payload = _run_cli("codex")
        self.assertEqual(result.returncode, 0, f"{result.stdout}\n{result.stderr}")
        self.assertEqual(payload.get("exit_code"), 0, payload.get("raw_output"))

    def test_agent_image_input(self) -> None:
        for name in list_agents():
            with self.subTest(agent=name):
                agent = create_agent(name, workdir=TEST_DIR)
                if name in IMAGE_SUPPORT:
                    self.assertTrue(agent.is_installed(), f"{name} CLI is not installed")
                run_result = agent.run(PROMPT, images=[IMAGE_1, IMAGE_2])
                expected_exit = 0 if name in IMAGE_SUPPORT else 2
                self.assertEqual(run_result.exit_code, expected_exit, run_result.raw_output)
