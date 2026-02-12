from __future__ import annotations

import json
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_STDERR_MARKER = "----- STDERR -----"


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


def load_env_file(path: Path) -> Dict[str, str]:
    content = path.read_text(encoding="utf-8")
    env: Dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        env[key] = value
    return env


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


class _TraceDumper(yaml.SafeDumper):
    pass


def _represent_multiline_str(dumper: yaml.SafeDumper, data: str) -> yaml.nodes.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_TraceDumper.add_representer(str, _represent_multiline_str)


def _yaml_dump(data: Dict[str, Any]) -> str:
    return yaml.dump(
        data,
        Dumper=_TraceDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000000,
    ).rstrip() + "\n"


def _split_stdout_stderr(text: str) -> Tuple[str, str]:
    if _STDERR_MARKER not in text:
        return text, ""
    stdout, stderr = text.split(_STDERR_MARKER, 1)
    return stdout, stderr.strip("\n")


def _parse_jsonl_entries(text: str) -> Tuple[List[Dict[str, Any]], bool]:
    entries: List[Dict[str, Any]] = []
    json_found = False
    for index, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except Exception:
            entries.append({"index": index, "type": "text", "text": raw_line})
            continue
        entries.append({"index": index, "type": "json", "item": parsed})
        json_found = True
    return entries, json_found


def format_trace_text(
    text: str,
    *,
    source: Optional[str] = None,
) -> str:
    if text is None:
        return ""
    stripped = text.strip("\n")
    if not stripped:
        return ""

    stdout, stderr = _split_stdout_stderr(text)
    stdout_stripped = stdout.strip("\n")
    doc: Dict[str, Any] = {
        "title": "Coding Agent Trace",
        "format": "text",
    }
    if source:
        doc["source"] = source

    if not stdout_stripped:
        doc["text"] = ""
        if stderr:
            doc["stderr"] = stderr
        return _yaml_dump(doc)

    parse_ok = False
    payload: Any = None
    try:
        payload = json.loads(stdout_stripped)
        parse_ok = True
    except Exception:
        parse_ok = False

    if parse_ok:
        doc["format"] = "json-yaml"
        doc["item"] = payload
        if stderr:
            doc["stderr"] = stderr
        return _yaml_dump(doc)

    entries, json_found = _parse_jsonl_entries(stdout)
    if json_found:
        doc["format"] = "jsonl-yaml"
        doc["entry_count"] = len(entries)
        doc["entries"] = entries
    else:
        doc["text"] = stdout_stripped
    if stderr:
        doc["stderr"] = stderr
    return _yaml_dump(doc)
