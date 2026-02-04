#!/usr/bin/env bash
set -u

echo "[test] coding-agent-kit smoke test"

if ! command -v cakit >/dev/null 2>&1; then
  echo "[error] cakit not found in PATH"
  exit 2
fi

if [ "$#" -gt 0 ]; then
  agents=("$@")
else
  agents=(codex claude copilot gemini kimi qwen openhands swe-agent trae-oss cursor)
fi

failed=0

for agent in "${agents[@]}"; do
  echo "\n[test] agent=${agent}"
  echo "[test] cmd: cakit run ${agent} \"hi\""
  cakit run "${agent}" "hi"
  rc=$?
  if [ $rc -eq 0 ]; then
    echo "[test] result: OK"
  else
    echo "[test] result: FAIL (exit=${rc})"
    failed=1
  fi
done

echo "\n[test] done"
exit $failed
