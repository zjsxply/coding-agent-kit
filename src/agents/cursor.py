from __future__ import annotations

import platform
import re
import shutil
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import CodingAgent, CommandResult, InstallStrategy, RunCommandTemplate, RunParseResult, RunPlan
from ..agent_runtime import parsing as runtime_parsing
from ..agent_runtime import env as runtime_env
from ..stats_extract import last_value, parse_usage_by_model, select_values, sum_usage_entries


class CursorAgent(CodingAgent):
    name = "cursor"
    display_name = "Cursor Agent"
    binary = "cursor-agent"
    required_runtimes = ("bash", "curl", "tar", "gzip")
    install_strategy = InstallStrategy(kind="custom")
    run_template = RunCommandTemplate(
        base_args=("--print", "--output-format", "stream-json", "--force"),
        prompt_mode="flag",
        prompt_flag="-p",
        model_flag="--model",
    )

    _VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

    def _install_with_custom_strategy(
        self,
        *,
        scope: str,
        version: Optional[str],
    ) -> CommandResult:
        if version and version.strip():
            return self._install_specific_version(version.strip())
        return self._run(["bash", "-c", "curl -fsS https://cursor.com/install | bash"])

    def _build_run_plan(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> Optional[RunPlan]:
        model = runtime_env.resolve_openai_model("CURSOR_MODEL", model_override=model_override)
        endpoint = runtime_env.resolve_openai_base_url("CURSOR_BASE_URL")
        env = {"CURSOR_API_KEY": runtime_env.resolve_openai_api_key("CURSOR_API_KEY")}
        template = self.run_template
        extra_args: list[str] = []
        if endpoint:
            extra_args.extend(["--endpoint", endpoint])
        return self._build_templated_run_plan(
            prompt=prompt,
            model=model,
            env=env,
            extra_args=extra_args,
            template=template,
            parse_output=lambda output, command_result: self._parse_pipeline_output(output),
        )

    def _parse_pipeline_output(self, output: str) -> RunParseResult:
        payloads = runtime_parsing.load_output_json_payloads(output)

        response = next(
            (
                text
                for text in (
                    runtime_parsing.last_nonempty_text(select_values(payloads, path))
                    for path in (
                        '$[?(@.type == "result")].result',
                        '$[?(@.type == "assistant")].message.content[?(@.type == "text")].text',
                    )
                )
                if text is not None
            ),
            None,
        )

        usage = self._extract_usage(payloads)
        system_payloads = [item for item in (select_values(payloads, '$[?(@.type == "system")]') or []) if isinstance(item, dict)]
        init_payloads = [
            item for item in system_payloads if runtime_parsing.normalize_text(last_value(item, "$.subtype")) == "init"
        ]
        model_name = runtime_parsing.normalize_text(last_value(init_payloads, "$[*].model"))
        models_usage = {model_name: usage} if model_name is not None and usage is not None else {}

        tool_payloads = [item for item in (select_values(payloads, '$[?(@.type == "tool_call")]') or []) if isinstance(item, dict)]
        started_call_ids = {
            normalized
            for call_id in (select_values(tool_payloads, '$[?(@.subtype == "started")].call_id') or [])
            if (normalized := runtime_parsing.normalize_text(call_id)) is not None
        }
        all_call_ids = {
            normalized
            for call_id in (select_values(tool_payloads, "$[*].call_id") or [])
            if (normalized := runtime_parsing.normalize_text(call_id)) is not None
        }
        tool_calls = (
            len(started_call_ids) if started_call_ids else len(all_call_ids) if all_call_ids else len(tool_payloads)
        )

        model_call_ids = {
            normalized
            for value in (select_values(payloads, '$[?(@.type == "assistant")].model_call_id') or [])
            if (normalized := runtime_parsing.normalize_text(value)) is not None
        }
        model_call_ids.update(
            normalized
            for value in (select_values(payloads, '$[?(@.type == "tool_call")].model_call_id') or [])
            if (normalized := runtime_parsing.normalize_text(value)) is not None
        )
        if model_call_ids:
            llm_calls: Optional[int] = len(model_call_ids)
        else:
            assistant_payload_values = select_values(payloads, '$[?(@.type == "assistant")]')
            assistant_payload_count = len(assistant_payload_values) if assistant_payload_values is not None else None
            if assistant_payload_count:
                llm_calls = assistant_payload_count
            else:
                llm_calls = 1 if select_values(payloads, '$[?(@.type == "result")]') is not None else None

        return RunParseResult(
            response=response or runtime_parsing.last_stdout_line(output),
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
        )

    def _extract_usage(self, payloads: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        usage_by_model_call_id: Dict[str, Dict[str, int]] = {}
        usage_without_model_call_id: List[Dict[str, int]] = []

        # Cannot be a single JSONPath end-to-end because we must keep parent payload context
        # (for model_call_id de-duplication) while reading multiple usage shapes.
        for usage_filter, usage_path in (
            ('$[?(@.usage != null)]', "$.usage"),
            ('$[?(@.message.usage != null)]', "$.message.usage"),
            ('$[?(@.result.usage != null)]', "$.result.usage"),
        ):
            usage_payloads = [item for item in (select_values(payloads, usage_filter) or []) if isinstance(item, dict)]
            for payload in usage_payloads:
                usage_raw = last_value(payload, usage_path)
                if not isinstance(usage_raw, dict):
                    continue
                usage = parse_usage_by_model(usage_raw, "input_output") or parse_usage_by_model(
                    usage_raw, "prompt_completion"
                )
                if usage is None:
                    continue
                model_call_id = runtime_parsing.normalize_text(last_value(payload, "$.model_call_id"))
                if model_call_id is not None:
                    previous = usage_by_model_call_id.get(model_call_id)
                    if previous is None or usage["total_tokens"] >= previous["total_tokens"]:
                        usage_by_model_call_id[model_call_id] = usage
                    continue
                usage_without_model_call_id.append(usage)

        items = list(usage_by_model_call_id.values()) + usage_without_model_call_id
        return sum_usage_entries(items)

    def _install_specific_version(self, version: str) -> CommandResult:
        started = time.monotonic()
        logs: List[str] = []
        try:
            if not self._VERSION_PATTERN.fullmatch(version):
                raise RuntimeError("invalid Cursor version format")
            os_name = {
                "Linux": "linux",
                "Darwin": "darwin",
            }.get(platform.system())
            arch = {
                "x86_64": "x64",
                "amd64": "x64",
                "arm64": "arm64",
                "aarch64": "arm64",
            }.get(platform.machine().strip().lower())
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
                    self._extract_package_archive(archive, extracted_root)
                candidates = [path.parent for path in extracted_root.rglob("cursor-agent") if path.is_file()]
                if not candidates:
                    raise RuntimeError("failed to locate cursor-agent in downloaded archive")
                candidates.sort(key=lambda path: len(path.parts))
                package_root = candidates[0]
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
    def _extract_package_archive(archive: tarfile.TarFile, extracted_root: Path) -> None:
        root_resolved = extracted_root.resolve()
        for member in archive.getmembers():
            target = (extracted_root / member.name).resolve()
            try:
                target.relative_to(root_resolved)
            except Exception as exc:
                raise RuntimeError(f"unsafe archive member path: {member.name}") from exc

            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"failed to extract archive member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(extracted.read())
            try:
                target.chmod(member.mode & 0o777)
            except Exception:
                pass
