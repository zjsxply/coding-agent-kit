#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import email.utils
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterable, Optional


UTC = dt.timezone.utc
USER_AGENT = "cakit-agent-history-version-research/1.0"
PLAIN_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")
CURSOR_ARTICLE_RE = re.compile(
    r"<article>.*?"
    r'<a[^>]+href="(?P<href>/changelog/[^"]+)">(?P<meta>.*?)'
    r'<time dateTime="(?P<date>[^"]+)"[^>]*>.*?</time></a>.*?'
    r'<h1[^>]*><a[^>]+href="(?P=href)">(?P<title>.*?)</a>',
    re.S,
)
CURSOR_LABEL_RE = re.compile(r'<span class="label">([^<]+)</span>')


@dataclasses.dataclass(frozen=True)
class SnapshotRow:
    timepoint_date: str
    cutoff_utc: str
    agent: str
    install_version: str
    status: str
    source_kind: str
    source_ref: str
    published_at_utc: str
    note: str

    def to_tsv(self) -> str:
        return "\t".join(
            [
                self.timepoint_date,
                self.cutoff_utc,
                self.agent,
                self.install_version,
                self.status,
                self.source_kind,
                self.source_ref,
                self.published_at_utc,
                self.note,
            ]
        )


@dataclasses.dataclass(frozen=True)
class AgentSpec:
    kind: str
    source_kind: str
    source_ref: str
    note: str
    selector: str = ""
    strip_v: bool = False
    status: str = "confirmed"


@dataclasses.dataclass(frozen=True)
class HeadProbeResult:
    version: str
    url: str
    last_modified: Optional[dt.datetime]
    exists: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query historical install selectors for coding agents in coding-agent-kit."
    )
    parser.add_argument(
        "--timepoint-date",
        required=True,
        help="Snapshot date in YYYY-MM-DD. Default cutoff is YYYY-MM-DDT23:59:59Z.",
    )
    parser.add_argument(
        "--cutoff-utc",
        help="Optional explicit cutoff timestamp in UTC (for example 2026-02-01T23:59:59Z).",
    )
    parser.add_argument(
        "--agent",
        nargs="+",
        default=["all"],
        help="Agent names or 'all'.",
    )
    parser.add_argument(
        "--include-header",
        action="store_true",
        help="Print TSV column header before rows.",
    )
    parser.add_argument(
        "--include-comments",
        action="store_true",
        help="Print the snapshot file comment prelude before rows.",
    )
    parser.add_argument(
        "--extra-candidate",
        action="append",
        default=[],
        metavar="AGENT=VERSION",
        help="Additional candidate versions for probe-based agents such as factory or trae-cn.",
    )
    return parser.parse_args()


def http_request(
    url: str,
    *,
    accept: Optional[str] = None,
    method: str = "GET",
) -> urllib.request.addinfourl:
    headers = {
        "User-Agent": USER_AGENT,
    }
    if accept is not None:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers, method=method)
    return urllib.request.urlopen(request, timeout=60)


def http_get_text(url: str, *, accept: Optional[str] = None) -> str:
    with http_request(url, accept=accept) as response:
        return response.read().decode("utf-8", errors="replace")


def http_get_json(url: str, *, accept: Optional[str] = None) -> object:
    return json.loads(http_get_text(url, accept=accept))


def parse_utc_date(value: str) -> dt.datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_utc(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def normalize_cutoff(timepoint_date: str, cutoff_utc: Optional[str]) -> dt.datetime:
    if cutoff_utc:
        return parse_utc_date(cutoff_utc)
    return parse_utc_date(f"{timepoint_date}T23:59:59Z")


def normalize_agent_list(values: list[str]) -> list[str]:
    if values == ["all"] or "all" in values:
        return list(AGENT_ORDER)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        if normalized not in AGENT_ORDER:
            raise ValueError(f"unsupported agent: {normalized}")
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def parse_extra_candidates(values: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid --extra-candidate value: {value!r}")
        agent, version = value.split("=", 1)
        normalized_agent = agent.strip()
        normalized_version = version.strip()
        if not normalized_agent or not normalized_version:
            raise ValueError(f"invalid --extra-candidate value: {value!r}")
        result.setdefault(normalized_agent, []).append(normalized_version)
    return result


def version_key(version: str) -> tuple[int, ...]:
    normalized = version.strip()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    parts = normalized.split(".")
    return tuple(int(part) for part in parts)


def choose_latest_by_time(candidates: Iterable[tuple[str, dt.datetime]]) -> Optional[tuple[str, dt.datetime]]:
    best: Optional[tuple[str, dt.datetime]] = None
    for version, published_at in candidates:
        if best is None or published_at > best[1] or (published_at == best[1] and version_key(version) > version_key(best[0])):
            best = (version, published_at)
    return best


def plain_version(version: str) -> bool:
    return bool(PLAIN_VERSION_RE.fullmatch(version))


def snapshot_row(
    *,
    timepoint_date: str,
    cutoff: dt.datetime,
    agent: str,
    install_version: str,
    status: str,
    source_kind: str,
    source_ref: str,
    published_at: Optional[dt.datetime],
    note: str,
) -> SnapshotRow:
    return SnapshotRow(
        timepoint_date=timepoint_date,
        cutoff_utc=format_utc(cutoff),
        agent=agent,
        install_version=install_version,
        status=status,
        source_kind=source_kind,
        source_ref=source_ref,
        published_at_utc=(format_utc(published_at) if published_at is not None else ""),
        note=note,
    )


def pending_row(
    *,
    timepoint_date: str,
    cutoff: dt.datetime,
    agent: str,
    source_kind: str,
    source_ref: str,
    note: str,
) -> SnapshotRow:
    return snapshot_row(
        timepoint_date=timepoint_date,
        cutoff=cutoff,
        agent=agent,
        install_version="",
        status="pending",
        source_kind=source_kind,
        source_ref=source_ref,
        published_at=None,
        note=note,
    )


def resolve_npm(spec: AgentSpec, *, timepoint_date: str, cutoff: dt.datetime, agent: str) -> SnapshotRow:
    encoded = urllib.parse.quote(spec.selector, safe="@")
    payload = http_get_json(f"https://registry.npmjs.org/{encoded}", accept="application/json")
    if not isinstance(payload, dict):
        raise RuntimeError("npm registry returned a non-object payload")
    time_map = payload.get("time")
    if not isinstance(time_map, dict):
        raise RuntimeError("npm registry payload is missing the time map")
    candidates: list[tuple[str, dt.datetime]] = []
    for version, published_raw in time_map.items():
        if not isinstance(version, str) or version in {"created", "modified"} or not plain_version(version):
            continue
        if not isinstance(published_raw, str):
            continue
        published_at = parse_utc_date(published_raw)
        if published_at <= cutoff:
            candidates.append((version, published_at))
    best = choose_latest_by_time(candidates)
    if best is None:
        raise RuntimeError("no plain npm version exists on or before the cutoff")
    return snapshot_row(
        timepoint_date=timepoint_date,
        cutoff=cutoff,
        agent=agent,
        install_version=best[0],
        status=spec.status,
        source_kind=spec.source_kind,
        source_ref=spec.source_ref,
        published_at=best[1],
        note=spec.note,
    )


def resolve_pypi(spec: AgentSpec, *, timepoint_date: str, cutoff: dt.datetime, agent: str) -> SnapshotRow:
    payload = http_get_json(f"https://pypi.org/pypi/{spec.selector}/json", accept="application/json")
    if not isinstance(payload, dict):
        raise RuntimeError("PyPI returned a non-object payload")
    releases = payload.get("releases")
    if not isinstance(releases, dict):
        raise RuntimeError("PyPI payload is missing releases")
    candidates: list[tuple[str, dt.datetime]] = []
    for version, files in releases.items():
        if not isinstance(version, str) or not plain_version(version) or not isinstance(files, list):
            continue
        for file_payload in files:
            if not isinstance(file_payload, dict):
                continue
            published_raw = file_payload.get("upload_time_iso_8601")
            if not isinstance(published_raw, str):
                continue
            published_at = parse_utc_date(published_raw)
            if published_at <= cutoff:
                candidates.append((version, published_at))
    best = choose_latest_by_time(candidates)
    if best is None:
        raise RuntimeError("no plain PyPI version exists on or before the cutoff")
    return snapshot_row(
        timepoint_date=timepoint_date,
        cutoff=cutoff,
        agent=agent,
        install_version=best[0],
        status=spec.status,
        source_kind=spec.source_kind,
        source_ref=spec.source_ref,
        published_at=best[1],
        note=spec.note,
    )


def github_get_json(path: str) -> object:
    url = f"https://api.github.com{path}"
    return http_get_json(url, accept="application/vnd.github+json")


def resolve_github_release(spec: AgentSpec, *, timepoint_date: str, cutoff: dt.datetime, agent: str) -> SnapshotRow:
    owner, repo = spec.selector.split("/", 1)
    candidates: list[tuple[str, dt.datetime, str]] = []
    page = 1
    while True:
        payload = github_get_json(f"/repos/{owner}/{repo}/releases?per_page=100&page={page}")
        if not isinstance(payload, list):
            raise RuntimeError("GitHub releases API returned a non-list payload")
        if not payload:
            break
        for release in payload:
            if not isinstance(release, dict) or release.get("draft"):
                continue
            tag_name = release.get("tag_name")
            published_raw = release.get("published_at")
            html_url = release.get("html_url")
            if not isinstance(tag_name, str) or not isinstance(published_raw, str) or not isinstance(html_url, str):
                continue
            published_at = parse_utc_date(published_raw)
            if published_at > cutoff:
                continue
            candidates.append((tag_name, published_at, html_url))
        page += 1
    if not candidates:
        raise RuntimeError("no GitHub release exists on or before the cutoff")
    tag_name, published_at, html_url = max(
        candidates,
        key=lambda item: (item[1], version_key(item[0].lstrip("v"))),
    )
    install_version = tag_name[1:] if spec.strip_v and tag_name.startswith("v") else tag_name
    return snapshot_row(
        timepoint_date=timepoint_date,
        cutoff=cutoff,
        agent=agent,
        install_version=install_version,
        status=spec.status,
        source_kind=spec.source_kind,
        source_ref=html_url,
        published_at=published_at,
        note=spec.note,
    )


def iter_claude_objects() -> Iterable[dict[str, object]]:
    next_page_token: Optional[str] = None
    while True:
        params = {
            "prefix": "claude-code-releases/",
            "fields": "items(name,timeCreated),nextPageToken",
        }
        if next_page_token is not None:
            params["pageToken"] = next_page_token
        query = urllib.parse.urlencode(params)
        payload = http_get_json(
            f"https://storage.googleapis.com/storage/v1/b/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/o?{query}",
            accept="application/json",
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Claude release bucket returned a non-object payload")
        items = payload.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    yield item
        next_page_token = payload.get("nextPageToken") if isinstance(payload.get("nextPageToken"), str) else None
        if next_page_token is None:
            return


def resolve_claude(*, timepoint_date: str, cutoff: dt.datetime, agent: str) -> SnapshotRow:
    versions: dict[str, dt.datetime] = {}
    for item in iter_claude_objects():
        name = item.get("name")
        created_raw = item.get("timeCreated")
        if not isinstance(name, str) or not isinstance(created_raw, str):
            continue
        parts = name.split("/")
        if len(parts) < 2:
            continue
        version = parts[1].strip()
        if not plain_version(version):
            continue
        created_at = parse_utc_date(created_raw)
        previous = versions.get(version)
        if previous is None or created_at > previous:
            versions[version] = created_at
    candidates = [(version, created_at) for version, created_at in versions.items() if created_at <= cutoff]
    best = choose_latest_by_time(candidates)
    if best is None:
        raise RuntimeError("no Claude Code version exists on or before the cutoff")
    return snapshot_row(
        timepoint_date=timepoint_date,
        cutoff=cutoff,
        agent=agent,
        install_version=best[0],
        status="confirmed",
        source_kind="gcs-object-prefix",
        source_ref=(
            "https://storage.googleapis.com/storage/v1/b/"
            "claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/o?prefix=claude-code-releases/"
        ),
        published_at=best[1],
        note="latest Claude Code version whose release objects were created on or before cutoff",
    )


def cursor_version_from_href(href: str, label: Optional[str]) -> Optional[str]:
    if label is not None:
        normalized = label.strip()
        if plain_version(normalized):
            return normalized
    slug = href.rsplit("/", 1)[-1]
    if re.fullmatch(r"\d+(?:-\d+)+", slug):
        return slug.replace("-", ".")
    return None


def iter_cursor_articles() -> Iterable[tuple[str, dt.datetime, str]]:
    page = 1
    while True:
        page_url = "https://cursor.com/changelog" if page == 1 else f"https://cursor.com/changelog/page/{page}"
        try:
            document = http_get_text(page_url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return
            raise
        matches = list(CURSOR_ARTICLE_RE.finditer(document))
        if not matches:
            return
        for match in matches:
            href = match.group("href")
            published_at = parse_utc_date(match.group("date"))
            label_match = CURSOR_LABEL_RE.search(match.group("meta"))
            label = html.unescape(label_match.group(1)).strip() if label_match is not None else None
            version = cursor_version_from_href(href, label)
            if version is None:
                continue
            yield version, published_at, f"https://cursor.com{href}"
        page += 1


def parse_cursor_release_document(document: str, expected_href: str) -> Optional[tuple[str, dt.datetime, str]]:
    match = CURSOR_ARTICLE_RE.search(document)
    if match is None:
        return None
    href = match.group("href")
    label_match = CURSOR_LABEL_RE.search(match.group("meta"))
    label = html.unescape(label_match.group(1)).strip() if label_match is not None else None
    version = cursor_version_from_href(href, label)
    if version is None:
        return None
    published_at = parse_utc_date(match.group("date"))
    return version, published_at, f"https://cursor.com{expected_href}"


def probe_cursor_release_page(version: str) -> Optional[tuple[str, dt.datetime, str]]:
    href = f"/changelog/{version.replace('.', '-')}"
    url = f"https://cursor.com{href}"
    try:
        document = http_get_text(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return parse_cursor_release_document(document, href)


def cursor_probe_candidates(highest_version: str) -> list[str]:
    parts = list(version_key(highest_version))
    while len(parts) < 2:
        parts.append(0)
    highest_major, highest_minor = parts[:2]
    candidates: list[str] = []
    for major in range(highest_major, max(-1, highest_major - 2), -1):
        minor_start = highest_minor if major == highest_major else 20
        for minor in range(minor_start, -1, -1):
            candidates.append(f"{major}.{minor}")
    return candidates


def resolve_cursor(*, timepoint_date: str, cutoff: dt.datetime, agent: str) -> SnapshotRow:
    paginated_articles = list(iter_cursor_articles())
    candidates = [item for item in paginated_articles if item[1] <= cutoff]
    if not candidates and paginated_articles:
        paginated_versions = {version for version, _published_at, _source_ref in paginated_articles}
        highest_seen = max((version for version, _published_at, _source_ref in paginated_articles), key=version_key)
        for candidate_version in cursor_probe_candidates(highest_seen):
            if candidate_version in paginated_versions:
                continue
            probed = probe_cursor_release_page(candidate_version)
            if probed is None:
                continue
            version, published_at, source_ref = probed
            if published_at <= cutoff:
                candidates.append((version, published_at, source_ref))
                break
    if not candidates:
        raise RuntimeError("no public Cursor release page exists on or before the cutoff")
    version, published_at, source_ref = max(candidates, key=lambda item: (item[1], version_key(item[0])))
    return snapshot_row(
        timepoint_date=timepoint_date,
        cutoff=cutoff,
        agent=agent,
        install_version=version,
        status="inferred",
        source_kind="cursor-changelog",
        source_ref=source_ref,
        published_at=published_at,
        note="public Cursor release on or before cutoff; exact agent build ID may still require a later translation step",
    )


def resolve_trae_oss(*, timepoint_date: str, cutoff: dt.datetime, agent: str) -> SnapshotRow:
    cutoff_text = format_utc(cutoff)
    payload = github_get_json(
        f"/repos/bytedance/trae-agent/commits?sha=main&until={urllib.parse.quote(cutoff_text)}&per_page=1"
    )
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("no trae-oss commit exists on or before the cutoff")
    commit = payload[0]
    if not isinstance(commit, dict):
        raise RuntimeError("GitHub commits API returned an invalid trae-oss item")
    sha = commit.get("sha")
    commit_payload = commit.get("commit")
    if not isinstance(sha, str) or not isinstance(commit_payload, dict):
        raise RuntimeError("GitHub commits API omitted trae-oss sha metadata")
    committer_payload = commit_payload.get("committer")
    if not isinstance(committer_payload, dict) or not isinstance(committer_payload.get("date"), str):
        raise RuntimeError("GitHub commits API omitted trae-oss commit date")
    published_at = parse_utc_date(committer_payload["date"])
    return snapshot_row(
        timepoint_date=timepoint_date,
        cutoff=cutoff,
        agent=agent,
        install_version=sha,
        status="confirmed",
        source_kind="github-commit-before-date",
        source_ref=f"https://github.com/bytedance/trae-agent/commits/main?until={cutoff_text}",
        published_at=published_at,
        note="use exact git commit because upstream repo does not publish a matching release/tag stream for this cutoff",
    )


def parse_rfc7231(value: str) -> Optional[dt.datetime]:
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def head_probe(url: str, version: str) -> HeadProbeResult:
    try:
        with http_request(url, method="HEAD") as response:
            last_modified_raw = response.headers.get("Last-Modified")
            last_modified = parse_rfc7231(last_modified_raw) if last_modified_raw else None
            return HeadProbeResult(version=version, url=url, last_modified=last_modified, exists=True)
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 404}:
            return HeadProbeResult(version=version, url=url, last_modified=None, exists=False)
        raise


def candidate_versions_from_hint(
    hint: str,
    *,
    minor_window: int,
    patch_max: int,
) -> list[str]:
    parts = list(version_key(hint))
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts[:3]
    versions: list[str] = []
    lower_minor = max(0, minor - minor_window)
    for current_minor in range(minor, lower_minor - 1, -1):
        current_patch_max = max(patch, patch_max) if current_minor == minor else patch_max
        for current_patch in range(current_patch_max, -1, -1):
            versions.append(f"{major}.{current_minor}.{current_patch}")
    return versions


def choose_probe_candidate(
    *,
    candidates: list[str],
    cutoff: dt.datetime,
    url_builder: Callable[[str], str],
    batch_size: int = 16,
) -> Optional[HeadProbeResult]:
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch) or 1) as executor:
            futures = {
                version: executor.submit(head_probe, url_builder(version), version)
                for version in batch
            }
            ordered_results = [futures[version].result() for version in batch]
        for result in ordered_results:
            if not result.exists or result.last_modified is None:
                continue
            if result.last_modified <= cutoff:
                return result
    return None


def current_factory_version_hint() -> str:
    installer = http_get_text("https://app.factory.ai/cli")
    match = re.search(r'VER="([^"]+)"', installer)
    if match is None:
        raise RuntimeError("Factory installer no longer exposes VER")
    return match.group(1).strip()


def current_trae_cn_version_hint() -> str:
    hint = http_get_text("https://lf-cdn.trae.com.cn/obj/trae-com-cn/trae-cli/trae-cli_latest_version.txt").strip()
    return hint[1:] if hint.startswith("v") else hint


def resolve_factory(
    *,
    timepoint_date: str,
    cutoff: dt.datetime,
    agent: str,
    extra_candidates: dict[str, list[str]],
) -> SnapshotRow:
    hint = current_factory_version_hint()
    candidates = extra_candidates.get(agent, []) + candidate_versions_from_hint(hint, minor_window=120, patch_max=5)
    deduped_candidates = list(dict.fromkeys(candidates))
    result = choose_probe_candidate(
        candidates=deduped_candidates,
        cutoff=cutoff,
        url_builder=lambda version: f"https://downloads.factory.ai/factory-cli/releases/{version}/linux/x64-baseline/droid",
    )
    if result is None:
        return pending_row(
            timepoint_date=timepoint_date,
            cutoff=cutoff,
            agent=agent,
            source_kind="cdn-head-scan",
            source_ref="https://downloads.factory.ai/factory-cli/releases/",
            note=(
                "no Factory release binary matched on or before cutoff inside the probed version window; "
                "add --extra-candidate factory=<version> or widen the script search"
            ),
        )
    return snapshot_row(
        timepoint_date=timepoint_date,
        cutoff=cutoff,
        agent=agent,
        install_version=result.version,
        status="confirmed",
        source_kind="cdn-head-scan",
        source_ref=result.url,
        published_at=result.last_modified,
        note="latest confirmed release binary whose Last-Modified is on or before cutoff within the probed version window",
    )


def resolve_trae_cn(
    *,
    timepoint_date: str,
    cutoff: dt.datetime,
    agent: str,
    extra_candidates: dict[str, list[str]],
) -> SnapshotRow:
    hint = current_trae_cn_version_hint()
    candidates = extra_candidates.get(agent, []) + candidate_versions_from_hint(hint, minor_window=220, patch_max=20)
    deduped_candidates = list(dict.fromkeys(candidates))
    result = choose_probe_candidate(
        candidates=deduped_candidates,
        cutoff=cutoff,
        url_builder=lambda version: (
            "https://lf-cdn.trae.com.cn/obj/trae-com-cn/trae-cli/"
            f"trae-cli_{version}_linux_amd64.tar.gz"
        ),
    )
    if result is None:
        return pending_row(
            timepoint_date=timepoint_date,
            cutoff=cutoff,
            agent=agent,
            source_kind="cdn-head-scan",
            source_ref="https://lf-cdn.trae.com.cn/obj/trae-com-cn/trae-cli/",
            note=(
                "no trae-cn archive matched on or before cutoff inside the probed version window; "
                "add --extra-candidate trae-cn=<version> or widen the script search"
            ),
        )
    return snapshot_row(
        timepoint_date=timepoint_date,
        cutoff=cutoff,
        agent=agent,
        install_version=result.version,
        status="confirmed",
        source_kind="cdn-head-scan",
        source_ref=result.url,
        published_at=result.last_modified,
        note="latest confirmed archive whose Last-Modified is on or before cutoff within the probed version window",
    )


AGENT_SPECS: dict[str, AgentSpec] = {
    "aider": AgentSpec(
        kind="pypi",
        selector="aider-chat",
        source_kind="pypi-release",
        source_ref="https://pypi.org/project/aider-chat/",
        note="latest plain PyPI release on or before cutoff",
    ),
    "auggie": AgentSpec(
        kind="npm",
        selector="@augmentcode/auggie",
        source_kind="npm-publish",
        source_ref="https://www.npmjs.com/package/@augmentcode/auggie",
        note="latest plain npm version on or before cutoff",
    ),
    "claude": AgentSpec(
        kind="claude",
        source_kind="gcs-object-prefix",
        source_ref=(
            "https://storage.googleapis.com/storage/v1/b/"
            "claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/o?prefix=claude-code-releases/"
        ),
        note="latest Claude Code version whose release objects were created on or before cutoff",
    ),
    "codebuddy": AgentSpec(
        kind="npm",
        selector="@tencent-ai/codebuddy-code",
        source_kind="npm-publish",
        source_ref="https://www.npmjs.com/package/@tencent-ai/codebuddy-code",
        note="latest plain npm version on or before cutoff",
    ),
    "codex": AgentSpec(
        kind="npm",
        selector="@openai/codex",
        source_kind="npm-publish",
        source_ref="https://www.npmjs.com/package/@openai/codex",
        note="latest plain generic npm version on or before cutoff; excludes platform-specific builds",
    ),
    "continue": AgentSpec(
        kind="npm",
        selector="@continuedev/cli",
        source_kind="npm-publish",
        source_ref="https://www.npmjs.com/package/@continuedev/cli",
        note="latest plain npm version on or before cutoff",
    ),
    "copilot": AgentSpec(
        kind="github-release",
        selector="github/copilot-cli",
        strip_v=True,
        source_kind="github-release",
        source_ref="https://github.com/github/copilot-cli/releases",
        note="pass to cakit without leading v; upstream install script prefixes v automatically",
    ),
    "crush": AgentSpec(
        kind="npm",
        selector="@charmland/crush",
        source_kind="npm-publish",
        source_ref="https://www.npmjs.com/package/@charmland/crush",
        note="latest plain npm version on or before cutoff",
    ),
    "cursor": AgentSpec(
        kind="cursor",
        source_kind="cursor-changelog",
        source_ref="https://cursor.com/changelog",
        note="public Cursor release on or before cutoff; exact agent build ID may still require a later translation step",
        status="inferred",
    ),
    "deepagents": AgentSpec(
        kind="pypi",
        selector="deepagents-cli",
        source_kind="pypi-release",
        source_ref="https://pypi.org/project/deepagents-cli/",
        note="latest plain PyPI release on or before cutoff",
    ),
    "factory": AgentSpec(
        kind="factory",
        source_kind="cdn-head-scan",
        source_ref="https://downloads.factory.ai/factory-cli/releases/",
        note="latest confirmed release binary whose Last-Modified is on or before cutoff within the probed version window",
    ),
    "gemini": AgentSpec(
        kind="npm",
        selector="@google/gemini-cli",
        source_kind="npm-publish",
        source_ref="https://www.npmjs.com/package/@google/gemini-cli",
        note="latest plain npm version on or before cutoff",
    ),
    "goose": AgentSpec(
        kind="github-release",
        selector="block/goose",
        strip_v=True,
        source_kind="github-release",
        source_ref="https://github.com/block/goose/releases",
        note="pass to cakit without leading v; cakit normalizes goose tags to v-prefixed downloads",
    ),
    "kilocode": AgentSpec(
        kind="npm",
        selector="@kilocode/cli",
        source_kind="npm-publish",
        source_ref="https://www.npmjs.com/package/@kilocode/cli",
        note="latest plain npm version on or before cutoff",
    ),
    "kimi": AgentSpec(
        kind="pypi",
        selector="kimi-cli",
        source_kind="pypi-release",
        source_ref="https://pypi.org/project/kimi-cli/",
        note="latest plain PyPI release on or before cutoff",
    ),
    "openclaw": AgentSpec(
        kind="npm",
        selector="openclaw",
        source_kind="npm-publish",
        source_ref="https://www.npmjs.com/package/openclaw",
        note="installer resolves versions from npm package releases",
    ),
    "opencode": AgentSpec(
        kind="github-release",
        selector="anomalyco/opencode",
        strip_v=True,
        source_kind="github-release",
        source_ref="https://github.com/anomalyco/opencode/releases",
        note="pass plain version; upstream installer strips/normalizes leading v internally",
    ),
    "openhands": AgentSpec(
        kind="pypi",
        selector="openhands",
        source_kind="pypi-release",
        source_ref="https://pypi.org/project/openhands/",
        note="latest plain PyPI release on or before cutoff",
    ),
    "qoder": AgentSpec(
        kind="npm",
        selector="@qoder-ai/qodercli",
        source_kind="npm-publish",
        source_ref="https://www.npmjs.com/package/@qoder-ai/qodercli",
        note="qoder versioned installs should use npm package versions, not the shell manifest",
    ),
    "qwen": AgentSpec(
        kind="npm",
        selector="@qwen-code/qwen-code",
        source_kind="npm-publish",
        source_ref="https://www.npmjs.com/package/@qwen-code/qwen-code",
        note="latest plain npm version on or before cutoff",
    ),
    "swe-agent": AgentSpec(
        kind="github-release",
        selector="SWE-agent/SWE-agent",
        source_kind="github-release",
        source_ref="https://github.com/SWE-agent/SWE-agent/releases",
        note="use exact git ref tag with leading v",
    ),
    "trae-cn": AgentSpec(
        kind="trae-cn",
        source_kind="cdn-head-scan",
        source_ref="https://lf-cdn.trae.com.cn/obj/trae-com-cn/trae-cli/",
        note="latest confirmed archive whose Last-Modified is on or before cutoff within the probed version window",
    ),
    "trae-oss": AgentSpec(
        kind="trae-oss",
        source_kind="github-commit-before-date",
        source_ref="https://github.com/bytedance/trae-agent/commits/main",
        note="use exact git commit because upstream repo does not publish a matching release/tag stream for this cutoff",
    ),
}


AGENT_ORDER = [
    "aider",
    "auggie",
    "claude",
    "codebuddy",
    "codex",
    "continue",
    "copilot",
    "crush",
    "cursor",
    "deepagents",
    "factory",
    "gemini",
    "goose",
    "kilocode",
    "kimi",
    "openclaw",
    "opencode",
    "openhands",
    "qoder",
    "qwen",
    "swe-agent",
    "trae-cn",
    "trae-oss",
]


def resolve_agent(
    *,
    agent: str,
    timepoint_date: str,
    cutoff: dt.datetime,
    extra_candidates: dict[str, list[str]],
) -> SnapshotRow:
    spec = AGENT_SPECS[agent]
    if spec.kind == "npm":
        return resolve_npm(spec, timepoint_date=timepoint_date, cutoff=cutoff, agent=agent)
    if spec.kind == "pypi":
        return resolve_pypi(spec, timepoint_date=timepoint_date, cutoff=cutoff, agent=agent)
    if spec.kind == "github-release":
        return resolve_github_release(spec, timepoint_date=timepoint_date, cutoff=cutoff, agent=agent)
    if spec.kind == "claude":
        return resolve_claude(timepoint_date=timepoint_date, cutoff=cutoff, agent=agent)
    if spec.kind == "cursor":
        return resolve_cursor(timepoint_date=timepoint_date, cutoff=cutoff, agent=agent)
    if spec.kind == "factory":
        return resolve_factory(
            timepoint_date=timepoint_date,
            cutoff=cutoff,
            agent=agent,
            extra_candidates=extra_candidates,
        )
    if spec.kind == "trae-cn":
        return resolve_trae_cn(
            timepoint_date=timepoint_date,
            cutoff=cutoff,
            agent=agent,
            extra_candidates=extra_candidates,
        )
    if spec.kind == "trae-oss":
        return resolve_trae_oss(timepoint_date=timepoint_date, cutoff=cutoff, agent=agent)
    raise RuntimeError(f"unsupported resolver kind: {spec.kind}")


def main() -> int:
    args = parse_args()
    cutoff = normalize_cutoff(args.timepoint_date, args.cutoff_utc)
    agents = normalize_agent_list(args.agent)
    extra_candidates = parse_extra_candidates(args.extra_candidate)

    rows: list[SnapshotRow] = []
    for agent in agents:
        spec = AGENT_SPECS[agent]
        try:
            rows.append(
                resolve_agent(
                    agent=agent,
                    timepoint_date=args.timepoint_date,
                    cutoff=cutoff,
                    extra_candidates=extra_candidates,
                )
            )
        except Exception as exc:  # noqa: BLE001
            rows.append(
                pending_row(
                    timepoint_date=args.timepoint_date,
                    cutoff=cutoff,
                    agent=agent,
                    source_kind=spec.source_kind,
                    source_ref=spec.source_ref,
                    note=str(exc),
                )
            )

    if args.include_comments:
        print("# Historical install-version snapshots for install-script Docker coverage.")
        print("# Columns:")
        print("# timepoint_date\tcutoff_utc\tagent\tinstall_version\tstatus\tsource_kind\tsource_ref\tpublished_at_utc\tnote")
    elif args.include_header:
        print("timepoint_date\tcutoff_utc\tagent\tinstall_version\tstatus\tsource_kind\tsource_ref\tpublished_at_utc\tnote")

    for row in rows:
        print(row.to_tsv())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
