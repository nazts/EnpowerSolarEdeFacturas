# EnpowerSolarEdeFacturas

Descarga automática de facturas eléctricas de la Oficina Virtual de Edenorte
para todos los contratos de la empresa, con dashboard de contratos (NIC) y envío
de las facturas del día a un canal de Telegram en un solo álbum.

## Cómo funciona

1. Cada día a las 7:00 a.m. inicia sesión en `https://ofv.edenorte.com.do`.
2. Recorre **todos los contratos asociados** a la cuenta.
3. Descarga solo las facturas de tipo **Recibo de consumo** cuya **fecha de
   corte** es la del día (el PDF se guarda el mismo día porque el portal solo
   conserva las 2 facturas más recientes).
4. Renombra el archivo: `NOMBRES APELLIDOS <NIC>.pdf` (Edenorte imprime
   `APELLIDOS, NOMBRES`). El PDF nunca se modifica.
5. Manda todas las facturas del día en **un solo álbum** de Telegram.
6. Si una factura cortó y no está publicada, queda **pendiente** y se reintenta
   en las corridas siguientes; el reporte avisa de los pendientes.
7. Si el portal falla, se emite la alerta **"La plataforma de Edenorte tiene
   problemas"** y no se pierde ningún pendiente.

## Arranque rápido (local)

```bash
cd "C:\Users\Luis Martinez\Documents\proyectosGithub\EnpowerSolarEdeFacturas"
.venv\Scripts\python.exe -m uvicorn app.dashboard:app --host 127.0.0.1 --port 8000
```

Luego abre `http://127.0.0.1:8000` e inicia sesión. Para detenerlo: `Ctrl+C`.

### Roles

| Rol | Puede |
|---|---|
| **asesor** | consultar clientes, facturas y mapa |
| **soporte** | además: agregar y editar clientes, correr la descarga, guardar histórico, exportar Excel |
| **admin** | todo, incluidos usuarios, borrados y el registro de **Actividad** (`/eventos`) |

El administrador principal es el `ADMIN_USER` del `.env`: nadie más lo desactiva ni
le cambia la clave. Cada movimiento queda registrado con el usuario que lo hizo.

### Instalación automática

Windows (crea el venv, el `.env`, la base y las tareas programadas):

```powershell
.\scripts\instalar_windows.ps1
```

Linux / WSL (recomendado para el servidor: servicios de systemd y corrida a las 07:00):

```bash
bash scripts/instalar_wsl.sh
```

El paso a paso completo —Windows, WSL, red de la oficina y credenciales de
producción— está en **[docs/produccion.md](docs/produccion.md)**.

Comandos útiles (siempre con el python del venv):

```bash
.venv\Scripts\python.exe -m pytest tests -q          # pruebas
.venv\Scripts\python.exe -m app.runner                # escaneo del día + envío
.venv\Scripts\python.exe -m app.runner --historial    # últimas facturas (histórico)
.venv\Scripts\python.exe -m app.edesur --nic 2115589  # bajar una factura de Edesur
```

Si es una copia nueva (sin `.venv`):

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env      # y llenar las credenciales
```

## Instalación

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
cp .env.example .env      # completar credenciales y bot
```

## Uso

```bash
# Corrida del día (descarga + envío a Telegram)
./.venv/Scripts/python.exe -m app.runner

# Simular una fecha, sin enviar nada
./.venv/Scripts/python.exe -m app.runner --fecha 2026-10-16 --sin-envio

# Dashboard
./.venv/Scripts/python.exe -m uvicorn app.dashboard:app --port 8000
# http://127.0.0.1:8000

# Excel
./.venv/Scripts/python.exe -m app.excel
```

## Dashboard

- Agregar contrato por **NIC**: el nombre se autocompleta consultando el titular
  real en la Oficina Virtual.
- Campos extra por contrato: **pagador/alias** (cuando paga alguien distinto al
  titular del suministro), **asesor**, día de corte y notas.
- Tabla de facturas descargadas con enlace al PDF, pendientes y actividad.
- Botón **Descargar Excel** (contratos + facturas).

## Usuarios y permisos

- El panel pide **usuario y contraseña** (`/login`). Sin sesión, cualquier ruta
  redirige al login y las rutas `/api/*` responden 401.
- Roles: **admin** (todo, incluida la página *Usuarios*) y **asesor** (trabaja con
  clientes, facturas y mapa, pero no administra usuarios).
- Usuarios: alta, cambio de contraseña y activar/desactivar en `/usuarios`.
- El primer administrador se crea solo al iniciar si no existe ninguno, con
  `ADMIN_USER` / `ADMIN_PASSWORD` del `.env` (si no están, se genera una
  contraseña aleatoria y se imprime una vez en la consola).
- La sesión es una cookie firmada con `SECRET_KEY` (se genera en `.secret_key`
  si falta) y dura 10 horas.

## Proyectos instalados

Cada cliente puede tener **uno o varios proyectos** (NIC, nombre del proyecto,
paneles, capacidad instalada, inversores, batería, fecha, asesor y notas),
guardados en la tabla `proyectos` y listados en la ficha del cliente, con su
formulario para agregar y eliminar.

## Distribuidoras

- **Edenorte** (`app/edenorte.py`): scraping del portal con `requests` + CSRF.
- **Edesur** (`app/edesur.py`): API propia (`ovapi.edesur.com.do/ovapi`) con token Bearer.
  Descarga la factura mas reciente de un NIC:

  ```
  .venv\Scripts\python.exe -m app.edesur --nic 2115589 --ref 2115589310
  ```

  Si la referencia de pago ya no vale (los 3 digitos suben con cada factura),
  el cliente prueba hasta 50 referencias cercanas antes de rendirse.

## Histórico

`python -m app.runner --historial` guarda las últimas facturas de cada cliente
(3 por defecto) y manda a Telegram **solo la más reciente, un mensaje por cliente**.

## Iconos

La interfaz usa **Bootstrap Icons** (MIT), servidos **localmente** desde
`app/static/bootstrap-icons/` (CSS + woff2/woff), sin CDN: funcionan sin
internet. Se usan como `<i class="bi bi-nombre"></i>`.

## Telegram

1. Crear el canal en Telegram (privado o público).
2. Agregar `@EnpowerSolarBot` como **administrador** del canal con permiso de
   publicar.
3. Publicar cualquier mensaje en el canal para que el bot pueda resolver el
   `chat_id` (o poner el `-100…` a mano en `TELEGRAM_CHAT_ID`).
4. Probar desde el dashboard con **Probar Telegram**.

## Estructura

```
app/
  config.py      variables de entorno y rutas
  edenorte.py    cliente HTTP de la Oficina Virtual (login, contratos, PDFs)
  pdfdata.py     extracción de datos del PDF (pdftotext, sin modificarlo)
  naming.py      normalización de razón social y nombre del archivo
  db.py          SQLite: contratos, facturas, pendientes, eventos
  runner.py      corrida diaria (filtro por corte + envío)
  telegram.py    mensajes y álbum de PDFs
  excel.py       exportación a Excel
  dashboard.py   dashboard web (FastAPI)
tests/           pruebas (unittest)
docs/requisitos.md  especificación y casos de aceptación
```

## Programación diaria

La tarea de las 7:00 a.m. se registra con el cron de Hermes y ejecuta
`app.runner`.
