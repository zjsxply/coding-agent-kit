#!/usr/bin/env sh
set -eu

REPO_ARCHIVE_URL="https://github.com/zjsxply/coding-agent-kit/archive/refs/heads/main.tar.gz"
UV_INSTALLER_URL="https://astral.sh/uv/install.sh"

log() {
    printf '%s\n' "[cakit-install] $*" >&2
}

fail() {
    printf '%s\n' "[cakit-install] $*" >&2
    exit 1
}

have_cmd() {
    command -v "$1" >/dev/null 2>&1
}

run_quiet() {
    quiet_log=$(mktemp "${TMPDIR:-/tmp}/cakit-install.XXXXXX")
    if "$@" >"$quiet_log" 2>&1; then
        rm -f "$quiet_log"
        return 0
    fi
    quiet_status=$?
    log "command failed: $*"
    if [ -s "$quiet_log" ]; then
        cat "$quiet_log" >&2
    fi
    rm -f "$quiet_log"
    return "$quiet_status"
}

run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
        return
    fi
    if have_cmd sudo; then
        sudo "$@"
        return
    fi
    fail "need root or sudo to install system packages"
}

detect_package_manager() {
    for candidate in apt-get apk dnf microdnf yum zypper pacman; do
        if have_cmd "$candidate"; then
            printf '%s\n' "$candidate"
            return
        fi
    done
    printf '\n'
}

install_bootstrap_tools() {
    if have_cmd curl && have_cmd tar && have_cmd gzip; then
        return
    fi

    package_manager=$(detect_package_manager)
    case "$package_manager" in
        apt-get)
            run_quiet run_as_root env DEBIAN_FRONTEND=noninteractive apt-get -qq update
            run_quiet run_as_root env DEBIAN_FRONTEND=noninteractive apt-get -qq install -y ca-certificates curl tar gzip
            ;;
        apk)
            run_quiet run_as_root apk add --no-cache ca-certificates curl tar gzip
            ;;
        dnf)
            run_quiet run_as_root dnf install -y ca-certificates curl tar gzip
            ;;
        microdnf)
            run_quiet run_as_root microdnf install -y ca-certificates curl tar gzip
            ;;
        yum)
            run_quiet run_as_root yum install -y ca-certificates curl tar gzip
            ;;
        zypper)
            run_quiet run_as_root zypper --non-interactive install ca-certificates curl tar gzip
            ;;
        pacman)
            run_quiet run_as_root pacman -Sy --noconfirm ca-certificates curl tar gzip
            ;;
        *)
            fail "curl, tar, and gzip are required, and no supported package manager was detected"
            ;;
    esac
}

resolve_local_source() {
    source_path=$1
    if [ -d "$source_path" ] && [ -f "$source_path/pyproject.toml" ]; then
        (
            CDPATH= cd -- "$source_path"
            pwd
        )
        return
    fi
    printf '%s\n' "$source_path"
}

resolve_source() {
    if [ -n "${CAKIT_INSTALL_SOURCE:-}" ]; then
        resolve_local_source "$CAKIT_INSTALL_SOURCE"
        return
    fi

    case "$0" in
        */*)
            script_dir=$(
                CDPATH= cd -- "$(dirname -- "$0")"
                pwd
            )
            ;;
        *)
            script_dir=$(pwd)
            ;;
    esac

    if [ -f "$script_dir/pyproject.toml" ] && [ -f "$script_dir/README.md" ]; then
        printf '%s\n' "$script_dir"
        return
    fi

    printf '%s\n' "$REPO_ARCHIVE_URL"
}

default_install_home() {
    if [ -n "${CAKIT_INSTALL_HOME:-}" ]; then
        printf '%s\n' "$CAKIT_INSTALL_HOME"
        return
    fi
    if [ "$(id -u)" -eq 0 ]; then
        printf '%s\n' "/opt/cakit"
        return
    fi
    printf '%s\n' "$HOME/.local/share/cakit"
}

default_bin_dir() {
    if [ -n "${CAKIT_INSTALL_BIN_DIR:-}" ]; then
        printf '%s\n' "$CAKIT_INSTALL_BIN_DIR"
        return
    fi
    if [ "$(id -u)" -eq 0 ]; then
        printf '%s\n' "/usr/local/bin"
        return
    fi
    if [ -d "/usr/local/bin" ] && [ -w "/usr/local/bin" ]; then
        printf '%s\n' "/usr/local/bin"
        return
    fi
    printf '%s\n' "$HOME/.local/bin"
}

install_uv_if_needed() {
    if have_cmd uv; then
        command -v uv
        return
    fi

    install_bootstrap_tools >&2
    install_home=$1
    uv_root=${CAKIT_INSTALL_UV_DIR:-$install_home/uv}
    mkdir -p "$uv_root"

    log "uv not found; installing uv"
    run_quiet env UV_UNMANAGED_INSTALL="$uv_root" sh -c "curl -LsSf '$UV_INSTALLER_URL' | sh"

    uv_bin="$uv_root/uv"
    [ -x "$uv_bin" ] || fail "uv installation completed but the uv binary was not found"
    printf '%s\n' "$uv_bin"
}

ensure_cakit_installed() {
    install_home=$(default_install_home)
    bin_dir=$(default_bin_dir)
    source=$(resolve_source)
    uv_bin=$(install_uv_if_needed "$install_home")

    mkdir -p "$bin_dir" "$install_home/tools" "$install_home/python" "$install_home/cache"

    export UV_TOOL_BIN_DIR=$bin_dir
    export UV_TOOL_DIR=${CAKIT_INSTALL_TOOL_DIR:-$install_home/tools}
    export UV_PYTHON_INSTALL_DIR=${CAKIT_INSTALL_PYTHON_INSTALL_DIR:-$install_home/python}
    export UV_CACHE_DIR=${CAKIT_INSTALL_CACHE_DIR:-$install_home/cache}

    log "installing coding-agent-kit from $source"
    run_quiet "$uv_bin" tool install --force --from "$source" coding-agent-kit

    cakit_bin="$UV_TOOL_BIN_DIR/cakit"
    [ -x "$cakit_bin" ] || fail "cakit executable was not created at $cakit_bin"
    "$cakit_bin" --help >/dev/null

    log "installed cakit to $cakit_bin"
    case ":$PATH:" in
        *:"$UV_TOOL_BIN_DIR":*)
            ;;
        *)
            log "add $UV_TOOL_BIN_DIR to PATH in new shells to use cakit directly"
            ;;
    esac
}

ensure_cakit_installed "$@"
