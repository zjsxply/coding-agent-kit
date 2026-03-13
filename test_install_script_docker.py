from __future__ import annotations

import os
import time
from functools import lru_cache
from pathlib import Path

import docker
import pytest
from docker.errors import APIError, DockerException


DEFAULT_DOCKER_IMAGES = (
    "ubuntu:20.04",
    "ubuntu:22.04",
    "ubuntu:24.04",
    "debian:11-slim",
    "debian:12-slim",
    "alpine:3.16",
    "alpine:3.20",
    "rockylinux:9",
    "fedora:40",
    "opensuse/leap:15.6",
    "archlinux:latest",
)
DOCKER_TEST_TIMEOUT_SECONDS = 15 * 60


def _env_truthy(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _docker_images() -> tuple[str, ...]:
    raw_value = os.environ.get("CAKIT_INSTALL_TEST_IMAGES")
    if raw_value is None or not raw_value.strip():
        return DEFAULT_DOCKER_IMAGES
    images = tuple(item.strip() for item in raw_value.split(",") if item.strip())
    return images or DEFAULT_DOCKER_IMAGES


@lru_cache(maxsize=1)
def _docker_client() -> docker.DockerClient:
    if not _env_truthy("CAKIT_RUN_DOCKER_INSTALL_TESTS"):
        pytest.skip("set CAKIT_RUN_DOCKER_INSTALL_TESTS=1 to run Docker install tests")

    try:
        client = docker.from_env(timeout=DOCKER_TEST_TIMEOUT_SECONDS)
        client.ping()
    except DockerException as exc:
        pytest.skip(f"docker daemon is unavailable: {exc}")
    return client


def _container_logs(container: docker.models.containers.Container) -> str:
    return container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")


def _wait_for_container(container: docker.models.containers.Container) -> int:
    deadline = time.monotonic() + DOCKER_TEST_TIMEOUT_SECONDS
    while True:
        container.reload()
        if container.status == "exited":
            result = container.wait()
            return int(result.get("StatusCode", 1))
        if time.monotonic() >= deadline:
            container.kill()
            pytest.fail(f"docker install validation timed out:\n{_container_logs(container)}")
        time.sleep(1)


@pytest.mark.parametrize("image", _docker_images())
def test_install_script_installs_cakit_in_docker(image: str) -> None:
    client = _docker_client()
    repo_root = Path(__file__).resolve().parent

    try:
        client.images.pull(image, platform="linux/amd64")
    except APIError as exc:
        explanation = getattr(exc, "explanation", str(exc))
        pytest.fail(f"docker pull failed for {image}:\n{explanation}")

    shell_script = """
set -eu
export DEBIAN_FRONTEND=noninteractive
sh ./install.sh
command -v cakit
cakit --help >/tmp/cakit-help.txt
cakit install --help >/tmp/cakit-install-help.txt
grep -q "Coding Agent Kit CLI" /tmp/cakit-help.txt
grep -q "Install a coding agent" /tmp/cakit-install-help.txt
"""
    container = client.containers.create(
        image=image,
        command=["sh", "-lc", shell_script],
        working_dir="/work/cakit",
        volumes={str(repo_root): {"bind": "/work/cakit", "mode": "rw"}},
        platform="linux/amd64",
        detach=True,
    )
    try:
        container.start()
        status_code = _wait_for_container(container)
        if status_code != 0:
            pytest.fail(f"docker install validation failed for {image}:\n{_container_logs(container)}")
    finally:
        container.remove(force=True)
