from __future__ import annotations

import json
from typing import Any, Dict, List


def load_json_payloads(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    stripped = text.strip()
    if stripped:
        try:
            data = json.loads(stripped)
        except Exception:
            data = None
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    payloads: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict):
            payloads.append(data)
        elif isinstance(data, list):
            payloads.extend([item for item in data if isinstance(item, dict)])
    return payloads
