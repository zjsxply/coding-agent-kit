from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import Enum
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, Literal, Optional, Union

import jsonpath


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
class JsonlStatsSpec:
    source_field: str = "jsonl_payloads"
    model_field: str = "$.model"
    usage_field: str = "$.usage"
    payload_filter_paths: tuple[str, ...] = ()
    tool_calls_path: Optional[str] = None
    usage_patterns: tuple["UsagePattern", ...] = ()


class StatsMergeStrategy(str, Enum):
    AGGREGATE = "aggregate"
    FALLBACK = "fallback"


class NumericMergeStrategy(str, Enum):
    FIRST = "first"
    MAX = "max"
    SUM = "sum"


_MISSING = object()
_INVALID = object()

JsonPathSegment = Union[str, int]
JsonPathSpec = Union[JsonPathSegment, tuple[JsonPathSegment, ...]]


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
    segments: tuple[JsonPathSegment, ...]
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


@lru_cache(maxsize=4096)
def _compile_json_path(path: str) -> Any:
    return jsonpath.compile(path, strict=True)


def select_values(value: Any, path: str) -> Optional[list[Any]]:
    try:
        return _compile_json_path(path).findall(value) or None
    except Exception:
        return None


def get_path_value(value: Any, path: str) -> Any:
    try:
        compiled = _compile_json_path(path)
    except Exception:
        return _MISSING
    values = compiled.findall(value)
    if not values:
        return _MISSING
    if compiled.singular_query():
        return values[-1]
    return values


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


def sum_usage_entries(usages: Iterable[Optional[Dict[str, int]]]) -> Optional[Dict[str, int]]:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    count = 0
    for usage in usages:
        parsed_usage = parse_usage_by_model(usage, "prompt_completion") if isinstance(usage, dict) else None
        if parsed_usage is None:
            continue
        prompt_tokens += parsed_usage["prompt_tokens"]
        completion_tokens += parsed_usage["completion_tokens"]
        total_tokens += parsed_usage["total_tokens"]
        count += 1
    if count < 1:
        return None
    return _compose_usage_entry(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def normalize_stats_snapshot(
    *,
    models_usage: Any,
    llm_calls: Any,
    tool_calls: Any,
    total_cost: Any = None,
) -> StatsSnapshot:
    snapshot = build_stats_snapshot(
        models_usage=models_usage,
        llm_calls=llm_calls,
        tool_calls=tool_calls,
        total_cost=total_cost,
    )
    if snapshot is not None:
        return snapshot
    return StatsSnapshot(models_usage={}, llm_calls=None, tool_calls=None, total_cost=None)


def build_single_model_stats_snapshot(
    *,
    model_name: Optional[str],
    usage: Optional[Dict[str, int]],
    llm_calls: Optional[int],
    tool_calls: Optional[int],
    total_cost: Optional[float] = None,
    normalize_text: Callable[[Optional[str]], Optional[str]] = _normalize_nonempty_text,
    as_int: Optional[Callable[[Any], Optional[int]]] = None,
) -> Optional[StatsSnapshot]:
    def _as_int(value: Any) -> Optional[int]:
        return _normalize_optional_int(value)

    int_parser = as_int or _as_int
    models_usage: Dict[str, Dict[str, int]] = {}
    normalized_model_name = normalize_text(model_name)
    parsed_usage = parse_usage_by_model(usage, "prompt_completion") if isinstance(usage, dict) else None
    if normalized_model_name is not None and parsed_usage is not None:
        models_usage = {normalized_model_name: parsed_usage}

    return build_stats_snapshot(
        models_usage=models_usage,
        llm_calls=int_parser(llm_calls),
        tool_calls=int_parser(tool_calls),
        total_cost=total_cost,
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
            merge_model_usage(
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
    spec: Optional[JsonlStatsSpec] = None,
    source_field: str = "jsonl_payloads",
    model_field: str = "$.model",
    usage_field: str = "$.usage",
    payload_filter_paths: tuple[str, ...] = (),
    tool_calls_path: Optional[str] = None,
    usage_patterns: tuple[UsagePattern, ...] = _DEFAULT_USAGE_PATTERNS,
) -> Optional[StatsSnapshot]:
    if spec is not None:
        source_field = spec.source_field
        model_field = spec.model_field
        usage_field = spec.usage_field
        payload_filter_paths = spec.payload_filter_paths
        tool_calls_path = spec.tool_calls_path
        usage_patterns = spec.usage_patterns or _DEFAULT_USAGE_PATTERNS

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
            merge_model_usage(models_usage, model_name, usage)
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
            merge_model_usage(models_usage, f"{provider_id}/{model_id}", usage)

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


def _normalize_snapshot_merge_strategy(
    strategy: Union[StatsMergeStrategy, Literal["aggregate", "fallback"]],
) -> StatsMergeStrategy:
    if isinstance(strategy, StatsMergeStrategy):
        return strategy
    if strategy == "aggregate":
        return StatsMergeStrategy.AGGREGATE
    if strategy == "fallback":
        return StatsMergeStrategy.FALLBACK
    raise ValueError(f"unsupported snapshot merge strategy: {strategy}")


def _normalize_numeric_merge_strategy(
    strategy: Union[NumericMergeStrategy, Literal["first", "max", "sum"]],
) -> NumericMergeStrategy:
    if isinstance(strategy, NumericMergeStrategy):
        return strategy
    if strategy == "first":
        return NumericMergeStrategy.FIRST
    if strategy == "max":
        return NumericMergeStrategy.MAX
    if strategy == "sum":
        return NumericMergeStrategy.SUM
    raise ValueError(f"unsupported numeric merge strategy: {strategy}")


def _merge_numeric_field(
    values: Iterable[Optional[Union[int, float]]],
    *,
    strategy: Union[NumericMergeStrategy, Literal["first", "max", "sum"]],
) -> Optional[Union[int, float]]:
    normalized_values = [value for value in values if value is not None]
    if not normalized_values:
        return None
    normalized_strategy = _normalize_numeric_merge_strategy(strategy)
    if normalized_strategy == NumericMergeStrategy.FIRST:
        return normalized_values[0]
    if normalized_strategy == NumericMergeStrategy.MAX:
        return max(normalized_values)
    if normalized_strategy == NumericMergeStrategy.SUM:
        return sum(normalized_values)
    raise ValueError(f"unsupported numeric merge strategy: {strategy}")


def _merge_stats_snapshots_aggregate_with_field_strategy(
    snapshots: list[StatsSnapshot],
    *,
    llm_calls_strategy: Union[NumericMergeStrategy, Literal["first", "max", "sum"]],
    tool_calls_strategy: Union[NumericMergeStrategy, Literal["first", "max", "sum"]],
    total_cost_strategy: Union[NumericMergeStrategy, Literal["first", "max", "sum"]],
) -> StatsSnapshot:
    merged_models_usage: Dict[str, Dict[str, int]] = {}
    for snapshot in snapshots:
        for model_name, usage in snapshot.models_usage.items():
            merge_model_usage(merged_models_usage, model_name, usage)
    llm_calls_raw = _merge_numeric_field(
        (snapshot.llm_calls for snapshot in snapshots),
        strategy=llm_calls_strategy,
    )
    tool_calls_raw = _merge_numeric_field(
        (snapshot.tool_calls for snapshot in snapshots),
        strategy=tool_calls_strategy,
    )
    total_cost_raw = _merge_numeric_field(
        (snapshot.total_cost for snapshot in snapshots),
        strategy=total_cost_strategy,
    )
    llm_calls = int(llm_calls_raw) if llm_calls_raw is not None else None
    tool_calls = int(tool_calls_raw) if tool_calls_raw is not None else None
    total_cost = float(total_cost_raw) if total_cost_raw is not None else None
    return StatsSnapshot(
        models_usage=merged_models_usage,
        llm_calls=llm_calls,
        tool_calls=tool_calls,
        total_cost=total_cost,
    )


def merge_stats_snapshots(
    snapshots: Iterable[Optional[StatsSnapshot]],
    *,
    strategy: Union[StatsMergeStrategy, Literal["aggregate", "fallback"]] = StatsMergeStrategy.AGGREGATE,
    llm_calls_strategy: Union[NumericMergeStrategy, Literal["first", "max", "sum"]] = NumericMergeStrategy.FIRST,
    tool_calls_strategy: Union[NumericMergeStrategy, Literal["first", "max", "sum"]] = NumericMergeStrategy.FIRST,
    total_cost_strategy: Union[NumericMergeStrategy, Literal["first", "max", "sum"]] = NumericMergeStrategy.FIRST,
) -> StatsSnapshot:
    normalized_strategy = _normalize_snapshot_merge_strategy(strategy)
    normalized_llm_calls_strategy = _normalize_numeric_merge_strategy(llm_calls_strategy)
    normalized_tool_calls_strategy = _normalize_numeric_merge_strategy(tool_calls_strategy)
    normalized_total_cost_strategy = _normalize_numeric_merge_strategy(total_cost_strategy)
    if normalized_strategy == StatsMergeStrategy.FALLBACK and (
        normalized_llm_calls_strategy != NumericMergeStrategy.FIRST
        or normalized_tool_calls_strategy != NumericMergeStrategy.FIRST
        or normalized_total_cost_strategy != NumericMergeStrategy.FIRST
    ):
        raise ValueError(
            "numeric merge strategies are supported only when strategy='aggregate'"
        )
    validated_snapshots = _validated_snapshots(snapshots)
    if not validated_snapshots:
        return StatsSnapshot(models_usage={}, llm_calls=None, tool_calls=None, total_cost=None)
    if normalized_strategy == StatsMergeStrategy.FALLBACK:
        return _merge_stats_snapshots_fallback(validated_snapshots)
    return _merge_stats_snapshots_aggregate_with_field_strategy(
        validated_snapshots,
        llm_calls_strategy=normalized_llm_calls_strategy,
        tool_calls_strategy=normalized_tool_calls_strategy,
        total_cost_strategy=normalized_total_cost_strategy,
    )
