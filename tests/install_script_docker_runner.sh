#!/bin/sh
set -eu

require_command() {
  command -v "$1" >/dev/null 2>&1
}

require_command_any() {
  for candidate in "$@"; do
    if command -v "$candidate" >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

case "${1:-}" in
  basic)
    sh ./install.sh
    command -v cakit
    cakit --help >/tmp/cakit-help.txt
    cakit install --help >/tmp/cakit-install-help.txt
    grep -q "Coding Agent Kit CLI" /tmp/cakit-help.txt
    grep -q "Install a coding agent" /tmp/cakit-install-help.txt
    ;;
  install-all)
    sh ./install.sh
    command -v cakit
    if ! cakit install all >/tmp/cakit-install-all.json 2>/tmp/cakit-install-all.stderr; then
      if [ -s /tmp/cakit-install-all.json ]; then
        cat /tmp/cakit-install-all.json
      fi
      if [ -s /tmp/cakit-install-all.stderr ]; then
        cat /tmp/cakit-install-all.stderr >&2
      fi
      exit 1
    fi
    grep -q '"agent": "all"' /tmp/cakit-install-all.json
    grep -q '"ok": true' /tmp/cakit-install-all.json
    grep -q '"parallel": true' /tmp/cakit-install-all.json
    grep -q '"results": \[' /tmp/cakit-install-all.json
    ;;
  tools)
    sh ./install.sh
    command -v cakit
    if ! cakit tools >/tmp/cakit-tools.json 2>/tmp/cakit-tools.stderr; then
      if [ -s /tmp/cakit-tools.json ]; then
        cat /tmp/cakit-tools.json
      fi
      if [ -s /tmp/cakit-tools.stderr ]; then
        cat /tmp/cakit-tools.stderr >&2
      fi
      exit 1
    fi
    grep -q '"ok": true' /tmp/cakit-tools.json
    require_command rg
    require_command_any fd fdfind
    require_command fzf
    require_command jq
    require_command yq
    require_command_any bat batcat
    require_command git
    require_command git-lfs
    require_command delta
    require_command gh
    require_command sg
    ;;
  *)
    echo "unsupported Docker test mode: ${1:-}" >&2
    exit 1
    ;;
esac
