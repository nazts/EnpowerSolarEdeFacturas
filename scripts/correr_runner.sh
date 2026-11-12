#!/usr/bin/env bash
# Corre la descarga diaria una vez (lo usa el timer de systemd y sirve a mano).
#
#   ./scripts/correr_runner.sh                 corrida normal (envia a Telegram)
#   ./scripts/correr_runner.sh --sin-envio     sin enviar nada (prueba)
set -uo pipefail
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

[[ -x .venv/bin/python ]] || { echo "Falta el entorno: bash scripts/instalar_wsl.sh"; exit 1; }
mkdir -p logs && chmod 700 logs 2>/dev/null || true
umask 077            # los logs pueden llevar NIC y nombres de clientes

ARCHIVO="logs/runner_$(date +%Y%m%d).log"
{
    echo "==== corrida $(date '+%Y-%m-%d %H:%M:%S') ===="
    .venv/bin/python -m app.runner "$@"
    CODIGO=$?
    echo "==== fin $(date '+%Y-%m-%d %H:%M:%S') (codigo $CODIGO) ===="
    exit $CODIGO
} 2>&1 | tee -a "$ARCHIVO"
