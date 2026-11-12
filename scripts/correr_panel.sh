#!/usr/bin/env bash
# Arranca el panel en primer plano (para probar o cuando no hay systemd).
set -euo pipefail
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

[[ -x .venv/bin/python ]] || { echo "Falta el entorno: bash scripts/instalar_wsl.sh"; exit 1; }
mkdir -p logs

exec .venv/bin/python run.py
