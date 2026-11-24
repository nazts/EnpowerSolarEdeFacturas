#!/usr/bin/env bash
# Enciende, apaga, reinicia o mira el panel y la descarga diaria.
#
#     bash scripts/servidor.sh estado      # ¿está corriendo? (lo más usado)
#     bash scripts/servidor.sh encender    # arranca panel + timer de las 07:00
#     bash scripts/servidor.sh apagar      # detiene todo
#     bash scripts/servidor.sh reiniciar   # reinicia el panel
#     bash scripts/servidor.sh ahora       # corre la descarga de facturas ya mismo
#     bash scripts/servidor.sh logs        # últimas 60 líneas del panel
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"
PANEL="enpower-panel"
TIMER="enpower-runner.timer"

negrita() { printf '\n\033[1m%s\033[0m\n' "$*"; }
verde()   { printf '\033[32m%s\033[0m\n' "$*"; }
rojo()    { printf '\033[31m%s\033[0m\n' "$*"; }

if ! command -v systemctl >/dev/null 2>&1; then
    rojo "No hay systemd en este WSL."
    echo "Arrancá el panel en primer plano con:"
    echo "    bash $RAIZ/scripts/correr_panel.sh"
    exit 1
fi

case "${1:-estado}" in
encender)
    negrita "Encendiendo el panel y la descarga diaria"
    sudo systemctl enable --now "$PANEL" && verde "panel encendido"
    sudo systemctl enable --now "$TIMER" && verde "descarga diaria activa"
    "$0" estado
    ;;
apagar)
    negrita "Apagando"
    sudo systemctl stop "$PANEL" "$TIMER" && verde "detenido"
    ;;
reiniciar)
    negrita "Reiniciando el panel"
    sudo systemctl restart "$PANEL" && sleep 2
    if systemctl is-active --quiet "$PANEL"; then
        verde "panel activo"
        curl -s -o /dev/null -w "responde HTTP %{http_code}\n" http://127.0.0.1:8000/ || true
    else
        rojo "no arrancó"; journalctl -u "$PANEL" -n 30 --no-pager
        exit 1
    fi
    ;;
ahora)
    negrita "Corriendo la descarga de facturas ahora"
    sudo systemctl start --no-block enpower-runner
    verde "lanzada (mirá el avance en el panel o con: $0 logs)"
    ;;
logs)
    journalctl -u "$PANEL" -n 60 --no-pager
    ;;
estado)
    negrita "Panel"
    systemctl status "$PANEL" --no-pager -n 0 | head -6
    negrita "Descarga diaria"
    systemctl list-timers "$TIMER" --no-pager 2>/dev/null | head -3 || echo "timer no instalado"
    negrita "¿Responde?"
    curl -s -o /dev/null -w "http://127.0.0.1:8000/ → HTTP %{http_code}\n" http://127.0.0.1:8000/ \
        || echo "no responde (¿está apagado?)"
    ;;
*)
    echo "Uso: bash scripts/servidor.sh {estado|encender|apagar|reiniciar|ahora|logs}"
    exit 2
    ;;
esac
