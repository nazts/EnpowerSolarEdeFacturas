# Puesta en producción (Windows y WSL/Linux)

El proyecto funciona igual en las tres formas:

| Forma | Para qué sirve | Base de datos |
|---|---|---|
| Esta PC, Windows | probar, trabajar día a día | Supabase (`DATABASE_URL`) |
| PC de la oficina, Windows | servidor pequeño, pocas PC | Supabase |
| PC de la oficina, WSL/Linux | **recomendado para producción**: servicios que se reinician solos y corrida diaria sin depender de una ventana abierta | Supabase |

Las credenciales viven en **`.env`** (no se sube al repositorio). Hay una copia
lista para trasladar: **`.env.produccion`**, también ignorada por git. Ese es el
archivo que se lleva al servidor, y **es el primer paso**: sin credenciales la
instalación crea una base SQLite vacía en vez de usar Supabase.

---

## 1. Llevar las credenciales al servidor (primero)

`.env.produccion` tiene todo: Edenorte, Edesur, Telegram, `SECRET_KEY`, usuario
admin y `DATABASE_URL` de Supabase.

No viaja por el repositorio. Tres maneras:

```bash
# A. desde Windows con OpenSSH (viene con Windows 10/11), si el servidor tiene sshd
scp ".env.produccion" usuario@192.168.1.50:~/enpower/.env

# B. dentro de WSL, si el proyecto se copió desde el disco de Windows
cp "/mnt/c/Users/Luis Martinez/Documents/proyectosGithub/EnpowerSolarEdeFacturas/.env.produccion" ~/enpower/.env

# C. por la ruta de red de WSL, desde PowerShell
Copy-Item .env.produccion "\\wsl$\Ubuntu\home\<usuario>\enpower\.env"

# después, siempre:
chmod 600 ~/enpower/.env
```

Comprobar que llegó (no imprime valores):

```bash
cd ~/enpower && .venv/bin/python -c "
import os
from app import config  # noqa: F401  (carga el .env)
claves = ('EDENORTE_EMAIL','EDENORTE_PASSWORD','EDESUR_USER','EDESUR_PASSWORD',
          'TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','DATABASE_URL','ADMIN_PASSWORD')
for c in claves:
    print(f'  {c:20} {\"OK\" if os.environ.get(c) else \"FALTA\"}')
print('  SECRET_KEY           ', 'OK' if os.environ.get('SECRET_KEY') else 'FALTA (se genera uno)')
"
```

---

## 2. Windows (esta PC o la de la oficina)

### Instalación

En PowerShell, dentro de la carpeta del proyecto:

```powershell
.\scripts\instalar_windows.ps1
```

Hace todo: busca Python 3.11+, crea `.venv`, instala dependencias, prepara `.env`
(desde `.env.produccion` si existe), revisa la base, crea `data/`, `facturas/`,
`exports/`, `logs/`, comprueba `pdftotext` y registra las tareas programadas.

Opciones:

```powershell
.\scripts\instalar_windows.ps1 -Red          # panel abierto a la red de la oficina (HOST=0.0.0.0)
.\scripts\instalar_windows.ps1 -Firewall     # agrega la regla del puerto (pide administrador)
.\scripts\instalar_windows.ps1 -Puerto 8080  # solo cambia el puerto si se pide
.\scripts\instalar_windows.ps1 -SinTareas    # sin tareas programadas
.\scripts\instalar_windows.ps1 -OlvidarEnv   # vuelve a copiar .env.produccion sobre .env
```

Si PowerShell bloquea el script:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\instalar_windows.ps1
```

> Sin `-Red` el panel queda solo en `127.0.0.1`. Si `.env.produccion` trae
> `HOST=0.0.0.0`, el instalador lo baja a `127.0.0.1` y lo avisa.

### Uso diario

- **`iniciar_panel.bat`** — doble clic; queda sirviendo y muestra la URL.
- **`detener_panel.bat`** — lo baja (busca el proceso por el puerto del `.env`).
- La ventana tiene que quedar abierta (es el servidor).

### Tareas programadas

| Tarea | Cuándo | Qué hace |
|---|---|---|
| `EnpowerSolar Panel` | al iniciar sesión | arranca el panel |
| `EnpowerSolar Facturas` | todos los días 07:00 | descarga y manda a Telegram (log `logs\runner_AAAAMMDD.log`) |

```powershell
.\scripts\tareas_windows.ps1 -Estado     # qué existe, cuándo corre y con qué ruta
.\scripts\tareas_windows.ps1 -Crear
.\scripts\tareas_windows.ps1 -Quitar
```

La de facturas se registra con **S4U**, así que corre **aunque nadie haya iniciado
sesión** (si Windows lo permite; si no, cae a modo interactivo y avisa). La del
panel sí necesita sesión: sin sesión no hay ventana donde vivir.

### Antes de dejarla como servidor

```powershell
powercfg /change standby-timeout-ac 0     # que no se suspenda nunca
```

---

## 3. WSL (recomendado para producción)

### 3.1 Instalar WSL

PowerShell **como administrador**:

```powershell
wsl --install -d Ubuntu-24.04
```

Ubuntu 24.04 trae Python 3.12; el proyecto pide **3.11 o más** (22.04 trae 3.10 y
falla). Reinicia la PC, crea el usuario de Linux y verifica:

```powershell
wsl -l -v          # Ubuntu-24.04, version 2, Running
```

### 3.2 Copiar el proyecto al disco de Linux

**Opción A — clonar (recomendado si todo está subido):**

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/nazts/EnpowerSolarEdeFacturas.git ~/enpower
```

El repo es privado: usuario `nazts` y, como contraseña, un **token** de GitHub
(Settings → Developer settings → Personal access tokens → Fine-grained, permiso
*Contents: Read* y el repo seleccionado explícitamente).

**Opción B — por SSH (mejor para actualizar con `git pull`):**

```bash
ssh-keygen -t ed25519 -C "enpower-servidor"      # Enter a todo
cat ~/.ssh/id_ed25519.pub                         # pegar en GitHub → SSH keys
git clone git@github.com:nazts/EnpowerSolarEdeFacturas.git ~/enpower
```

**Opción C — copiar desde el disco de Windows (la más rápida la primera vez):**

```bash
rsync -a --exclude .venv --exclude __pycache__ --exclude .git \
      "/mnt/c/Users/Luis Martinez/Documents/proyectosGithub/EnpowerSolarEdeFacturas/" ~/enpower/
```

`rsync` se salta `.venv` (es de Windows y no sirve en Linux). Después copia el
`.env` como en la sección 1.

> No dejes el proyecto en `/mnt/c/...`: el panel y las pruebas van 5–10 veces más
> lentos. Que viva en `~/enpower`.

### 3.3 Instalar

```bash
cd ~/enpower
bash scripts/instalar_wsl.sh --red
```

Qué hace: instala `python3-venv` y `poppler-utils` (necesario para leer los datos
del PDF), crea `.venv`, instala dependencias, prepara `.env` (desde
`.env.produccion` o `.env`), deja permisos `600`, inicializa la base y instala
**dos servicios de systemd**:

| Servicio | Qué hace |
|---|---|
| `enpower-panel` | el panel; arranca con el sistema y se reinicia si se cae |
| `enpower-runner.timer` | dispara la descarga todos los días a las **07:00 hora de RD** (la zona va en el timer) |

Opciones:

```bash
bash scripts/instalar_wsl.sh --puerto 8080
bash scripts/instalar_wsl.sh --red              # HOST=0.0.0.0 (visible en la red)
bash scripts/instalar_wsl.sh --sin-servicio     # sin systemd (pruebas)
bash scripts/instalar_wsl.sh --zona             # America/Santo_Domingo
```

Si WSL no tiene systemd activo, el instalador lo detecta y avisa: hay que agregar
`[boot]` / `systemd=true` en `/etc/wsl.conf` y hacer `wsl --shutdown`.

### 3.4 Comandos del día a día

```bash
systemctl status enpower-panel               # ¿está corriendo?
sudo systemctl restart enpower-panel         # reiniciar tras cambiar el .env
journalctl -u enpower-panel -n 50            # últimos mensajes
systemctl list-timers enpower-runner.timer   # próxima corrida
sudo systemctl start --no-block enpower-runner   # correr ahora (no bloquea la consola)
journalctl -fu enpower-runner                # seguir esa corrida
tail -f ~/enpower/logs/runner_$(date +%Y%m%d).log
```

### 3.5 Que arranque sola al encender la PC

WSL no se enciende solo: algo tiene que lanzarlo. `iniciar_wsl.bat` arranca la
distro, los servicios y deja un proceso vivo para que WSL no la apague por
inactividad. Se registra con:

```powershell
.\scripts\tareas_wsl.ps1 -Crear                      # distro Ubuntu-24.04
.\scripts\tareas_wsl.ps1 -Crear -Distro Ubuntu-24.04
```

Crea `EnpowerSolar WSL` (al iniciar sesión). **Necesita que alguien inicie sesión
en Windows**: las distros de WSL son por usuario y la tarea no puede correr sin
sesión. Si nadie entra tras un reinicio, el panel arranca cuando esa persona entre.
Para que no dependa de eso, activa el inicio de sesión automático de Windows.

### 3.6 Red: que las otras PC lo vean

WSL2 usa NAT por defecto: Windows ve el panel en `localhost:8000`, las demás PC no.

**A. Red compartida (lo más limpio).** Copia `deploy\wslconfig.ejemplo` a
`C:\Users\<usuario>\.wslconfig` (respalda el que hubiera), reinicia WSL:

```powershell
Copy-Item "$env:USERPROFILE\.wslconfig" "$env:USERPROFILE\.wslconfig.bak" -ErrorAction SilentlyContinue
Copy-Item deploy\wslconfig.ejemplo "$env:USERPROFILE\.wslconfig"
wsl --shutdown
```

Con `networkingMode=mirrored` el tráfico entrante lo filtra el **firewall de
Hyper-V**, no el de Windows. En PowerShell administrador:

```powershell
Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
```

**B. Si `mirrored` no funciona en esta build:** el mismo archivo trae el plan B con
`netsh portproxy` (ojo: la IP de WSL cambia en cada arranque).

**Comprobar desde otra PC:**

```powershell
Test-NetConnection 192.168.1.50 -Port 8000
```

Y abrir el puerto en el Firewall de Windows (administrador):

```powershell
netsh advfirewall firewall add rule name="Enpower 8000" dir=in action=allow protocol=TCP localport=8000
```

---

## 4. Verificación después de instalar

```bash
# 1. la base responde
.venv/bin/python -m app.db --estado

# 2. el panel escucha (200 = login servido)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/login

# 3. corrida sin enviar nada a Telegram (sí consulta los portales y escribe en la base)
.venv/bin/python -m app.runner --sin-envio --fecha $(date +%F)

# 4. pruebas del proyecto
.venv/bin/python -m pytest tests -q

# 5. el timer quedó bien agendado
systemd-analyze calendar "*-*-* 07:00:00 America/Santo_Domingo"
```

En Windows los mismos pasos con `.venv\Scripts\python.exe`.

---

## 5. Lista de chequeo para pasar a producción

- [ ] `.env` en el servidor con las 8 claves en `OK` (sección 1), permisos `600`.
- [ ] Windows: `.\scripts\instalar_windows.ps1` sin errores.
- [ ] El panel abre y el login funciona (`iniciar_panel.bat`).
- [ ] Tarea `EnpowerSolar Facturas` creada a las 07:00 (`tareas_windows.ps1 -Estado`).
- [ ] WSL instalado (`wsl -l -v` → Ubuntu-24.04, 2).
- [ ] Proyecto en `~/enpower` (no en `/mnt/c`).
- [ ] `bash scripts/instalar_wsl.sh --red` sin errores.
- [ ] `systemctl status enpower-panel` → `active (running)`.
- [ ] `systemctl list-timers enpower-runner.timer` → próxima a las 07:00.
- [ ] Otra PC de la oficina abre `http://IP-DEL-SERVIDOR:8000`.
- [ ] Solo **uno** de los dos paneles activo (Windows o WSL): los dos usan el puerto 8000.
- [ ] Suspensión de Windows desactivada.
- [ ] Corrida de prueba sin envío termina sin errores.
- [ ] Contraseña de Supabase rotada (se compartió por chat durante las pruebas).

---

## 6. Problemas típicos

| Síntoma | Causa / arreglo |
|---|---|
| `solo se permite un uso de cada dirección de socket` | ya hay un panel corriendo: `detener_panel.bat` o `sudo systemctl stop enpower-panel` |
| Otra PC no abre el panel | falta la regla de firewall; en WSL, revisa §3.6 (y el firewall de Hyper-V si usas mirrored) |
| `pdftotext no encontrado` | `sudo apt install poppler-utils` (en Windows lo trae Git for Windows) |
| El panel no arranca tras reiniciar | las tareas de Windows y de WSL necesitan sesión iniciada |
| `status=203/EXEC` en el servicio | el `.sh` quedó sin bit `+x`: el instalador lo corrige (llama por `bash`) |
| `systemctl: command not found` / nada arranca | falta `[boot] systemd=true` en `/etc/wsl.conf` |
| Se cortó a media descarga | la corrida siguiente retoma: los pendientes quedan en la base |
| `duplicate key ... pkey` al importar | `python -m app.db --importar` recoloca las secuencias |
| El login falla con la base en Supabase | revisa `DATABASE_URL` (puerto 6543, `sslmode=require`) |
| `git pull` pide credenciales siempre | usa SSH (Opción B) o guarda el token con `git config credential.helper store` |
