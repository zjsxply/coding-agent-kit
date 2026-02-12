#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_WEB_URL = "https://github.com/algorithmicsuperintelligence/openevolve"
DEFAULT_IMAGE_PROMPT = "What is shown in this image? What text can you read?"
DEFAULT_VIDEO_PROMPT = "What happens in this video? List any visible text."
DEFAULT_WEB_PROMPT = "Visit {url} and briefly describe what is on that page."
DEFAULT_BASIC_EXPECTED = "CAKIT_HEALTHCHECK_OK"
DEFAULT_VIDEO_EXPECTED = "CAKIT VIDEO TEST 123"
DEFAULT_BASIC_PROMPT = f"Reply with exactly this text and nothing else: {DEFAULT_BASIC_EXPECTED}"
CASE_ORDER = {"basic": 0, "image": 1, "video": 2, "web": 3}
TASK_CHOICES = ("basic", "image", "video", "web")


def _extract_last_json(text: str) -> Dict[str, Any]:
    decoder = json.JSONDecoder()
    idx = 0
    last_obj: Optional[Dict[str, Any]] = None
    while idx < len(text):
        ch = text[idx]
        if ch != "{":
            idx += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, idx)
        except Exception:
            idx += 1
            continue
        if isinstance(obj, dict):
            last_obj = obj
        idx = end
    if last_obj is None:
        raise ValueError("no JSON object found in command output")
    return last_obj


def _empty_payload() -> Dict[str, Any]:
    return {
        "exit_code": None,
        "agent_version": None,
        "response": None,
        "models_usage": {},
        "llm_calls": None,
        "tool_calls": None,
        "output_path": None,
        "raw_output": None,
    }


def _run_cakit(args: List[str], timeout_seconds: int) -> Tuple[int, Dict[str, Any], str, str, Optional[str]]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        return 124, _empty_payload(), stdout, stderr, "timeout"
    parse_error: Optional[str] = None
    try:
        payload = _extract_last_json(proc.stdout)
    except Exception as exc:
        payload = _empty_payload()
        parse_error = str(exc)
    return proc.returncode, payload, proc.stdout, proc.stderr, parse_error


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_models_usage(payload: Dict[str, Any]) -> Tuple[bool, str]:
    models_usage = payload.get("models_usage")
    if not isinstance(models_usage, dict) or not models_usage:
        return False, "models_usage is empty or not object"
    for model_name, usage in models_usage.items():
        if not isinstance(model_name, str) or not model_name:
            return False, "models_usage has invalid model name"
        if not isinstance(usage, dict):
            return False, "models_usage item is not object"
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if not _is_int(value):
                return False, f"models_usage.{model_name}.{key} is not int"
    return True, "ok"


def _check_common_stats(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    response = payload.get("response")
    if not isinstance(response, str) or not response.strip():
        errors.append("response missing or empty")

    models_ok, models_msg = _check_models_usage(payload)
    if not models_ok:
        errors.append(models_msg)

    llm_calls = payload.get("llm_calls")
    if not _is_int(llm_calls) or llm_calls < 1:
        errors.append("llm_calls missing or invalid")

    tool_calls = payload.get("tool_calls")
    if not _is_int(tool_calls):
        errors.append("tool_calls missing or invalid")
    elif tool_calls < 0:
        errors.append("tool_calls must be >= 0")

    return not errors, errors


def _contains_any(text: str, keywords: List[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _run_case(
    *,
    agent: str,
    label: str,
    prompt: str,
    image: Optional[Path],
    video: Optional[Path],
    expected_keywords: Optional[List[str]],
    timeout_seconds: int,
    workdir: Path,
) -> Dict[str, Any]:
    cmd = ["cakit", "run", agent, prompt, "--cwd", str(workdir)]
    if image is not None:
        cmd.extend(["--image", str(image)])
    if video is not None:
        cmd.extend(["--video", str(video)])
    rc, payload, stdout, stderr, parse_error = _run_cakit(cmd, timeout_seconds)

    stats_ok, stat_errors = _check_common_stats(payload)
    semantic_ok = True
    semantic_error = None
    if expected_keywords:
        response_text = str(payload.get("response") or "")
        if not _contains_any(response_text, expected_keywords):
            semantic_ok = False
            semantic_error = f"response missing expected keywords: {expected_keywords}"

    ok = rc == 0 and stats_ok and semantic_ok and (payload.get("exit_code") == 0)
    return {
        "label": label,
        "ok": ok,
        "command_rc": rc,
        "agent_exit_code": payload.get("exit_code"),
        "agent_version": payload.get("agent_version"),
        "stats_ok": stats_ok,
        "stats_errors": stat_errors,
        "semantic_ok": semantic_ok,
        "semantic_error": semantic_error,
        "response": payload.get("response"),
        "models_usage": payload.get("models_usage"),
        "llm_calls": payload.get("llm_calls"),
        "tool_calls": payload.get("tool_calls"),
        "output_path": payload.get("output_path"),
        "command": cmd,
        "workdir": str(workdir),
        "parse_error": parse_error,
        "cakit_result": payload,
        "raw_stdout": stdout,
        "raw_stderr": stderr,
    }


def _build_case_specs(
    *,
    agent: str,
    image_path: Path,
    video_path: Path,
    web_url: str,
    tasks: List[str],
    run_root: Path,
) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    if "basic" in tasks:
        specs.append(
            {
                "agent": agent,
                "label": "basic",
                "prompt": DEFAULT_BASIC_PROMPT,
                "image": None,
                "video": None,
                "expected_keywords": [DEFAULT_BASIC_EXPECTED],
            }
        )
    if "image" in tasks:
        image_keywords = ["unminimize", "ubuntu", "verteen/ubuntu-unminimize"]
        specs.append(
            {
                "agent": agent,
                "label": "image",
                "prompt": DEFAULT_IMAGE_PROMPT,
                "image": image_path,
                "video": None,
                "expected_keywords": image_keywords,
            }
        )
    if "video" in tasks:
        specs.append(
            {
                "agent": agent,
                "label": "video",
                "prompt": DEFAULT_VIDEO_PROMPT,
                "image": None,
                "video": video_path,
                "expected_keywords": [DEFAULT_VIDEO_EXPECTED],
            }
        )
    if "web" in tasks:
        web_keywords = ["openevolve", "algorithmicsuperintelligence", "evolutionary coding agent"]
        specs.append(
            {
                "agent": agent,
                "label": "web",
                "prompt": DEFAULT_WEB_PROMPT.format(url=web_url),
                "image": None,
                "video": None,
                "expected_keywords": web_keywords,
            }
        )
    for spec in specs:
        case_workdir = run_root / agent / str(spec["label"])
        case_workdir.mkdir(parents=True, exist_ok=True)
        spec["workdir"] = case_workdir
    return specs


def _run_case_spec(spec: Dict[str, Any], timeout_seconds: int) -> Dict[str, Any]:
    result = _run_case(
        agent=str(spec["agent"]),
        label=str(spec["label"]),
        prompt=str(spec["prompt"]),
        image=spec["image"],
        video=spec["video"],
        expected_keywords=spec["expected_keywords"],
        timeout_seconds=timeout_seconds,
        workdir=Path(spec["workdir"]),
    )
    result["agent"] = spec["agent"]
    return result


def _build_agent_report(agent: str, cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    ordered_cases = sorted(cases, key=lambda case: CASE_ORDER.get(str(case.get("label")), 99))
    overall_ok = all(case["ok"] for case in ordered_cases)
    version = None
    for case in ordered_cases:
        if isinstance(case.get("agent_version"), str) and case["agent_version"]:
            version = case["agent_version"]
            break
    return {
        "agent": agent,
        "ok": overall_ok,
        "agent_version": version,
        "cases": ordered_cases,
    }


def _run_all_cases(
    *,
    case_specs: List[Dict[str, Any]],
    timeout_seconds: int,
    parallel: bool,
    max_workers: int,
) -> Dict[str, List[Dict[str, Any]]]:
    cases_by_agent: Dict[str, List[Dict[str, Any]]] = {}
    if not parallel or len(case_specs) <= 1:
        for spec in case_specs:
            result = _run_case_spec(spec, timeout_seconds)
            cases_by_agent.setdefault(str(result["agent"]), []).append(result)
        return cases_by_agent
    workers = min(max_workers, len(case_specs))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_run_case_spec, spec, timeout_seconds) for spec in case_specs]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            cases_by_agent.setdefault(str(result["agent"]), []).append(result)
    return cases_by_agent


def _test_agents(
    *,
    agents: List[str],
    image_path: Path,
    video_path: Path,
    web_url: str,
    tasks: List[str],
    timeout_seconds: int,
    parallel: bool,
    max_workers: int,
    run_root: Path,
) -> List[Dict[str, Any]]:
    case_specs: List[Dict[str, Any]] = []
    for agent in agents:
        case_specs.extend(
            _build_case_specs(
                agent=agent,
                image_path=image_path,
                video_path=video_path,
                web_url=web_url,
                tasks=tasks,
                run_root=run_root,
            )
        )
    cases_by_agent = _run_all_cases(
        case_specs=case_specs,
        timeout_seconds=timeout_seconds,
        parallel=parallel,
        max_workers=max_workers,
    )
    reports: List[Dict[str, Any]] = []
    for agent in agents:
        reports.append(_build_agent_report(agent, cases_by_agent.get(agent, [])))
    return reports


def _is_positive_int(value: Any) -> bool:
    return _is_int(value) and value > 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generic availability test for cakit agents.")
    parser.add_argument("agents", nargs="+", help="Agents to test, e.g. kimi codex claude")
    parser.add_argument("--image", default="tests/image1.png", help="Image file path for image test")
    parser.add_argument("--video", default="tests/video.mp4", help="Video file path for video test")
    parser.add_argument("--web-url", default=DEFAULT_WEB_URL, help="URL used for web access test")
    parser.add_argument(
        "--tasks",
        default="basic,image,video,web",
        help="Comma-separated tasks to run: basic,image,video,web (default: all)",
    )
    parser.add_argument("--timeout-seconds", type=int, default=600, help="Per-case timeout in seconds")
    parser.add_argument("--no-parallel", action="store_true", help="Run cases sequentially")
    parser.add_argument("--max-workers", type=int, default=6, help="Max workers for parallel execution")
    args = parser.parse_args()

    image_path = Path(args.image).expanduser().resolve()
    video_path = Path(args.video).expanduser().resolve()
    tasks = [item.strip().lower() for item in str(args.tasks).split(",") if item.strip()]
    invalid_tasks = [item for item in tasks if item not in TASK_CHOICES]
    if not tasks or invalid_tasks:
        print(
            json.dumps(
                {"error": f"invalid tasks: {invalid_tasks}", "allowed": list(TASK_CHOICES)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    if "image" in tasks and not image_path.exists():
        print(json.dumps({"error": f"image not found: {image_path}"}, ensure_ascii=False, indent=2))
        return 2
    if "video" in tasks and not video_path.exists():
        print(json.dumps({"error": f"video not found: {video_path}"}, ensure_ascii=False, indent=2))
        return 2
    if not _is_positive_int(args.max_workers):
        print(json.dumps({"error": "--max-workers must be a positive integer"}, ensure_ascii=False, indent=2))
        return 2

    started = time.time()
    run_root = Path("/tmp") / f"cakit-availability-{int(started)}"
    run_root.mkdir(parents=True, exist_ok=True)
    agents_reports = _test_agents(
        agents=args.agents,
        image_path=image_path,
        video_path=video_path,
        web_url=args.web_url,
        tasks=tasks,
        timeout_seconds=args.timeout_seconds,
        parallel=not args.no_parallel,
        max_workers=args.max_workers,
        run_root=run_root,
    )
    all_ok = all(bool(item.get("ok")) for item in agents_reports)
    report = {
        "timestamp": int(started),
        "ok": all_ok,
        "runtime_seconds": round(time.time() - started, 3),
        "parallel": not args.no_parallel,
        "max_workers": args.max_workers,
        "run_root": str(run_root),
        "agents": agents_reports,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
