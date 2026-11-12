#!/usr/bin/env bash
# Deja el panel accesible por Tailscale (red privada, sin abrir puertos).
#
#   bash scripts/instalar_tailscale.sh                     instala y explica
#   bash scripts/instalar_tailscale.sh tskey-auth-XXXX      ademas se une a la red
#   bash scripts/instalar_tailscale.sh --puerto 8000        otro puerto del panel
#   bash scripts/instalar_tailscale.sh --sin-serve          no publica el panel
#
# Lo que hace:
#   1. instala tailscale (si falta)
#   2. se une a tu tailnet con la auth key
#   3. publica el panel local con `tailscale serve` (HTTPS y nombre .ts.net)
#
# El panel sigue escuchando solo en 127.0.0.1: no hay que abrirlo a la red.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PUERTO=""
AUTHKEY=""
SERVE=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --puerto) PUERTO="${2:?--puerto necesita un numero}"; shift 2 ;;
        --sin-serve) SERVE=0; shift ;;
        -h|--help) awk 'NR>1 && /^set -euo/{exit} NR>1' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        tskey-*) AUTHKEY="$1"; shift ;;
        *) echo "Opcion desconocida: $1"; exit 1 ;;
    esac
done

cyan() { printf '\n\033[36m== %s\033[0m\n' "$1"; }
ok()   { printf '   \033[32mOK\033[0m  %s\n' "$1"; }
warn() { printf '   \033[33m!!\033[0m  %s\n' "$1"; }
fail() { printf '   \033[31mXX\033[0m  %s\n' "$1"; exit 1; }

[[ "$(uname -s)" == "Linux" ]] || fail "Esto es para Linux/WSL."
SUDO=""
[[ $EUID -ne 0 ]] && SUDO="sudo"

# puerto del .env si no se paso
if [[ -z "$PUERTO" ]]; then
    PUERTO="$(grep -E '^PORT=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2 || true)"
    PUERTO="${PUERTO:-8000}"
fi

cyan "Revisando Tailscale"
if command -v tailscale >/dev/null 2>&1; then
    ok "tailscale $(tailscale version | head -1)"
else
    echo "   instalando..."
    curl -fsSL https://tailscale.com/install.sh | $SUDO sh
    command -v tailscale >/dev/null 2>&1 || fail "no se pudo instalar tailscale"
    ok "instalado"
fi

# el demonio necesita /dev/net/tun; en WSL suele faltar si no hay systemd
if [[ ! -e /dev/net/tun ]]; then
    warn "no existe /dev/net/tun (tun no cargado)"
    $SUDO modprobe tun 2>/dev/null || true
    if [[ ! -e /dev/net/tun ]]; then
        warn "WSL sin tun: se puede usar modo usuarios, pero entonces hay que"
        warn "publicar con: sudo tailscale serve --bg $PUERTO"
        warn "   (ver docs/tailscale.md, seccion 'WSL sin tun')"
    fi
fi

cyan "Servicio"
if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    $SUDO systemctl enable --now tailscaled >/dev/null 2>&1 \
        && ok "tailscaled activo" || warn "no se pudo activar tailscaled"
else
    warn "sin systemd: arrancalo a mano con  sudo tailscaled &"
fi

cyan "Conexion a la red"
estado="$(tailscale status 2>/dev/null | head -1 || true)"
if tailscale ip -4 >/dev/null 2>&1; then
    ok "ya esta conectado: $(tailscale ip -4 | head -1)"
elif [[ -n "$AUTHKEY" ]]; then
    $SUDO tailscale up --authkey "$AUTHKEY" --accept-routes=false
    ok "conectado: $(tailscale ip -4 | head -1)"
else
    warn "falta unirte a la red. Opciones:"
    warn "  a) interactivo:  sudo tailscale up    (abre un link para aprobar)"
    warn "  b) con clave:    bash scripts/instalar_tailscale.sh tskey-auth-XXXX"
fi

if [[ $SERVE -eq 1 ]] && tailscale ip -4 >/dev/null 2>&1; then
    cyan "Publicando el panel dentro de la red Tailscale"
    if $SUDO tailscale serve --bg "$PUERTO" >/dev/null 2>&1 \
       || $SUDO tailscale serve --bg "http://127.0.0.1:$PUERTO" >/dev/null 2>&1; then
        ok "panel publicado"
    else
        warn "no se pudo publicar con 'tailscale serve' (revisa la version instalada)"
    fi
    echo
    tailscale serve status 2>/dev/null | sed 's/^/   /' || true
fi

NOMBRE="$(tailscale status --json 2>/dev/null | grep -o '"DNSName":"[^"]*"' | head -1 | cut -d'"' -f4 || true)"
IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"

printf '\n==============================================================\n'
printf '  Listo.\n'
printf '  IP Tailscale:  %s\n' "${IP:-(sin conectar)}"
printf '  Nombre:        %s\n' "${NOMBRE:-ver 'tailscale status'}"
printf '  Panel:         https://%s  (con serve)  o  http://%s:%s\n' \
    "${NOMBRE:-nombre.ts.net}" "${IP:-100.x.y.z}" "$PUERTO"
printf '\n  En cada PC: instalar Tailscale, entrar con la misma cuenta y abrir esa URL.\n'
printf '  Con serve el panel NO se abre a la red: sigue en 127.0.0.1.\n'
printf '==============================================================\n'
