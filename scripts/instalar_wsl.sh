#!/usr/bin/env bash
# Instalador del panel de Enpower Solar en Linux / WSL (Ubuntu).
#
#   bash scripts/instalar_wsl.sh                 instala (panel + corrida diaria)
#   bash scripts/instalar_wsl.sh --puerto 8080   cambia el puerto del panel
#   bash scripts/instalar_wsl.sh --red           deja el panel visible en la red (HOST=0.0.0.0)
#   bash scripts/instalar_wsl.sh --zona          pone la zona horaria America/Santo_Domingo
#   bash scripts/instalar_wsl.sh --env RUTA      usa otro archivo de credenciales
#   bash scripts/instalar_wsl.sh --sin-servicio  no crea los servicios de systemd
#
# Es idempotente: se puede correr las veces que haga falta.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PUERTO=""
RED=0
SIN_SERVICIO=0
ZONA=0
ARCHIVO_ENV=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --puerto) PUERTO="${2:?--puerto necesita un numero}"; shift 2 ;;
        --red) RED=1; shift ;;
        --sin-servicio) SIN_SERVICIO=1; shift ;;
        --zona) ZONA=1; shift ;;
        --env) ARCHIVO_ENV="${2:?--env necesita una ruta}"; shift 2 ;;
        -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Opcion desconocida: $1"; exit 1 ;;
    esac
done

cyan() { printf '\n\033[36m== %s\033[0m\n' "$1"; }
ok()   { printf '   \033[32mOK\033[0m  %s\n' "$1"; }
warn() { printf '   \033[33m!!\033[0m  %s\n' "$1"; }
fail() { printf '   \033[31mXX\033[0m  %s\n' "$1"; exit 1; }

if [[ -n "$PUERTO" ]]; then
    [[ "$PUERTO" =~ ^[0-9]+$ ]] || fail "Puerto invalido: $PUERTO (tiene que ser numerico)"
    # aritmetica sin (( )) para que set -e no aborte con valores fuera de rango
    if [[ "$PUERTO" -lt 1 || "$PUERTO" -gt 65535 ]]; then
        fail "Puerto fuera de rango: $PUERTO (1 a 65535)"
    fi
fi

printf '==============================================================\n'
printf '  Enpower Solar - instalacion en Linux/WSL\n'
printf '  carpeta: %s\n' "$RAIZ"
printf '==============================================================\n'

# ------------------------------------------------------------------ 1. sistema
cyan "Revisando el sistema"

[[ "$(uname -s)" == "Linux" ]] || fail "Esto es para Linux. En Windows usa scripts\\instalar_windows.ps1"

if [[ "$RAIZ" =~ [[:space:]\&\\%] ]]; then
    fail "La ruta tiene espacios o caracteres raros ($RAIZ): mueve el proyecto a ~/enpower"
fi

if [[ "$RAIZ" == /mnt/?/* ]]; then
    warn "el proyecto esta en $RAIZ (disco de Windows)"
    warn "el panel y las pruebas van mucho mas rapido en el disco de Linux:"
    warn "  mv ~/enpower  o  git clone ... ~/enpower"
fi

if [[ $EUID -eq 0 ]]; then
    warn "estas corriendo como root: los archivos y el servicio quedarian de root."
    warn "mejor correlo con tu usuario normal (el script pide sudo cuando hace falta)."
fi

for cmd in python3 git; do
    command -v "$cmd" >/dev/null 2>&1 || fail "falta $cmd (sudo apt install $cmd)"
done

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || fail "hace falta Python 3.11 o superior (este es $(python3 -V 2>&1)). En Ubuntu 22.04 instala python3.11 o usa Ubuntu 24.04."
ok "$(python3 -V)"

if command -v apt-get >/dev/null 2>&1; then
    cyan "Instalando paquetes del sistema"
    SUDO=""
    [[ $EUID -ne 0 ]] && SUDO="sudo"
    $SUDO apt-get update -qq
    $SUDO apt-get install -y --no-install-recommends python3-venv python3-pip poppler-utils >/dev/null
    ok "python3-venv, python3-pip, poppler-utils"
else
    warn "no es Debian/Ubuntu: instala a mano python3-venv y poppler-utils"
fi

if command -v pdftotext >/dev/null 2>&1; then
    ok "pdftotext disponible (lee los datos de los PDF)"
else
    fail "falta pdftotext (poppler-utils): sin el no se pueden leer las facturas"
fi

if [[ $ZONA -eq 1 ]]; then
    cyan "Zona horaria"
    SUDO=""
    [[ $EUID -ne 0 ]] && SUDO="sudo"
    if $SUDO timedatectl set-timezone America/Santo_Domingo 2>/dev/null; then
        ok "America/Santo_Domingo"
    else
        warn "no se pudo cambiar la zona (revisa timedatectl)"
    fi
fi

# ------------------------------------------------------------------ 2. entorno
cyan "Preparando el entorno virtual"
cd "$RAIZ"
if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
    ok ".venv creado"
else
    ok ".venv ya existia"
fi

.venv/bin/python -m pip install --quiet --upgrade pip || warn "no se pudo actualizar pip"
.venv/bin/python -m pip install --quiet -r requirements.txt \
    || fail "fallo la instalacion de dependencias"
ok "dependencias al dia"

chmod +x scripts/*.sh 2>/dev/null || true

# ------------------------------------------------------------------ 3. .env
cyan "Preparando el .env"

if [[ -z "$ARCHIVO_ENV" ]]; then
    for candidato in .env.produccion .env .env.example; do
        [[ -f "$candidato" ]] && { ARCHIVO_ENV="$candidato"; break; }
    done
fi

# esqueleto minimo: si no hay ni .env.example el instalador no se detiene,
# deja el archivo creado y sigue (asi se ve el error real mas adelante)
escribir_env_base() {
    cat > .env <<'ENV'
# Generado por scripts/instalar_wsl.sh — completar con las credenciales de la empresa
EDENORTE_URL=https://ofv.edenorte.com.do
EDENORTE_EMAIL=
EDENORTE_PASSWORD=

# Edesur (Oficina Virtual) - API ovapi.edesur.com.do
EDESUR_USER=
EDESUR_PASSWORD=

# Base de datos: vacio -> SQLite local | postgresql://... -> Supabase
DATABASE_URL=

# Bot de Telegram (BotFather) y canal destino
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Rutas de trabajo (relativas al proyecto)
DB_PATH=data/enpower.db
FACTURAS_DIR=facturas
EXPORT_DIR=exports

# Sesiones y usuario administrador inicial
SECRET_KEY=
ADMIN_USER=admin
ADMIN_PASSWORD=

PORT=8000
HOST=127.0.0.1
ENV
}

CREDENCIALES_COMPLETAS=1
if [[ -f .env ]]; then
    ok ".env ya existia (no se toco)"
elif [[ -n "$ARCHIVO_ENV" && -f "$ARCHIVO_ENV" ]]; then
    cp "$ARCHIVO_ENV" .env
    ok ".env creado desde $ARCHIVO_ENV"
    [[ "$ARCHIVO_ENV" == ".env.example" ]] && CREDENCIALES_COMPLETAS=0
else
    escribir_env_base
    CREDENCIALES_COMPLETAS=0
    warn "no habia .env, .env.produccion ni .env.example en esta carpeta"
    warn "deje un .env vacio para que puedas seguir; hay que llenar las credenciales"
    warn "carpeta actual: $RAIZ"
    if command -v git >/dev/null 2>&1 && git -C "$RAIZ" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        warn "es un repo git, pero faltan archivos: revisa que no sea una copia parcial"
        warn "  git -C \"$RAIZ\" ls-files | grep env      (deberia listar .env.example)"
        warn "  git -C \"$RAIZ\" checkout -- .env.example  (si el archivo esta en el repo)"
    else
        warn "esta carpeta NO es un repo git: parece una copia incompleta"
        warn "vuelve a traerlo con: git clone https://github.com/nazts/EnpowerSolarEdeFacturas.git ~/enpower"
    fi
    warn "si el .env real esta en Windows, copialo con:"
    warn "  cp \"/mnt/c/Users/<usuario>/Documents/EnpowerSolarEdeFacturas/.env.produccion\" \"$RAIZ/.env\""
fi

# el .env tiene credenciales: solo el dueño lo lee
chmod 600 .env
[[ -f .env.produccion ]] && chmod 600 .env.produccion
ok "permisos del .env: 600"

if [[ -n "$PUERTO" ]]; then
    if grep -q '^PORT=' .env; then
        sed -i "s/^PORT=[^[:space:]]*/PORT=$PUERTO/" .env
    else
        printf 'PORT=%s\n' "$PUERTO" >> .env
    fi
    ok "puerto $PUERTO"
else
    ok "puerto $(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2 || echo '8000 (por defecto)')"
fi

if [[ $RED -eq 1 ]]; then
    if grep -q '^HOST=' .env; then
        sed -i 's/^HOST=.*/HOST=0.0.0.0/' .env
    else
        printf 'HOST=0.0.0.0\n' >> .env
    fi
    ok "HOST=0.0.0.0 (visible en la red)"
else
    host_leido="$(grep -E '^HOST=' .env 2>/dev/null | cut -d= -f2 || true)"
    if [[ "$host_leido" == "0.0.0.0" ]]; then
        warn "HOST=0.0.0.0: el panel esta abierto a la red (quitalo o usa --red a proposito)"
    else
        ok "HOST=${host_leido:-127.0.0.1 (solo esta PC)} — usa --red para abrirlo a la red"
    fi
fi

if ! grep -qE '^DATABASE_URL=postgres' .env; then
    if [[ $CREDENCIALES_COMPLETAS -eq 0 ]]; then
        cat <<FIN

   El .env existe pero esta vacio: faltan las credenciales (Edenorte, Edesur,
   Telegram y DATABASE_URL de Supabase).

   Que hacer:
     1. consigue el .env.produccion de quien administra el sistema hoy
     2. copialo a esta carpeta como .env (reemplaza el vacio):
          cp /ruta/al/.env.produccion "$RAIZ/.env"
          chmod 600 "$RAIZ/.env"
     3. vuelve a correr este instalador

FIN
        fail "faltan las credenciales en el .env"
    fi
    warn "DATABASE_URL vacio: se usara SQLite local (produccion espera Supabase)"
fi

# ------------------------------------------------------------------ 4. base
cyan "Preparando la base de datos"
.venv/bin/python -m app.db --init
.venv/bin/python -m app.db --estado
ok "base lista"

cyan "Creando carpetas"
mkdir -p data facturas exports logs
chmod 700 logs
ok "data, facturas, exports, logs"

# ------------------------------------------------------------------ 5. systemd
if [[ $SIN_SERVICIO -eq 1 ]]; then
    warn "servicios de systemd omitidos (--sin-servicio)"
elif [[ ! -d /run/systemd/system ]]; then
    warn "systemd no esta activo en este entorno: no se instalan los servicios"
    warn "en WSL: agrega [boot] systemd=true en /etc/wsl.conf, luego 'wsl --shutdown'"
    warn "mientras tanto: ./scripts/correr_panel.sh"
else
    cyan "Instalando los servicios de systemd"
    USUARIO="$(id -un)"
    SUDO=""
    [[ $EUID -ne 0 ]] && SUDO="sudo"

    if [[ "$USUARIO" == "root" ]]; then
        fail "no instalo servicios con User=root: corre el script con tu usuario normal"
    fi

    for unidad in enpower-panel.service enpower-runner.service enpower-runner.timer; do
        sed -e "s|__RAIZ__|$RAIZ|g" -e "s|__USUARIO__|$USUARIO|g" \
            "deploy/$unidad" | $SUDO tee "/etc/systemd/system/$unidad" >/dev/null
    done

    if ! $SUDO systemctl daemon-reload; then
        warn "daemon-reload fallo: revisa que systemd sea el PID 1"
    fi

    if $SUDO systemctl enable enpower-panel.service >/dev/null 2>&1; then
        ok "enpower-panel.service habilitado (arranca con el sistema)"
    else
        warn "no se pudo habilitar enpower-panel.service"
    fi
    if $SUDO systemctl restart enpower-panel.service; then
        ok "enpower-panel.service arrancado"
    else
        warn "el panel no arrancó: journalctl -u enpower-panel -n 40"
    fi

    if $SUDO systemctl enable --now enpower-runner.timer >/dev/null 2>&1; then
        ok "enpower-runner.timer activo (todos los dias a las 07:00)"
    else
        warn "no se pudo activar enpower-runner.timer"
    fi
fi

# ------------------------------------------------------------------ 6. prueba
cyan "Prueba rapida"
.venv/bin/python - <<'PY'
from app import config, db
from app.dashboard import app  # noqa: F401
motor = "postgres" if db.pg.es_postgres(db.pg.limpiar_url(config.DATABASE_URL)) else "sqlite"
print(f"   OK  proyecto importado (motor: {motor})")
with db.sesion() as conexion:
    n = conexion.execute("SELECT COUNT(*) AS n FROM contratos").fetchone()["n"]
print(f"   OK  clientes en la base: {n}")
PY

IP="$(hostname -I 2>/dev/null | awk '{print $1}')" || IP=""
PUERTO_FINAL="$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2 || true)"
PUERTO_FINAL="${PUERTO_FINAL:-8000}"

printf '\n==============================================================\n'
printf '  Listo.\n'
printf '  Panel:            http://%s:%s\n' "${IP:-127.0.0.1}" "$PUERTO_FINAL"
printf '  Arrancar a mano:  ./scripts/correr_panel.sh\n'
printf '  Correr la tanda:  .venv/bin/python -m app.runner --sin-envio\n'
printf '  Estado servicios: systemctl status enpower-panel\n'
if [[ "$IP" == 172.* || "$IP" == 10.* ]]; then
    printf '\n  Ojo: %s parece la IP interna de WSL (NAT). Para que las otras PC\n' "$IP"
    printf '  entren, revisa docs/produccion.md seccion 2.6 (red de WSL).\n'
fi
printf '==============================================================\n'
