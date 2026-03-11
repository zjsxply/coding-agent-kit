from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

import yaml

from .parsing import STDERR_MARKER


class _TraceDumper(yaml.SafeDumper):
    pass


def _represent_multiline_str(dumper: yaml.SafeDumper, data: str) -> yaml.nodes.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_TraceDumper.add_representer(str, _represent_multiline_str)


def _yaml_dump(data: dict[str, object]) -> str:
    return yaml.dump(
        data,
        Dumper=_TraceDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000000,
    ).rstrip() + "\n"


def _append_stderr(doc: dict[str, object], stderr: str) -> None:
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

    if STDERR_MARKER not in text:
        stdout, stderr = text, ""
    else:
        stdout, stderr = text.split(STDERR_MARKER, 1)
        stderr = stderr.strip("\n")
    stdout_stripped = stdout.strip("\n")
    doc: dict[str, object] = {
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

    entries: list[dict[str, object]] = []
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


def build_trajectory_content(
    *,
    output: str,
    source: str,
    attachments: Optional[list[tuple[str, Path]]] = None,
    read_text: Optional[Callable[[Path], Optional[str]]] = None,
) -> str:
    if not attachments:
        return format_trace_text(output, source=source)

    text_loader = read_text or (lambda p: p.read_text(encoding="utf-8") if p.exists() else None)
    parts = [output]
    for label, path in attachments:
        text = text_loader(path)
        if not text or not text.strip():
            continue
        parts.append(f"----- {label} ({path}) -----\n{text}")
    return format_trace_text("\n\n".join(parts), source=source)


def build_trajectory_from_raw(
    *,
    raw_text: Optional[str],
    output: str,
    source: str,
) -> str:
    if raw_text and raw_text.strip():
        return format_trace_text(raw_text, source=source)
    return format_trace_text(output, source=source)
