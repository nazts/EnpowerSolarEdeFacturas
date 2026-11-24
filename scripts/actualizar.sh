#!/usr/bin/env bash
# Actualiza el panel con lo último del repositorio. Se corre desde cualquier lado:
#
#     bash ~/enpower/scripts/actualizar.sh
#
# Hace: git pull → dependencias → migración de la base → reinicio del panel → control.
# Si algo falla, se detiene ahí (set -e) y muestra el error, sin dejar el panel a medias.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

rojo()  { printf '\033[31m%s\033[0m\n' "$*"; }
verde() { printf '\033[32m%s\033[0m\n' "$*"; }
paso()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

if [ ! -x ".venv/bin/python" ]; then
    rojo "No encuentro .venv/bin/python en $RAIZ"
    echo "Instalá primero con: bash scripts/instalar_wsl.sh --red"
    exit 1
fi

paso "1/5 Bajando los cambios del repositorio"
git pull --ff-only

paso "2/5 Instalando dependencias"
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
verde "dependencias al día"

paso "3/5 Actualizando la base de datos"
.venv/bin/python -m app.db --init

paso "4/5 Reiniciando el panel"
if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl restart enpower-panel
    sleep 2
    if systemctl is-active --quiet enpower-panel; then
        verde "enpower-panel activo"
    else
        rojo "el panel no arrancó — revisá: journalctl -u enpower-panel -n 40 --no-pager"
        exit 1
    fi
else
    echo "no hay systemd: reiniciá el panel a mano (bash scripts/correr_panel.sh)"
fi

paso "5/5 Versión en el servidor"
git log -1 --oneline
verde "listo"
