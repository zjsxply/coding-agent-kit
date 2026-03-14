from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import docker
import pytest
from docker.errors import DockerException


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
DOCKER_INSTALL_ALL_TIMEOUT_SECONDS = 30 * 60
REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKER_TEST_DOCKERFILE = REPO_ROOT / "tests" / "install_script_docker.Dockerfile"


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
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is unavailable")

    try:
        client = docker.from_env(timeout=DOCKER_TEST_TIMEOUT_SECONDS)
        client.ping()
    except DockerException as exc:
        pytest.skip(f"docker daemon is unavailable: {exc}")
    return client


def _docker_image_id(image: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", image)


def _run_docker_build_test(image: str, *, mode: str) -> None:
    _docker_client()
    env = os.environ.copy()
    env["DOCKER_BUILDKIT"] = "1"
    env["BUILDKIT_PROGRESS"] = "plain"
    cache_key = _docker_image_id(image).lower()
    timeout_seconds = DOCKER_INSTALL_ALL_TIMEOUT_SECONDS if mode == "install-all" else DOCKER_TEST_TIMEOUT_SECONDS
    command = [
        "docker",
        "build",
        "--pull",
        "--platform",
        "linux/amd64",
        "--progress=plain",
        "-f",
        str(DOCKER_TEST_DOCKERFILE),
        "--build-arg",
        f"BASE_IMAGE={image}",
        "--build-arg",
        f"CACHE_KEY={cache_key}",
        "--build-arg",
        f"TEST_MODE={mode}",
        str(REPO_ROOT),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = f"{exc.stdout or ''}{exc.stderr or ''}"
        pytest.fail(f"docker {mode} validation timed out for {image}:\n{output}")
    if result.returncode != 0:
        output = f"{result.stdout}{result.stderr}"
        pytest.fail(f"docker {mode} validation failed for {image}:\n{output}")


@pytest.mark.parametrize("image", _docker_images(), ids=_docker_image_id)
def test_install_script_installs_cakit_in_docker(image: str) -> None:
    _run_docker_build_test(image, mode="basic")


@pytest.mark.parametrize("image", _docker_images(), ids=_docker_image_id)
def test_install_script_installs_all_agents_in_docker(image: str) -> None:
    _run_docker_build_test(image, mode="install-all")
