# Poner el panel en red (local y fuera de la empresa)

El panel es una app web normal (FastAPI). Por defecto escucha solo en `127.0.0.1`
(esta PC). Para que lo usen otras computadoras hay que abrirlo a la red y, si van
a entrar desde fuera, **poner HTTPS** — si no, la contraseña viaja en claro.

> El usuario y la contraseña del panel siguen siendo obligatorios en todos los
> casos: el login es el que ya existe (PBKDF2 + cookie firmada).

---

## 1. Varias PC en la misma oficina (red local)

En la PC que hace de servidor (tiene que quedar encendida y con el panel abierto):

```powershell
cd EnpowerSolarEdeFacturas        # la carpeta donde quedó el proyecto
$env:HOST = "0.0.0.0"
.venv\Scripts\python.exe run.py
```

`run.py` imprime las direcciones, por ejemplo:

```
  Esta PC        http://127.0.0.1:8000
  Red local      http://192.168.1.50:8000
```

Esa segunda URL es la que abren las demás PC del equipo (misma red / mismo WiFi).

**Abrir el puerto en el Firewall de Windows** (una sola vez, en la PC servidor,
PowerShell **como administrador**):

```powershell
netsh advfirewall firewall add rule name="Enpower 8000" dir=in action=allow protocol=TCP localport=8000
```

Si no responde desde otra PC, revisa además que la red esté marcada como
**Privada** (Configuración → Red e Internet) y que no haya otra regla de firewall
bloqueando Python.

---

## 2. Personal fuera de la empresa

No abras el router a Internet directamente. Dos opciones seguras y gratuitas:

### Opción A — Tailscale (recomendada)

Es una VPN privada: cada persona instala Tailscale, tú la autorizas una vez y
entran a la IP privada del servidor. **Nada queda expuesto a Internet.**

1. Crea la cuenta en <https://tailscale.com> e instala Tailscale en la PC servidor
   y en las laptops/celulares del personal.
2. En la PC servidor: `tailscale up` y anota su IP (`tailscale ip -4`, tipo `100.x.y.z`).
3. Arranca el panel como en la sección 1 (`HOST=0.0.0.0`).
4. Cada persona abre `http://100.x.y.z:8000` con su usuario del panel.

Si quieres HTTPS dentro de Tailscale: `tailscale cert <nombre>.<tailnet>.ts.net`
y sirve el panel con `COOKIE_SECURE=1` detrás de un proxy.

### Opción B — Cloudflare Tunnel (URL pública con HTTPS)

Útil si el personal no puede instalar nada (solo abrir un link).

1. Instala `cloudflared` en la PC servidor.
2. `cloudflared tunnel login` y `cloudflared tunnel create enpower`
3. `cloudflared tunnel route dns enpower panel.tudominio.com`
4. Configura `config.yml` apuntando al panel local:

   ```yaml
   tunnel: enpower
   credentials-file: C:\Users\<usuario>\.cloudflared\<id>.json
   ingress:
     - hostname: panel.tudominio.com
       service: http://127.0.0.1:8000
     - service: http_status:404
   ```

5. `cloudflared tunnel run enpower`
6. Arranca el panel con `COOKIE_SECURE=1` (la cookie solo viaja por HTTPS):

   ```powershell
   $env:COOKIE_SECURE = "1"; .venv\Scripts\python.exe run.py
   ```

Con Cloudflare puedes además poner **Cloudflare Access** (login por correo de la
empresa) delante del panel, para que solo pase quien tú digas.

### Opción C — Reglas en el router (no recomendada)

Solo si tienes IP pública fija, sabes lo que haces y el router permite
redireccionar el 8000. Sin HTTPS es peligroso: la contraseña se transmite en
claro. Si vas por aquí, mínimo pon Caddy/Nginx con certificado delante.

---

## 3. Cosas que conviene dejar listas

- **Usuarios por persona**: crea un usuario para cada asesor en *Usuarios*
  (rol `asesor`, no `admin`) y que cada uno cambie su clave en *Mi clave*.
- **Respaldo**: copia `data\enpower.db` y `facturas\` (son todos los datos).
- **El escaneo de las 7 a.m.** no depende de que el panel esté abierto: lo hace la
  tarea de Windows *"EnpowerSolar Facturas Edenorte"* con `app.runner`.
- **`.env`** no se versiona: si mueves el proyecto a otra PC, lleva el `.env` aparte.
- Si el panel va a estar siempre encendido, en vez de la ventana de PowerShell
  conviene dejarlo como **servicio** (NSSM, Task Scheduler "Al iniciar sesión",
  o `pythonw` con un acceso directo en Inicio).
