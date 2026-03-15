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
    CAKIT_INSTALL_SOURCE=/work/cakit sh ./install.sh
    command -v cakit
    cakit --help >/tmp/cakit-help.txt
    cakit install --help >/tmp/cakit-install-help.txt
    grep -q "Coding Agent Kit CLI" /tmp/cakit-help.txt
    grep -q "Install a coding agent" /tmp/cakit-install-help.txt
    ;;
  install-all)
    CAKIT_INSTALL_SOURCE=/work/cakit sh ./install.sh
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
  install-all-versioned)
    CAKIT_INSTALL_SOURCE=/work/cakit sh ./install.sh
    command -v cakit
    snapshot_date=${VERSION_SNAPSHOT_DATE:-}
    snapshot_file=./tests/install_script_version_snapshots.tsv
    [ -n "$snapshot_date" ]
    [ -f "$snapshot_file" ]
    if awk -F '	' -v date="$snapshot_date" '
      $0 ~ /^#/ { next }
      $1 == date && $5 != "confirmed" { print; bad = 1 }
      END { exit bad ? 0 : 1 }
    ' "$snapshot_file"; then
      echo "snapshot date $snapshot_date contains non-confirmed rows" >&2
      exit 1
    fi
    snapshot_rows=$(awk -F '	' -v date="$snapshot_date" '
      $0 ~ /^#/ { next }
      $1 == date { printf "%s\t%s\n", $3, $4 }
    ' "$snapshot_file")
    if [ -z "$snapshot_rows" ]; then
      echo "no snapshot rows found for $snapshot_date" >&2
      exit 1
    fi
    printf '%s\n' "$snapshot_rows" >/tmp/cakit-install-all-versioned.tsv
    while IFS='	' read -r agent version; do
      [ -n "$agent" ]
      [ -n "$version" ]
      echo "[install-all-versioned] installing ${agent}@${version} from snapshot ${snapshot_date}" >&2
      result_json="/tmp/cakit-install-${agent}.json"
      result_stderr="/tmp/cakit-install-${agent}.stderr"
      if ! cakit install "$agent" --version "$version" >"$result_json" 2>"$result_stderr"; then
        if [ -s "$result_json" ]; then
          cat "$result_json"
        fi
        if [ -s "$result_stderr" ]; then
          cat "$result_stderr" >&2
        fi
        exit 1
      fi
      if ! grep -q "\"agent\": \"${agent}\"" "$result_json" \
        || ! grep -q '"ok": true' "$result_json" \
        || ! grep -q "\"version\": \"${version}\"" "$result_json"; then
        cat "$result_json"
        if [ -s "$result_stderr" ]; then
          cat "$result_stderr" >&2
        fi
        exit 1
      fi
    done </tmp/cakit-install-all-versioned.tsv
    ;;
  tools)
    CAKIT_INSTALL_SOURCE=/work/cakit sh ./install.sh
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
