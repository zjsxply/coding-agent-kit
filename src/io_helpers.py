from __future__ import annotations

import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import fcntl
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

import tomli_w
import yaml

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def emit_json(payload: object) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    sys.stdout.write("\n")


@contextmanager
def file_lock(name: str):
    with _thread_locks_guard:
        thread_lock = _thread_locks.setdefault(name, threading.Lock())

    with thread_lock:
        if fcntl is None:
            yield
            return
        lock_root = Path("/tmp") / "cakit-locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_path = lock_root / f"{name}.lock"
        with lock_path.open("w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def dump_toml(payload: dict[str, Any]) -> str:
    content = tomli_w.dumps(payload)
    if content.endswith("\n"):
        return content
    return f"{content}\n"


def dump_yaml(payload: Any) -> str:
    content = yaml.safe_dump(
        payload,
        allow_unicode=False,
        default_flow_style=False,
        sort_keys=False,
    )
    if content.endswith("\n"):
        return content
    return f"{content}\n"
