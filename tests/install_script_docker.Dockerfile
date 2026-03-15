# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG CACHE_KEY
ARG TEST_MODE
ARG VERSION_SNAPSHOT_DATE
ENV TEST_MODE=${TEST_MODE}
ENV VERSION_SNAPSHOT_DATE=${VERSION_SNAPSHOT_DATE}

WORKDIR /work/cakit
COPY . /work/cakit

RUN --mount=type=cache,id=${CACHE_KEY}-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-apk-cache,target=/var/cache/apk,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-dnf-cache,target=/var/cache/dnf,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-yum-cache,target=/var/cache/yum,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-zypp-cache,target=/var/cache/zypp,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-pacman-cache,target=/var/cache/pacman/pkg,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-pacman-sync,target=/var/lib/pacman/sync,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-opt-cakit-cache,target=/opt/cakit/cache,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-opt-cakit-node,target=/opt/cakit/node,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-opt-cakit-tools,target=/opt/cakit/tools,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-opt-cakit-python,target=/opt/cakit/python,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-opt-cakit-uv,target=/opt/cakit/uv,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-root-local-bin,target=/root/.local/bin,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-root-local-share-uv,target=/root/.local/share/uv,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-root-cache-pip,target=/root/.cache/pip,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-root-cache-uv,target=/root/.cache/uv,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-root-npm-cache,target=/root/.npm,sharing=locked \
    --mount=type=cache,id=${CACHE_KEY}-root-npm-prefix,target=/root/.npm-global,sharing=locked \
    sh -lc 'set -eu; sh ./tests/install_script_docker_runner.sh "$TEST_MODE"'
