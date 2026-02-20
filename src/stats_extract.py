from __future__ import annotations

import json
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, Literal, Optional, Union


@dataclass(frozen=True)
class StatsSnapshot:
    models_usage: Dict[str, Dict[str, int]]
    llm_calls: Optional[int]
    tool_calls: Optional[int]
    total_cost: Optional[float] = None


@dataclass(frozen=True)
class StatsArtifacts:
    raw_output: str = ""
    json_payload: Optional[Any] = None
    jsonl_payloads: tuple[Dict[str, Any], ...] = ()
    result_payload: Optional[Dict[str, Any]] = None
    session_payload: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class UsagePattern:
    prompt_field: "JsonPathSpec"
    completion_field: "JsonPathSpec"
    total_field: Optional["JsonPathSpec"] = None


@dataclass(frozen=True)
class UsageFieldSpec:
    prompt_required_fields: tuple["JsonPathSpec", ...] = ()
    completion_required_fields: tuple["JsonPathSpec", ...] = ()
    prompt_optional_fields: tuple["JsonPathSpec", ...] = ()
    completion_optional_fields: tuple["JsonPathSpec", ...] = ()
    total_field: Optional["JsonPathSpec"] = None


@dataclass(frozen=True)
class LlmCall:
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    call_count: int = 1
    cost: Optional[float] = None


@dataclass(frozen=True)
class ToolCall:
    call_count: int = 1


_MISSING = object()
_INVALID = object()
_WILDCARD = object()

JsonPathSegment = Union[str, int]
JsonPath = tuple[JsonPathSegment, ...]
JsonPathSpec = Union[JsonPathSegment, JsonPath]


@dataclass(frozen=True)
class _JsonFilterExpr:
    path: JsonPath
    operator: str
    literal: Any


_DEFAULT_USAGE_PATTERNS: tuple[UsagePattern, ...] = (
    UsagePattern(
        prompt_field="prompt_tokens",
        completion_field="completion_tokens",
        total_field="total_tokens",
    ),
    UsagePattern(
        prompt_field="input_tokens",
        completion_field="output_tokens",
        total_field="total_tokens",
    ),
    UsagePattern(
        prompt_field="prompt",
        completion_field="candidates",
        total_field="total",
    ),
)

_DEFAULT_USAGE_MODEL_ORDER: tuple[str, ...] = (
    "prompt_completion",
    "input_output",
    "prompt_candidates",
)


def _path_segment_to_json_path(segment: JsonPathSegment) -> Optional[str]:
    if isinstance(segment, str):
        if not segment:
            return None
        if segment.startswith("$"):
            return None
        if segment.isidentifier():
            return f".{segment}"
        return f"[{json.dumps(segment, ensure_ascii=True)}]"
    if isinstance(segment, int):
        if segment < 0:
            return None
        return f"[{segment}]"
    return None


def _json_path(path: JsonPathSpec) -> Optional[str]:
    if isinstance(path, str) and path.startswith("$"):
        return path
    segments: JsonPath
    if isinstance(path, tuple):
        segments = path
    else:
        segments = (path,)
    candidate = "$"
    for segment in segments:
        rendered = _path_segment_to_json_path(segment)
        if rendered is None:
            return None
        candidate += rendered
    return candidate


def _find_closing_bracket(path: str, *, start: int) -> int:
    quote: Optional[str] = None
    escape = False
    paren_depth = 0
    for cursor in range(start + 1, len(path)):
        char = path[cursor]
        if quote is not None:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == "(":
            paren_depth += 1
            continue
        if char == ")" and paren_depth > 0:
            paren_depth -= 1
            continue
        if char == "]" and paren_depth == 0:
            return cursor
    return -1


def _parse_filter_literal(raw: str) -> Any:
    token = raw.strip()
    if not token:
        return _INVALID
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        try:
            return json.loads(token)
        except Exception:
            return _INVALID
    if token.startswith("'") and token.endswith("'") and len(token) >= 2:
        return token[1:-1]
    if token == "true":
        return True
    if token == "false":
        return False
    if token == "null":
        return None
    try:
        return int(token)
    except Exception:
        pass
    try:
        return float(token)
    except Exception:
        return _INVALID


def _find_filter_operator(expr: str) -> Optional[tuple[int, str]]:
    quote: Optional[str] = None
    escape = False
    for cursor, char in enumerate(expr):
        if quote is not None:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if expr.startswith("==", cursor):
            return cursor, "=="
        if expr.startswith("!=", cursor):
            return cursor, "!="
    return None


def _parse_filter(token: str) -> Optional[_JsonFilterExpr]:
    if not token.startswith("?(") or not token.endswith(")"):
        return None
    expression = token[2:-1].strip()
    if not expression:
        return None
    operator_info = _find_filter_operator(expression)
    if operator_info is None:
        return None
    cursor, operator = operator_info
    lhs = expression[:cursor].strip()
    rhs = expression[cursor + len(operator) :].strip()
    if not lhs or not rhs:
        return None
    lhs_token = lhs.strip()
    if lhs_token == "@":
        filter_path: Optional[JsonPath] = ()
    elif not lhs_token.startswith("@"):
        filter_path = None
    else:
        parsed_path = _parse_json_path(f"${lhs_token[1:]}")
        if parsed_path is None or any(
            segment is _WILDCARD or isinstance(segment, _JsonFilterExpr) for segment in parsed_path
        ):
            filter_path = None
        else:
            filter_path = parsed_path  # type: ignore[assignment]
    if filter_path is None:
        return None
    literal = _parse_filter_literal(rhs)
    if literal is _INVALID:
        return None
    return _JsonFilterExpr(path=filter_path, operator=operator, literal=literal)


@lru_cache(maxsize=4096)
def _parse_json_path(path: str) -> Optional[tuple[Any, ...]]:
    if path == "$":
        return ()
    if not path.startswith("$"):
        return None
    cursor = 1
    segments: list[Any] = []
    while cursor < len(path):
        char = path[cursor]
        if char == ".":
            cursor += 1
            start = cursor
            while cursor < len(path) and path[cursor] not in ".[":
                cursor += 1
            token = path[start:cursor]
            if not token:
                return None
            segments.append(token)
            continue
        if char == "[":
            end = _find_closing_bracket(path, start=cursor)
            if end < 0:
                return None
            token = path[cursor + 1 : end].strip()
            if not token:
                return None
            if token == "*":
                segments.append(_WILDCARD)
            elif token.startswith("?(") and token.endswith(")"):
                parsed_filter = _parse_filter(token)
                if parsed_filter is None:
                    return None
                segments.append(parsed_filter)
            elif token.startswith("'") and token.endswith("'") and len(token) >= 2:
                segments.append(token[1:-1])
            elif token.startswith('"') and token.endswith('"') and len(token) >= 2:
                segments.append(token[1:-1])
            elif token.isdigit():
                segments.append(int(token))
            else:
                return None
            cursor = end + 1
            continue
        return None
    return tuple(segments)


def _dig(value: Any, path: JsonPath) -> Any:
    current = value
    for segment in path:
        if isinstance(segment, str):
            if not isinstance(current, dict) or segment not in current:
                return _MISSING
            current = current[segment]
            continue
        if not isinstance(current, list):
            return _MISSING
        if segment < 0 or segment >= len(current):
            return _MISSING
        current = current[segment]
    return current


def _select(value: Any, path: tuple[Any, ...]) -> list[Any]:
    values: list[Any] = [value]
    for segment in path:
        next_values: list[Any] = []
        for current in values:
            if segment is _WILDCARD:
                if isinstance(current, list):
                    next_values.extend(current)
                    continue
                if isinstance(current, dict):
                    next_values.extend(current.values())
                continue
            if isinstance(segment, _JsonFilterExpr):
                iterable: list[Any] = []
                if isinstance(current, list):
                    iterable = list(current)
                elif isinstance(current, dict):
                    iterable = list(current.values())
                if not iterable:
                    continue
                for item in iterable:
                    target = _dig(item, segment.path)
                    if target is _MISSING:
                        continue
                    if segment.operator == "==" and target == segment.literal:
                        next_values.append(item)
                        continue
                    if segment.operator == "!=" and target != segment.literal:
                        next_values.append(item)
                continue
            if isinstance(segment, str):
                if isinstance(current, dict) and segment in current:
                    next_values.append(current[segment])
                continue
            if isinstance(segment, int):
                if isinstance(current, list) and 0 <= segment < len(current):
                    next_values.append(current[segment])
                continue
        if not next_values:
            return []
        values = next_values
    return values


def select_values(value: Any, path: str) -> Optional[list[Any]]:
    parsed = _parse_json_path(path)
    if parsed is None:
        return None
    values = _select(value, parsed)
    if not values:
        return None
    return values


def get_path_value(value: Any, path: str) -> Any:
    parsed = _parse_json_path(path)
    if parsed is None:
        return _MISSING
    if any(segment is _WILDCARD or isinstance(segment, _JsonFilterExpr) for segment in parsed):
        values = _select(value, parsed)
        if not values:
            return _MISSING
        return values
    return _dig(value, parsed)  # type: ignore[arg-type]


def _normalize_nonempty_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned


def _normalize_optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return value


def _normalize_optional_cost(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    return float(value)


def req_int(value: Any, path: str) -> Optional[int]:
    found = get_path_value(value, path)
    if found is _MISSING:
        return None
    return _normalize_optional_int(found)


def req_str(value: Any, path: str) -> Optional[str]:
    found = get_path_value(value, path)
    if found is _MISSING:
        return None
    return _normalize_nonempty_text(found)


def opt_float(value: Any, path: str) -> Optional[float]:
    found = get_path_value(value, path)
    if found is _MISSING:
        return None
    return _normalize_optional_cost(found)


def sum_int(value: Any, path: str) -> Optional[int]:
    values = select_values(value, path)
    if values is None:
        return None
    total = 0
    for item in values:
        normalized = _normalize_optional_int(item)
        if normalized is None:
            return None
        total += normalized
    return total


def last_value(value: Any, path: str) -> Any:
    values = select_values(value, path)
    if values is None:
        return None
    return values[-1]


def _read_path_value(value: Any, path: JsonPathSpec) -> Any:
    json_path = _json_path(path)
    if json_path is None:
        return _MISSING
    selected = select_values(value, json_path)
    if selected is None:
        return _MISSING
    return selected[-1]


def _read_required_int(value: Any, path: JsonPathSpec) -> Optional[int]:
    raw = _read_path_value(value, path)
    if raw is _MISSING:
        return None
    return _normalize_optional_int(raw)


def _read_optional_int(value: Any, path: JsonPathSpec) -> Any:
    raw = _read_path_value(value, path)
    if raw is _MISSING:
        return _MISSING
    if raw is None:
        return None
    parsed = _normalize_optional_int(raw)
    if parsed is None:
        return _INVALID
    return parsed


def _extract_usage_entry(value: Any) -> Optional[Dict[str, int]]:
    if not isinstance(value, dict):
        return None
    prompt_tokens = req_int(value, "$.prompt_tokens")
    completion_tokens = req_int(value, "$.completion_tokens")
    total_tokens = req_int(value, "$.total_tokens")
    if prompt_tokens is None or completion_tokens is None or total_tokens is None:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def build_stats_snapshot(
    *,
    models_usage: Any,
    llm_calls: Any,
    tool_calls: Any,
    total_cost: Any = None,
) -> Optional[StatsSnapshot]:
    parsed_models_usage = models_usage if isinstance(models_usage, dict) else {}
    parsed_llm_calls = _normalize_optional_int(llm_calls)
    parsed_tool_calls = _normalize_optional_int(tool_calls)
    parsed_total_cost = _normalize_optional_cost(total_cost)
    snapshot = StatsSnapshot(
        models_usage=parsed_models_usage,
        llm_calls=parsed_llm_calls,
        tool_calls=parsed_tool_calls,
        total_cost=parsed_total_cost,
    )
    if (
        not snapshot.models_usage
        and snapshot.llm_calls is None
        and snapshot.tool_calls is None
        and snapshot.total_cost is None
    ):
        return None
    return snapshot


def _extract_usage_with_pattern(raw: Dict[str, Any], pattern: UsagePattern) -> Optional[Dict[str, int]]:
    prompt_tokens = _read_required_int(raw, pattern.prompt_field)
    completion_tokens = _read_required_int(raw, pattern.completion_field)
    if prompt_tokens is None or completion_tokens is None:
        return None

    return _resolve_usage_entry(
        raw,
        total_field=pattern.total_field,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _sum_required_int_fields(raw: Dict[str, Any], fields: tuple[JsonPathSpec, ...]) -> Optional[int]:
    total = 0
    for field in fields:
        value = _read_required_int(raw, field)
        if value is None:
            return None
        total += value
    return total


def _sum_optional_int_fields(raw: Dict[str, Any], fields: tuple[JsonPathSpec, ...]) -> Any:
    total = 0
    for field in fields:
        value = _read_optional_int(raw, field)
        if value is _INVALID:
            return _INVALID
        if value is _MISSING or value is None:
            continue
        total += value
    return total


def _resolve_total_tokens(
    raw: Dict[str, Any],
    *,
    total_field: Optional[JsonPathSpec],
    prompt_tokens: int,
    completion_tokens: int,
) -> Any:
    if total_field is None:
        return prompt_tokens + completion_tokens
    total_candidate = _read_optional_int(raw, total_field)
    if total_candidate is _INVALID:
        return _INVALID
    if total_candidate is _MISSING or total_candidate is None:
        return prompt_tokens + completion_tokens
    return total_candidate


def _resolve_usage_entry(
    raw: Dict[str, Any],
    *,
    total_field: Optional[JsonPathSpec],
    prompt_tokens: int,
    completion_tokens: int,
) -> Optional[Dict[str, int]]:
    total_tokens = _resolve_total_tokens(
        raw,
        total_field=total_field,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    if total_tokens is _INVALID:
        return None
    return _compose_usage_entry(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _compose_usage_entry(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> Optional[Dict[str, int]]:
    if (
        not isinstance(prompt_tokens, int)
        or isinstance(prompt_tokens, bool)
        or not isinstance(completion_tokens, int)
        or isinstance(completion_tokens, bool)
        or not isinstance(total_tokens, int)
        or isinstance(total_tokens, bool)
    ):
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _extract_usage_with_field_spec(raw: Dict[str, Any], field_spec: UsageFieldSpec) -> Optional[Dict[str, int]]:
    prompt_required = _sum_required_int_fields(raw, field_spec.prompt_required_fields)
    completion_required = _sum_required_int_fields(raw, field_spec.completion_required_fields)
    if prompt_required is None or completion_required is None:
        return None

    prompt_optional = _sum_optional_int_fields(raw, field_spec.prompt_optional_fields)
    completion_optional = _sum_optional_int_fields(raw, field_spec.completion_optional_fields)
    if prompt_optional is _INVALID or completion_optional is _INVALID:
        return None

    prompt_tokens = prompt_required + prompt_optional
    completion_tokens = completion_required + completion_optional
    return _resolve_usage_entry(
        raw,
        total_field=field_spec.total_field,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


_USAGE_PATTERNS_BY_MODEL: Dict[str, UsagePattern] = {
    "prompt_completion": _DEFAULT_USAGE_PATTERNS[0],
    "input_output": _DEFAULT_USAGE_PATTERNS[1],
    "prompt_candidates": _DEFAULT_USAGE_PATTERNS[2],
    "tokens_in_out": UsagePattern(
        prompt_field="tokensIn",
        completion_field="tokensOut",
        total_field="total_tokens",
    ),
    "accumulated_input_output": UsagePattern(
        prompt_field="accumulated_input_tokens",
        completion_field="accumulated_output_tokens",
        total_field="accumulated_total_tokens",
    ),
    "input_output_short": UsagePattern(
        prompt_field="input",
        completion_field="output",
        total_field="total",
    ),
    "qoder_total": UsagePattern(
        prompt_field="total_prompt_tokens",
        completion_field="total_completed_tokens",
        total_field="total_tokens",
    ),
}


_INPUT_OTHER_OUTPUT_USAGE_SPEC = UsageFieldSpec(
    prompt_required_fields=("input_other", "input_cache_read", "input_cache_creation"),
    completion_required_fields=("output",),
)


_USAGE_FIELD_SPECS_BY_MODEL: Dict[str, UsageFieldSpec] = {
    "input_other_output": _INPUT_OTHER_OUTPUT_USAGE_SPEC,
    "factory": UsageFieldSpec(
        prompt_required_fields=("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"),
        completion_required_fields=("output_tokens",),
    ),
    "openclaw": UsageFieldSpec(
        prompt_optional_fields=("input", "cacheRead", "cacheWrite"),
        completion_optional_fields=("output",),
        total_field="total",
    ),
    "input_other_output_delta": _INPUT_OTHER_OUTPUT_USAGE_SPEC,
    "claude_model_usage": UsageFieldSpec(
        prompt_required_fields=("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"),
        completion_required_fields=("outputTokens",),
    ),
    "qoder_stream": UsageFieldSpec(
        prompt_required_fields=("input_tokens",),
        prompt_optional_fields=("cache_read_tokens",),
        completion_required_fields=("output_tokens",),
    ),
    "opencode": UsageFieldSpec(
        prompt_required_fields=("input", ("cache", "read"), ("cache", "write")),
        completion_required_fields=("output", "reasoning"),
        total_field="total",
    ),
}


def parse_usage_by_model(raw: Any, model_name: str) -> Optional[Dict[str, int]]:
    if not isinstance(raw, dict):
        return None

    pattern = _USAGE_PATTERNS_BY_MODEL.get(model_name)
    if pattern is not None:
        return _extract_usage_with_pattern(raw, pattern)

    field_spec = _USAGE_FIELD_SPECS_BY_MODEL.get(model_name)
    if field_spec is not None:
        return _extract_usage_with_field_spec(raw, field_spec)
    return None


def _extract_usage(
    raw: Any,
    *,
    patterns: tuple[UsagePattern, ...] = _DEFAULT_USAGE_PATTERNS,
) -> Optional[Dict[str, int]]:
    if not isinstance(raw, dict):
        return None
    if patterns == _DEFAULT_USAGE_PATTERNS:
        for model_name in _DEFAULT_USAGE_MODEL_ORDER:
            parsed = parse_usage_by_model(raw, model_name)
            if parsed is not None:
                return parsed
    for pattern in patterns:
        parsed = _extract_usage_with_pattern(raw, pattern)
        if parsed is not None:
            return parsed
    return None


def _merge_usage(models_usage: Dict[str, Dict[str, int]], model_name: str, usage: Dict[str, int]) -> None:
    merge_model_usage(models_usage, model_name, usage)


def merge_model_usage(models_usage: Dict[str, Dict[str, int]], model_name: str, usage: Dict[str, int]) -> bool:
    normalized_model_name = _normalize_nonempty_text(model_name)
    usage_entry = _extract_usage_entry(usage)
    if normalized_model_name is None or usage_entry is None:
        return False
    entry = models_usage.setdefault(
        normalized_model_name,
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )
    entry["prompt_tokens"] += usage_entry["prompt_tokens"]
    entry["completion_tokens"] += usage_entry["completion_tokens"]
    entry["total_tokens"] += usage_entry["total_tokens"]
    return True


def build_llm_call(
    *,
    model: Any,
    usage: Any,
    call_count: Any = 1,
    cost: Any = None,
) -> Optional[LlmCall]:
    model_name = _normalize_nonempty_text(model)
    usage_entry = _extract_usage_entry(usage)
    normalized_call_count = _normalize_optional_int(call_count)
    normalized_cost = _normalize_optional_cost(cost)
    if model_name is None or usage_entry is None:
        return None
    if normalized_call_count is None or normalized_call_count < 1:
        return None
    if cost is not None and normalized_cost is None:
        return None
    return LlmCall(
        model=model_name,
        prompt_tokens=usage_entry["prompt_tokens"],
        completion_tokens=usage_entry["completion_tokens"],
        total_tokens=usage_entry["total_tokens"],
        call_count=normalized_call_count,
        cost=normalized_cost,
    )


def build_stats_snapshot_from_events(
    *,
    llm_events: Iterable[LlmCall],
    tool_events: Iterable[ToolCall],
    total_cost: Optional[float] = None,
) -> Optional[StatsSnapshot]:
    models_usage: Dict[str, Dict[str, int]] = {}
    llm_calls = 0
    tool_calls = 0
    has_tool_calls = False
    running_cost = _normalize_optional_cost(total_cost)

    for event in llm_events:
        call_count = _normalize_optional_int(event.call_count)
        if call_count is None or call_count < 1:
            continue
        llm_calls += call_count

        model_name = _normalize_nonempty_text(event.model)
        usage = _compose_usage_entry(
            prompt_tokens=event.prompt_tokens,
            completion_tokens=event.completion_tokens,
            total_tokens=event.total_tokens,
        )
        if model_name is not None and usage is not None:
            _merge_usage(
                models_usage,
                model_name,
                {
                    "prompt_tokens": usage["prompt_tokens"] * call_count,
                    "completion_tokens": usage["completion_tokens"] * call_count,
                    "total_tokens": usage["total_tokens"] * call_count,
                },
            )
        if event.cost is not None:
            event_cost = _normalize_optional_cost(event.cost)
            if event_cost is None:
                continue
            if running_cost is None:
                running_cost = 0.0
            running_cost += event_cost

    for event in tool_events:
        call_count = _normalize_optional_int(event.call_count)
        if call_count is None:
            continue
        has_tool_calls = True
        tool_calls += call_count

    return build_stats_snapshot(
        models_usage=models_usage,
        llm_calls=llm_calls if llm_calls > 0 else None,
        tool_calls=tool_calls if has_tool_calls else None,
        total_cost=running_cost,
    )


def _artifact_dict(artifacts: StatsArtifacts, source_field: str) -> Optional[Dict[str, Any]]:
    payload = getattr(artifacts, source_field, None)
    if not isinstance(payload, dict):
        return None
    return payload


def _count_tool_calls(payload: Any) -> int:
    total = 0
    stack: list[Any] = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, list):
            for item in current:
                if isinstance(item, (dict, list)):
                    stack.append(item)
            continue
        if not isinstance(current, dict):
            continue
        tool_calls = current.get("tool_calls", _MISSING)
        if isinstance(tool_calls, list):
            total += len(tool_calls)
        elif isinstance(tool_calls, dict):
            total += len(tool_calls)
        for value in current.values():
            if isinstance(value, (dict, list)):
                stack.append(value)
    return total


def extract_gemini_style_stats(
    artifacts: StatsArtifacts,
    *,
    source_field: str = "result_payload",
) -> Optional[StatsSnapshot]:
    payload = _artifact_dict(artifacts, source_field)
    if payload is None:
        return None
    stats = last_value(payload, "$.stats")
    if not isinstance(stats, dict):
        return None
    models = last_value(stats, "$.models")
    if not isinstance(models, dict):
        return None

    models_usage: Dict[str, Dict[str, int]] = {}
    llm_calls = 0
    has_llm_calls = False
    for raw_model_name, model_stats in models.items():
        if not isinstance(model_stats, dict):
            continue
        model_calls = sum_int(model_stats, "$.api.totalRequests")
        if model_calls is not None:
            llm_calls += model_calls
            has_llm_calls = True

        model_name = _normalize_nonempty_text(raw_model_name)
        prompt_tokens = sum_int(model_stats, "$.tokens.prompt")
        completion_tokens = sum_int(model_stats, "$.tokens.candidates")
        total_tokens = sum_int(model_stats, "$.tokens.total")
        usage = (
            _compose_usage_entry(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
            if prompt_tokens is not None and completion_tokens is not None and total_tokens is not None
            else None
        )
        if model_name is not None and usage is not None:
            _merge_usage(
                models_usage,
                model_name,
                usage,
            )

    tool_calls = sum_int(stats, "$.tools.totalCalls")
    return build_stats_snapshot(
        models_usage=models_usage,
        llm_calls=llm_calls if has_llm_calls else None,
        tool_calls=tool_calls,
    )


def extract_json_result_stats(
    artifacts: StatsArtifacts,
    *,
    inner: Callable[[StatsArtifacts], Optional[StatsSnapshot]],
    result_type: str = "result",
) -> Optional[StatsSnapshot]:
    result_payload = artifacts.result_payload
    if result_payload is None:
        if isinstance(artifacts.json_payload, dict) and req_str(artifacts.json_payload, "$.type") == result_type:
            result_payload = artifacts.json_payload
        else:
            quoted = json.dumps(result_type, ensure_ascii=True)
            selected = last_value(artifacts.json_payload, f"$[?(@.type == {quoted})]")
            if isinstance(selected, dict):
                result_payload = selected
    if result_payload is None and artifacts.jsonl_payloads:
        quoted = json.dumps(result_type, ensure_ascii=True)
        selected = last_value(list(artifacts.jsonl_payloads), f"$[?(@.type == {quoted})]")
        if isinstance(selected, dict):
            result_payload = selected
    if result_payload is None:
        return None
    patched = replace(
        artifacts,
        result_payload=result_payload,
        json_payload=result_payload,
    )
    return inner(patched)


def extract_jsonl_stats(
    artifacts: StatsArtifacts,
    *,
    source_field: str = "jsonl_payloads",
    model_field: str = "$.model",
    usage_field: str = "$.usage",
    payload_filter_paths: tuple[str, ...] = (),
    tool_calls_path: Optional[str] = None,
    usage_patterns: tuple[UsagePattern, ...] = _DEFAULT_USAGE_PATTERNS,
) -> Optional[StatsSnapshot]:
    payloads = getattr(artifacts, source_field, None)
    if not isinstance(payloads, tuple) or not payloads:
        return None
    if any(not isinstance(payload, dict) for payload in payloads):
        return None

    filtered: list[Any] = list(payloads)
    for json_path in payload_filter_paths:
        selected = select_values(filtered, json_path)
        if selected is None:
            return None
        filtered = [item for item in selected if isinstance(item, dict)]
        if not filtered:
            return None

    filtered_payloads = [item for item in filtered if isinstance(item, dict)]
    if not filtered_payloads:
        return None

    models_usage: Dict[str, Dict[str, int]] = {}
    llm_calls = 0
    has_llm_calls = False
    tool_calls = 0 if tool_calls_path is None else None
    for payload in filtered_payloads:
        usage_raw = last_value(payload, usage_field)
        has_usage_signal = isinstance(usage_raw, dict)
        if usage_raw is None:
            usage = _extract_usage(payload, patterns=usage_patterns)
            has_usage_signal = usage is not None
        elif isinstance(usage_raw, dict):
            usage = _extract_usage(usage_raw, patterns=usage_patterns)
        else:
            usage = None
        if has_usage_signal:
            llm_calls += 1
            has_llm_calls = True

        model_name = req_str(payload, model_field)
        if model_name is not None and usage is not None:
            _merge_usage(models_usage, model_name, usage)
        if tool_calls_path is None:
            tool_calls += _count_tool_calls(payload)

    if tool_calls_path is not None:
        selected_tool_calls = select_values(filtered_payloads, tool_calls_path)
        tool_calls = len(selected_tool_calls) if selected_tool_calls is not None else None

    return build_stats_snapshot(
        models_usage=models_usage,
        llm_calls=llm_calls if has_llm_calls else None,
        tool_calls=tool_calls,
    )


def extract_opencode_session_export_stats(
    artifacts: StatsArtifacts,
    *,
    source_field: str = "session_payload",
    assistant_messages_path: str = '$.messages[?(@.info.role == "assistant")]',
    tool_parts_path: str = '$.parts[?(@.type == "tool")]',
) -> Optional[StatsSnapshot]:
    payload = _artifact_dict(artifacts, source_field)
    if payload is None:
        return None

    assistant_messages = select_values(payload, assistant_messages_path)
    if assistant_messages is None:
        return None

    assistant_dicts = [message for message in assistant_messages if isinstance(message, dict)]
    llm_calls = len(assistant_dicts) if assistant_dicts else None
    models_usage: Dict[str, Dict[str, int]] = {}
    tool_calls = 0
    has_tool_calls = False
    total_cost: Optional[float] = None

    for message in assistant_dicts:
        provider_id = req_str(message, "$.info.providerID")
        model_id = req_str(message, "$.info.modelID")
        tokens = last_value(message, "$.info.tokens")
        usage = parse_usage_by_model(tokens, "opencode") if isinstance(tokens, dict) else None
        if provider_id is not None and model_id is not None and usage is not None:
            _merge_usage(models_usage, f"{provider_id}/{model_id}", usage)

        selected_tool_parts = select_values(message, tool_parts_path)
        if selected_tool_parts is not None:
            tool_calls += len(selected_tool_parts)
            has_tool_calls = True

        message_cost = opt_float(message, "$.info.cost")
        if message_cost is not None:
            if total_cost is None:
                total_cost = 0.0
            total_cost += message_cost

    return build_stats_snapshot(
        models_usage=models_usage,
        llm_calls=llm_calls,
        tool_calls=tool_calls if has_tool_calls else None,
        total_cost=total_cost,
    )


def _validated_snapshots(snapshots: Iterable[Optional[StatsSnapshot]]) -> list[StatsSnapshot]:
    validated_snapshots: list[StatsSnapshot] = []
    for candidate in snapshots:
        if candidate is None:
            continue
        validated = build_stats_snapshot(
            models_usage=candidate.models_usage,
            llm_calls=candidate.llm_calls,
            tool_calls=candidate.tool_calls,
            total_cost=candidate.total_cost,
        )
        if validated is not None:
            validated_snapshots.append(validated)
    return validated_snapshots


def _merge_stats_snapshots_fallback(snapshots: list[StatsSnapshot]) -> StatsSnapshot:
    merged = StatsSnapshot(models_usage={}, llm_calls=None, tool_calls=None, total_cost=None)
    for validated in snapshots:
        merged = StatsSnapshot(
            models_usage=merged.models_usage if merged.models_usage else validated.models_usage,
            llm_calls=merged.llm_calls if merged.llm_calls is not None else validated.llm_calls,
            tool_calls=merged.tool_calls if merged.tool_calls is not None else validated.tool_calls,
            total_cost=merged.total_cost if merged.total_cost is not None else validated.total_cost,
        )
    return merged


def _merge_stats_snapshots_aggregate(snapshots: list[StatsSnapshot]) -> StatsSnapshot:
    merged_models_usage: Dict[str, Dict[str, int]] = {}
    for snapshot in snapshots:
        for model_name, usage in snapshot.models_usage.items():
            merge_model_usage(merged_models_usage, model_name, usage)
    llm_calls = next((snapshot.llm_calls for snapshot in snapshots if snapshot.llm_calls is not None), None)
    tool_calls = next((snapshot.tool_calls for snapshot in snapshots if snapshot.tool_calls is not None), None)
    total_cost = next((snapshot.total_cost for snapshot in snapshots if snapshot.total_cost is not None), None)
    return StatsSnapshot(
        models_usage=merged_models_usage,
        llm_calls=llm_calls,
        tool_calls=tool_calls,
        total_cost=total_cost,
    )


def merge_stats_snapshots(
    snapshots: Iterable[Optional[StatsSnapshot]],
    *,
    strategy: Literal["aggregate", "fallback"] = "aggregate",
) -> StatsSnapshot:
    if strategy not in {"aggregate", "fallback"}:
        raise ValueError(f"unsupported snapshot merge strategy: {strategy}")
    validated_snapshots = _validated_snapshots(snapshots)
    if not validated_snapshots:
        return StatsSnapshot(models_usage={}, llm_calls=None, tool_calls=None, total_cost=None)
    if strategy == "fallback":
        return _merge_stats_snapshots_fallback(validated_snapshots)
    return _merge_stats_snapshots_aggregate(validated_snapshots)
