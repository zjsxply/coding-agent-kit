"""Coding Agent Kit (cakit)."""


def main() -> int:
    from .cli.main import main as cli_main

    return cli_main()


__all__ = ["main"]
