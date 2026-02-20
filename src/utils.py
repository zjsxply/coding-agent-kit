from __future__ import annotations

import json
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional

_STDERR_MARKER = "----- STDERR -----"


def _try_parse_json_dict_items(text: str) -> Optional[List[Dict[str, Any]]]:
    try:
        data = json.loads(text)
    except Exception:
        return None
    if isinstance(data, dict):
        items = [data]
    elif isinstance(data, list):
        items = [item for item in data if isinstance(item, dict)]
    else:
        items = []
    if not items:
        return None
    return items


def load_json_payloads(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    parsed_whole = _try_parse_json_dict_items(text.strip())
    if parsed_whole is not None:
        return parsed_whole
    payloads: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed_line = _try_parse_json_dict_items(line)
        if parsed_line is None:
            continue
        payloads.extend(parsed_line)
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


def _collect_response_candidates(obj: Any, candidates: List[str]) -> None:
    if isinstance(obj, list):
        for entry in obj:
            _collect_response_candidates(entry, candidates)
        return
    if not isinstance(obj, dict):
        return
    nested_item = obj.get("item")
    if isinstance(nested_item, (dict, list)):
        _collect_response_candidates(nested_item, candidates)
    content = obj.get("content")
    if isinstance(content, list):
        _collect_response_candidates(content, candidates)
    for key in ("text", "message", "content", "output", "final", "response", "answer"):
        if key in obj:
            value = obj.get(key)
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    candidates.append(cleaned)
    for value in obj.values():
        if isinstance(value, (dict, list)):
            _collect_response_candidates(value, candidates)


def extract_last_response(payloads: List[Dict[str, Any]], raw_output: str) -> Optional[str]:
    candidates: List[str] = []
    for payload in payloads:
        _collect_response_candidates(payload, candidates)
    if candidates:
        return candidates[-1]
    if not raw_output:
        return None
    stdout = raw_output
    if _STDERR_MARKER in stdout:
        stdout = stdout.split(_STDERR_MARKER, 1)[0]
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    return lines[-1]


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


def _append_stderr(doc: Dict[str, Any], stderr: str) -> None:
    if stderr:
        doc["stderr"] = stderr


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

    if _STDERR_MARKER not in text:
        stdout, stderr = text, ""
    else:
        stdout, stderr = text.split(_STDERR_MARKER, 1)
        stderr = stderr.strip("\n")
    stdout_stripped = stdout.strip("\n")
    doc: Dict[str, Any] = {
        "title": "Coding Agent Trace",
        "format": "text",
    }
    if source:
        doc["source"] = source

    if not stdout_stripped:
        doc["text"] = ""
        _append_stderr(doc, stderr)
        return _yaml_dump(doc)

    try:
        payload = json.loads(stdout_stripped)
        parsed_json = True
    except Exception:
        payload = None
        parsed_json = False
    if parsed_json:
        doc["format"] = "json-yaml"
        doc["item"] = payload
        _append_stderr(doc, stderr)
        return _yaml_dump(doc)

    entries: List[Dict[str, Any]] = []
    json_found = False
    for index, raw_line in enumerate(stdout.splitlines(), start=1):
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
    if json_found:
        doc["format"] = "jsonl-yaml"
        doc["entry_count"] = len(entries)
        doc["entries"] = entries
    else:
        doc["text"] = stdout_stripped
    _append_stderr(doc, stderr)
    return _yaml_dump(doc)
