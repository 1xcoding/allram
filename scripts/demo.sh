#!/usr/bin/env bash
# Запись консольной демонстрации AllRAM (asciinema или script).
# Требует: pip install asciinema  (или util-linux: script)
set -euo pipefail
cd "$(dirname "$0")/.."
export ALLRAM_HOME="${ALLRAM_HOME:-/tmp/allram-demo-home}"
export ALLRAM_NO_COLOR=1
rm -rf "$ALLRAM_HOME" 2>/dev/null || true

commands=(
  "python3 -m allram --version"
  "python3 -m allram status"
  "python3 -m allram bench --ram-mib 16 --disk-mib 8"
  "python3 -m allram presets"
  "python3 -m allram models --model 8b"
  "python3 -m allram explain"
)

if command -v asciinema >/dev/null 2>&1; then
  asciinema rec --overwrite -c "bash -lc '$(printf '%s\n' "${commands[@]}" | paste -sd ';')'" docs/demo.cast
  echo "Записано: docs/demo.cast"
else
  bash -lc "$(printf '%s\n' "${commands[@]}" | paste -sd ';')" 2>&1 | tee docs/demo.txt
  echo "asciinema не найден — сохранён текстовый лог docs/demo.txt"
fi
