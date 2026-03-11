from __future__ import annotations

import json
import sys
from typing import Any

import tomli_w
import yaml


def emit_json(payload: object) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    sys.stdout.write("\n")


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
