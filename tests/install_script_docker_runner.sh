#!/bin/sh
set -eu

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
  *)
    echo "unsupported Docker test mode: ${1:-}" >&2
    exit 1
    ;;
esac
