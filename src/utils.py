from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


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


def extract_last_response(payloads: List[Dict[str, Any]], raw_output: str) -> Optional[str]:
    candidates: List[str] = []

    def add(value: Any) -> None:
        if not isinstance(value, str):
            return
        cleaned = value.strip()
        if cleaned:
            candidates.append(cleaned)

    def visit(obj: Any) -> None:
        if isinstance(obj, dict):
            item = obj.get("item")
            if isinstance(item, (dict, list)):
                visit(item)
            content = obj.get("content")
            if isinstance(content, list):
                for entry in content:
                    visit(entry)
            for key in ("text", "message", "content", "output", "final"):
                if key in obj:
                    add(obj.get(key))
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(obj, list):
            for entry in obj:
                visit(entry)

    for payload in payloads:
        visit(payload)

    if candidates:
        return candidates[-1]

    if raw_output:
        stdout = raw_output
        marker = "----- STDERR -----"
        if marker in stdout:
            stdout = stdout.split(marker, 1)[0]
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if lines:
            return lines[-1]

    return None
