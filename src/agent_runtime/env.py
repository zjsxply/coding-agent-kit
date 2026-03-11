from __future__ import annotations

import os
from typing import Callable, Dict, Optional

from .parsing import normalize_text as default_normalize_text

_LITELLM_PROVIDER_IDS: frozenset[str] = frozenset(
    {
        "anthropic",
        "azure_ai",
        "azure_openai",
        "bedrock",
        "bedrock_converse",
        "cohere",
        "deepseek",
        "fireworks",
        "google_anthropic_vertex",
        "google_genai",
        "google_vertexai",
        "groq",
        "huggingface",
        "ibm",
        "mistralai",
        "nvidia",
        "ollama",
        "openai",
        "perplexity",
        "together",
        "upstage",
        "xai",
    }
)


def normalize_provider_model(
    model: str,
    *,
    default_provider: str,
    colon_as_provider: bool = True,
) -> str:
    normalized = model.strip()
    if "/" in normalized:
        return normalized
    if colon_as_provider and ":" in normalized:
        provider, model_name = normalized.split(":", 1)
        provider = provider.strip()
        model_name = model_name.strip()
        if provider and model_name:
            return f"{provider}/{model_name}"
    return f"{default_provider}/{normalized}"


def normalize_litellm_model(
    model: str,
    *,
    default_provider: str = "openai",
    output_format: str = "colon",
) -> str:
    if output_format == "slash":
        return normalize_provider_model(model, default_provider=default_provider)
    if output_format != "colon":
        raise ValueError(f"unsupported LiteLLM model output format: {output_format}")

    normalized = model.strip()
    if ":" in normalized:
        return normalized
    if "/" in normalized:
        provider, model_name = normalized.split("/", 1)
        if provider in _LITELLM_PROVIDER_IDS and model_name:
            return f"{provider}:{model_name}"
    return f"{default_provider}:{normalized}"


def normalize_model(
    model: Optional[str],
    *,
    provider: Optional[str],
    normalize_text: Callable[[Optional[str]], Optional[str]] = default_normalize_text,
    colon_as_provider: bool = True,
) -> Optional[str]:
    normalized = normalize_text(model)
    if normalized is None:
        return None

    if "/" in normalized:
        provider_id, model_id = normalized.split("/", 1)
    elif colon_as_provider and ":" in normalized:
        provider_id, model_id = normalized.split(":", 1)
    else:
        normalized_provider = normalize_text(provider)
        if normalized_provider is None:
            return None
        provider_id, model_id = normalized_provider, normalized

    provider_id = provider_id.strip()
    model_id = model_id.strip()
    if not provider_id or not model_id:
        return None
    return f"{provider_id}/{model_id}"


def extract_model_id(
    model: Optional[str],
    *,
    normalize_text: Callable[[Optional[str]], Optional[str]] = default_normalize_text,
    colon_as_provider: bool = True,
) -> Optional[str]:
    normalized = normalize_text(model)
    if normalized is None:
        return None
    if "/" in normalized:
        _, model_id = normalized.split("/", 1)
        return normalize_text(model_id)
    if colon_as_provider and ":" in normalized:
        _, model_id = normalized.split(":", 1)
        return normalize_text(model_id)
    return normalized


def missing_env_message(missing: list[str]) -> Optional[str]:
    if not missing:
        return None
    return f"missing required environment variable(s): {', '.join(missing)}"


def missing_env_with_fallback_message(missing: list[tuple[str, str]]) -> Optional[str]:
    if not missing:
        return None
    formatted: list[str] = []
    for primary, fallback in missing:
        if primary == fallback:
            formatted.append(primary)
        else:
            formatted.append(f"{primary} (or {fallback})")
    return f"missing required environment variable(s): {', '.join(formatted)}"


def resolve_openai_api_key(
    env_key: str,
    *,
    normalize_text: Callable[[Optional[str]], Optional[str]] = default_normalize_text,
    source_env: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    env_source = source_env if source_env is not None else os.environ
    return normalize_text(env_source.get(env_key)) or normalize_text(env_source.get("OPENAI_API_KEY"))


def resolve_openai_base_url(
    env_key: str,
    *,
    normalize_text: Callable[[Optional[str]], Optional[str]] = default_normalize_text,
    source_env: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    env_source = source_env if source_env is not None else os.environ
    return normalize_text(env_source.get(env_key)) or normalize_text(env_source.get("OPENAI_BASE_URL"))


def resolve_openai_model(
    env_key: str,
    *,
    normalize_text: Callable[[Optional[str]], Optional[str]] = default_normalize_text,
    model_override: Optional[str] = None,
    source_env: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    env_source = source_env if source_env is not None else os.environ
    return (
        normalize_text(model_override)
        or normalize_text(env_source.get(env_key))
        or normalize_text(env_source.get("OPENAI_DEFAULT_MODEL"))
    )


def resolve_litellm_model(
    env_key: str,
    *,
    normalize_text: Callable[[Optional[str]], Optional[str]] = default_normalize_text,
    model_override: Optional[str] = None,
    source_env: Optional[Dict[str, str]] = None,
    default_provider: str = "openai",
    output_format: str = "slash",
) -> Optional[str]:
    model = resolve_openai_model(
        env_key,
        normalize_text=normalize_text,
        model_override=model_override,
        source_env=source_env,
    )
    if model is None:
        return None
    return normalize_litellm_model(
        model,
        default_provider=default_provider,
        output_format=output_format,
    )


def resolve_openai_env(
    *,
    api_key_env: str,
    model_env: str,
    base_url_env: Optional[str] = None,
    model_override: Optional[str] = None,
    source_env: Optional[Dict[str, str]] = None,
    require_api_key: bool = True,
    require_model: bool = True,
    normalize_text: Callable[[Optional[str]], Optional[str]] = default_normalize_text,
) -> tuple[Dict[str, Optional[str]], Optional[str]]:
    api_key = resolve_openai_api_key(api_key_env, normalize_text=normalize_text, source_env=source_env)
    model = resolve_openai_model(
        model_env,
        normalize_text=normalize_text,
        model_override=model_override,
        source_env=source_env,
    )
    base_url = (
        resolve_openai_base_url(base_url_env, normalize_text=normalize_text, source_env=source_env)
        if base_url_env
        else None
    )

    missing: list[tuple[str, str]] = []
    if require_api_key and not api_key:
        missing.append((api_key_env, "OPENAI_API_KEY"))
    if require_model and not model:
        missing.append((model_env, "OPENAI_DEFAULT_MODEL"))
    if missing:
        return {}, missing_env_with_fallback_message(missing)

    return {
        "api_key": api_key,
        "model": model,
        "base_url": base_url,
    }, None
