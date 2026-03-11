from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from ..stats_extract import select_values


STDERR_MARKER = "----- STDERR -----"


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


def first_nonempty_line(text: Optional[str]) -> Optional[str]:
    if not isinstance(text, str):
        return None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            return line
    return None


def normalize_text(value: Optional[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except Exception:
        return None


def stdout_only(output: str) -> str:
    if STDERR_MARKER in output:
        return output.split(STDERR_MARKER, 1)[0]
    return output


def last_stdout_line(output: str, *, skip_prefixes: tuple[str, ...] = ()) -> Optional[str]:
    lines = [line.strip() for line in stdout_only(output).splitlines() if line.strip()]
    if skip_prefixes:
        lines = [line for line in lines if not any(line.startswith(prefix) for prefix in skip_prefixes)]
    if not lines:
        return None
    return lines[-1]


def last_nonempty_text(values: Optional[list[Any]]) -> Optional[str]:
    if values is None:
        return None
    for value in reversed(values):
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def extract_content_text(content: Any, *, allow_scalars: bool = False) -> Optional[str]:
    if isinstance(content, str):
        cleaned = content.strip()
        return cleaned or None
    if not isinstance(content, list):
        return None
    text_parts = [
        text
        for text in (
            normalize_text(item)
            for item in (select_values(content, '$[?(@.type == "text")].text') or [])
        )
        if text is not None
    ]
    if text_parts:
        return "\n".join(text_parts)
    if not allow_scalars:
        return None
    scalar_parts = [
        text
        for text in (
            normalize_text(item)
            for item in (select_values(content, "$[*]") or [])
        )
        if text is not None
    ]
    if not scalar_parts:
        return None
    return "\n".join(scalar_parts)


def extract_content_texts(value: Any, path: str, *, allow_scalars: bool = False) -> list[str]:
    extracted: list[str] = []
    for content in select_values(value, path) or []:
        text = extract_content_text(content, allow_scalars=allow_scalars)
        if text is not None:
            extracted.append(text)
    return extracted


def parse_json(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except Exception:
        return None


def parse_json_dict(text: str) -> Optional[Dict[str, Any]]:
    parsed = parse_json(text)
    if not isinstance(parsed, dict):
        return None
    return parsed


def load_json_payloads(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    parsed_whole = _try_parse_json_dict_items(text.strip())
    if parsed_whole is not None:
        return parsed_whole
    payloads: List[Dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parsed_line = _try_parse_json_dict_items(line)
        if parsed_line is None:
            continue
        payloads.extend(parsed_line)
    return payloads


def _collect_response_candidates(obj: Any, candidates: list[str]) -> None:
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


def extract_last_response(payloads: list[Dict[str, Any]], raw_output: str) -> Optional[str]:
    candidates: list[str] = []
    for payload in payloads:
        _collect_response_candidates(payload, candidates)
    if candidates:
        return candidates[-1]
    stdout = stdout_only(raw_output)
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    return lines[-1]


def load_json(
    path: Path,
    *,
    read_text: Optional[Callable[[Path], Optional[str]]] = None,
) -> Optional[Any]:
    text_loader = read_text or (lambda p: p.read_text(encoding="utf-8") if p.exists() else None)
    text = text_loader(path)
    if text is None:
        return None
    return parse_json(text)


def load_json_dict(
    path: Path,
    *,
    read_text: Optional[Callable[[Path], Optional[str]]] = None,
) -> Optional[Dict[str, Any]]:
    text_loader = read_text or (lambda p: p.read_text(encoding="utf-8") if p.exists() else None)
    text = text_loader(path)
    if text is None:
        return None
    return parse_json_dict(text)


def run_json_dict_command(
    *,
    args: Iterable[str],
    run: Callable[..., Any],
    env: Optional[Dict[str, str]] = None,
    base_env: Optional[Dict[str, str]] = None,
    stdout_only_output: bool = False,
) -> Optional[Dict[str, Any]]:
    result = run(args, env=env, base_env=base_env)
    if getattr(result, "exit_code", 1) != 0:
        return None
    output = result.output if stdout_only_output else result.stdout
    text = stdout_only(output) if stdout_only_output else output
    return parse_json_dict((text or "").strip())


def load_output_json_payloads(output: str, *, stdout_only_output: bool = True) -> list[Dict[str, Any]]:
    text = stdout_only(output) if stdout_only_output else output
    return load_json_payloads(text)


def extract_last_json_value(text: str) -> Optional[Any]:
    decoder = json.JSONDecoder()
    last_value_obj: Optional[Any] = None
    index = 0
    while index < len(text):
        char = text[index]
        if char not in {"{", "["}:
            index += 1
            continue
        try:
            value, end = decoder.raw_decode(text, index)
        except Exception:
            index += 1
            continue
        if isinstance(value, (dict, list)):
            last_value_obj = value
        index = end
    return last_value_obj


def parse_output_json(output: str) -> Optional[Any]:
    stdout = stdout_only(output).strip()
    if not stdout:
        return None
    return extract_last_json_value(stdout)


def parse_output_json_object(output: str) -> Optional[Dict[str, Any]]:
    parsed = parse_output_json(output)
    if not isinstance(parsed, dict):
        return None
    return parsed
