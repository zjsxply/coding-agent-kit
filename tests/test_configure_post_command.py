from pathlib import Path

from src.cli import install as install_cli


class _DummyAgent:
    def __init__(self, config_path: Path | None) -> None:
        self._config_path = config_path

    def configure(self) -> str | None:
        if self._config_path is None:
            return None
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text("base = true\n", encoding="utf-8")
        return str(self._config_path)


def test_configure_target_runs_post_command(monkeypatch, tmp_path):
    config_path = tmp_path / "codex" / "config.toml"
    monkeypatch.setattr(install_cli, "create_agent", lambda target: _DummyAgent(config_path))
    monkeypatch.setenv(
        "CAKIT_CONFIGURE_POST_COMMAND",
        "printf '%s|%s' \"$CAKIT_CONFIGURE_AGENT\" \"$CAKIT_CONFIG_PATH\" > \"$CAKIT_CONFIG_DIR/post-hook.txt\"",
    )

    ok, payload = install_cli._configure_target("codex")

    assert ok is True
    assert payload["config_path"] == str(config_path)
    assert payload["post_config_exit_code"] == 0
    assert (config_path.parent / "post-hook.txt").read_text(encoding="utf-8") == f"codex|{config_path}"


def test_configure_target_surfaces_post_command_failure(monkeypatch, tmp_path):
    config_path = tmp_path / "codex" / "config.toml"
    monkeypatch.setattr(install_cli, "create_agent", lambda target: _DummyAgent(config_path))
    monkeypatch.setenv("CAKIT_CONFIGURE_POST_COMMAND", "printf 'broken'; exit 7")

    ok, payload = install_cli._configure_target("codex")

    assert ok is False
    assert payload["config_path"] == str(config_path)
    assert payload["post_config_exit_code"] == 7
    assert payload["post_config_output"] == "broken"
    assert payload["details"] == "post-config command failed with exit code 7"
