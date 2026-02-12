from __future__ import annotations

import platform
import re
import shutil
import tarfile
import tempfile
import time
import urllib.request
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodingAgent, CommandResult
from ..models import InstallResult, RunResult
from ..utils import load_json_payloads


class CursorAgent(CodingAgent):
    name = "cursor"
    display_name = "Cursor Agent"
    binary = "cursor-agent"

    _VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        if version and version.strip():
            result = self._install_specific_version(version.strip())
        else:
            result = self._run(["bash", "-c", "curl -fsS https://cursor.com/install | bash"])
        ok = result.exit_code == 0
        details = result.output
        return InstallResult(
            agent=self.name,
            version=self.get_version() if ok else None,
            ok=ok,
            details=details,
            config_path=None,
        )

    def configure(self) -> Optional[str]:
        return None

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        model = model_override or os.environ.get("CURSOR_MODEL")
        endpoint = os.environ.get("CURSOR_API_BASE")
        env = {"CURSOR_API_KEY": os.environ.get("CURSOR_API_KEY")}
        cmd = [
            "cursor-agent",
            "-p",
            prompt,
            "--print",
            "--output-format",
            "stream-json",
            "--force",
        ]
        if model:
            cmd.extend(["--model", model])
        if endpoint:
            cmd.extend(["--endpoint", endpoint])
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        payloads = load_json_payloads(output)
        usage = self._extract_usage(payloads)
        tool_calls = self._count_tool_calls(payloads)
        output_path = self._write_output(self.name, output)
        models_usage = self._ensure_models_usage({}, usage, model)
        response = self._extract_response(payloads, output)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            response=response,
            exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["cursor-agent", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _extract_usage(self, payloads: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        for payload in payloads:
            usage = self._find_usage(payload)
            if usage:
                return usage
        return None

    def _extract_response(self, payloads: List[Dict[str, Any]], output: str) -> Optional[str]:
        messages: List[str] = []

        def add_text(value: Any) -> None:
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    messages.append(cleaned)

        def add_from_content(content: Any) -> None:
            if isinstance(content, list):
                parts: List[str] = []
                for entry in content:
                    if not isinstance(entry, dict):
                        continue
                    text = entry.get("text") or entry.get("output_text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                if parts:
                    messages.append("\n".join(parts))
            else:
                add_text(content)

        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            payload_type = payload.get("type")
            if isinstance(payload_type, str) and "delta" in payload_type:
                continue
            if payload.get("role") == "assistant":
                add_from_content(payload.get("content"))
            if payload_type in {"assistant", "assistant_message", "final", "response"}:
                add_text(payload.get("text") or payload.get("message"))
            for key in ("final", "response", "output"):
                add_text(payload.get(key))

        if messages:
            return messages[-1]

        if output:
            stdout = output
            marker = "----- STDERR -----"
            if marker in stdout:
                stdout = stdout.split(marker, 1)[0]
            lines = [line.strip() for line in stdout.splitlines() if line.strip()]
            if lines:
                return lines[-1]
        return None

    def _find_usage(self, payload: Any) -> Optional[Dict[str, int]]:
        if not isinstance(payload, dict):
            return None
        if "usage" in payload and isinstance(payload["usage"], dict):
            return self._normalize_usage(payload["usage"])
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
            if key in payload:
                return self._normalize_usage(payload)
        for value in payload.values():
            if isinstance(value, dict):
                nested = self._find_usage(value)
                if nested:
                    return nested
            if isinstance(value, list):
                for item in value:
                    nested = self._find_usage(item)
                    if nested:
                        return nested
        return None

    def _normalize_usage(self, raw: Dict[str, Any]) -> Dict[str, int]:
        prompt = self._as_int(raw.get("prompt_tokens"))
        completion = self._as_int(raw.get("completion_tokens"))
        total = self._as_int(raw.get("total_tokens"))
        if prompt is None and "input_tokens" in raw:
            prompt = self._as_int(raw.get("input_tokens"))
        if completion is None and "output_tokens" in raw:
            completion = self._as_int(raw.get("output_tokens"))
        if total is None:
            total = (prompt or 0) + (completion or 0)
        return {
            "prompt_tokens": prompt or 0,
            "completion_tokens": completion or 0,
            "total_tokens": total or 0,
        }

    def _count_tool_calls(self, payloads: List[Dict[str, Any]]) -> Optional[int]:
        count = 0
        for payload in payloads:
            if self._looks_like_tool_call(payload):
                count += 1
        return count

    def _looks_like_tool_call(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        for key in ("tool", "tool_name", "toolName", "tool_call", "toolCall", "tool_use", "toolUse", "action"):
            if key in payload:
                return True
        event_type = payload.get("type") or payload.get("event") or payload.get("name")
        if isinstance(event_type, str) and "tool" in event_type.lower():
            return True
        for value in payload.values():
            if isinstance(value, dict) and self._looks_like_tool_call(value):
                return True
            if isinstance(value, list):
                for item in value:
                    if self._looks_like_tool_call(item):
                        return True
        return False

    def _usage_totals(self, usage: Optional[Dict[str, int]]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        if not usage:
            return None, None, None
        return (
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
        )

    @staticmethod
    def _as_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except Exception:
            return None

    def _install_specific_version(self, version: str) -> CommandResult:
        started = time.monotonic()
        logs: List[str] = []
        try:
            if not self._VERSION_PATTERN.fullmatch(version):
                raise RuntimeError("invalid Cursor version format")
            os_name = self._map_os(platform.system())
            arch = self._map_arch(platform.machine())
            if not os_name or not arch:
                raise RuntimeError(
                    f"unsupported platform for cursor version install: {platform.system()}/{platform.machine()}"
                )
            url = f"https://downloads.cursor.com/lab/{version}/{os_name}/{arch}/agent-cli-package.tar.gz"
            logs.append(f"download_url={url}")

            staging_root = Path(tempfile.mkdtemp(prefix="cakit-cursor-"))
            archive_path = staging_root / "agent-cli-package.tar.gz"
            extracted_root = staging_root / "extract"
            extracted_root.mkdir(parents=True, exist_ok=True)
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "curl/8.0.0",
                        "Accept": "*/*",
                    },
                )
                with urllib.request.urlopen(request, timeout=60) as response, archive_path.open("wb") as file:
                    shutil.copyfileobj(response, file)
                with tarfile.open(archive_path, mode="r:gz") as archive:
                    archive.extractall(path=extracted_root)
                package_root = self._find_cursor_package_root(extracted_root)
                target_binary = package_root / "cursor-agent"
                if not target_binary.is_file():
                    raise RuntimeError("cursor-agent binary missing from downloaded package")

                final_dir = Path.home() / ".local" / "share" / "cursor-agent" / "versions" / version
                final_dir.parent.mkdir(parents=True, exist_ok=True)
                if final_dir.exists():
                    shutil.rmtree(final_dir)
                shutil.move(str(package_root), str(final_dir))

                bin_dir = Path.home() / ".local" / "bin"
                bin_dir.mkdir(parents=True, exist_ok=True)
                final_binary = final_dir / "cursor-agent"
                for link_name in ("agent", "cursor-agent"):
                    link = bin_dir / link_name
                    if link.exists() or link.is_symlink():
                        link.unlink()
                    link.symlink_to(final_binary)
            finally:
                shutil.rmtree(staging_root, ignore_errors=True)

            logs.append("installed cursor-agent and updated ~/.local/bin symlinks")
            return CommandResult(
                exit_code=0,
                stdout="\n".join(logs),
                stderr="",
                duration_seconds=time.monotonic() - started,
            )
        except Exception as exc:
            return CommandResult(
                exit_code=1,
                stdout="\n".join(logs),
                stderr=str(exc),
                duration_seconds=time.monotonic() - started,
            )

    @staticmethod
    def _map_os(system_name: str) -> Optional[str]:
        mapping = {
            "Linux": "linux",
            "Darwin": "darwin",
        }
        return mapping.get(system_name)

    @staticmethod
    def _map_arch(machine: str) -> Optional[str]:
        normalized = machine.strip().lower()
        mapping = {
            "x86_64": "x64",
            "amd64": "x64",
            "arm64": "arm64",
            "aarch64": "arm64",
        }
        return mapping.get(normalized)

    @staticmethod
    def _find_cursor_package_root(extracted_root: Path) -> Path:
        candidates = [path.parent for path in extracted_root.rglob("cursor-agent") if path.is_file()]
        if not candidates:
            raise RuntimeError("failed to locate cursor-agent in downloaded archive")
        candidates.sort(key=lambda path: len(path.parts))
        return candidates[0]
