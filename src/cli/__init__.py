from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "main":
        from .main import main

        return main
    raise AttributeError(name)


__all__ = ["main"]
