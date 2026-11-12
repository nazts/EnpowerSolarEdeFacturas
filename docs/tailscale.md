# Acceso remoto con Tailscale

Red privada entre las PC del equipo: nadie de afuera la ve, no se abre ningún puerto
en el router y funciona detrás de NAT o IP dinámica. El panel sigue escuchando en
`127.0.0.1`; Tailscale se encarga del resto.

> **Licencia, para que no haya sorpresas:** el plan **Personal** de Tailscale es
> gratis hasta 6 usuarios, pero su licencia es para **uso no comercial**. Enpower es
> una empresa, así que lo correcto es el plan **Standard (US$8 por usuario/mes)**.
> Con 6 personas eso son ~US$48/mes. Es una decisión tuya; técnicamente el plan
> Personal funciona igual.

---

## 1. Crear la red (una sola vez)

1. Entrá a <https://tailscale.com> y creá la cuenta con el correo de la empresa
   (por ejemplo con Google Workspace).
2. En **Settings → Keys**, generá una **auth key** (`tskey-auth-...`), reusable y con
   caducidad de 90 días. Guardala, es lo que usa el servidor para unirse sin aprobar
   nada a mano.
3. En **Users → Invite**, invitá a las 5 personas restantes con su correo.

## 2. Instalar en la PC servidor (WSL)

```bash
cd ~/enpowersolarapp/EnpowerSolarEdeFacturas
bash scripts/instalar_tailscale.sh tskey-auth-XXXXXXXX
```

El script instala Tailscale, se une a la red y publica el panel con
`tailscale serve`. Al final imprime algo como:

```
IP Tailscale:  100.101.102.103
Nombre:        servidor-enpower.tailXXXX.ts.net
Panel:         https://servidor-enpower.tailXXXX.ts.net
```

**Qué hace `tailscale serve`:** expone el puerto 8000 **solo dentro de tu red
Tailscale**, con HTTPS y un nombre `*.ts.net`. El panel no queda abierto a internet
ni a la red de la oficina.

Si preferís hacerlo a mano:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --authkey tskey-auth-XXXXXXXX
sudo tailscale serve --bg 8000
tailscale serve status
```

## 3. Ajustar el panel para HTTPS

`tailscale serve` entrega HTTPS, así que en el `.env` del servidor:

```
HOST=127.0.0.1     # no hace falta abrirlo a la red
COOKIE_SECURE=1    # la cookie de sesión solo viaja por HTTPS
```

```bash
sudo systemctl restart enpower-panel
```

## 4. En cada PC del equipo

1. Instalar Tailscale: Windows/macOS desde <https://tailscale.com/download>, Android
   o iPhone desde la tienda.
2. Entrar con **la cuenta de la empresa** (o con la invitación que les mandaste).
3. Abrir en el navegador:

   ```
   https://servidor-enpower.tailXXXX.ts.net
   ```

   También funciona `http://100.101.102.103:8000` si `serve` no se usó.

Nada más: no hay que instalar Python ni el proyecto. Cada uno entra con su usuario
del panel (`admin2`, `soporte1`, …).

## 5. Limitar quién puede entrar al panel

Por defecto, **todos** los equipos de tu red ven el panel. Para que solo lo vea el
equipo de facturación, en **Access Controls** (ACL):

```json
{
  "tagOwners": { "tag:facturas": ["autogroup:admin"] },
  "hosts": { "panel": "100.101.102.103" },
  "acls": [
    { "action": "accept", "src": ["group:facturacion"], "dst": ["panel:8000"] },
    { "action": "accept", "src": ["autogroup:admin"], "dst": ["panel:8000"] }
  ],
  "groups": { "group:facturacion": ["ana@empresa.com", "pedro@empresa.com"] }
}
```

## 6. Comprobar

En el servidor:

```bash
tailscale status          # todos los equipos conectados
tailscale serve status    # el panel publicado
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/login   # 200
```

Desde otra PC (con Tailscale conectado):

```powershell
Test-NetConnection servidor-enpower.tailXXXX.ts.net -Port 443
```

## 7. Problemas típicos

| Síntoma | Causa / arreglo |
|---|---|
| `no existe /dev/net/tun` | falta el módulo `tun`: `sudo modprobe tun`. En WSL, agregá `[boot]` `systemd=true` en `/etc/wsl.conf` y `wsl --shutdown` |
| `tailscale serve` no existe | versión vieja: `sudo tailscale update` |
| Desde otra PC no abre | esa PC no está en la red (revisá `tailscale status`), o la ACL la bloquea |
| Abre pero el panel no carga | el panel no está corriendo, o `serve` apunta a otro puerto (`tailscale serve status`) |
| Me pide login cada vez | `COOKIE_SECURE=1` sin HTTPS: dejalo en `0` si entrás por `http://100.x` |
| La PC se reinicia y no hay panel | revisá que `enpower-panel` y `tailscaled` estén `enabled` |
| Cambió el nombre `.ts.net` | se ve con `tailscale status --json | grep DNSName` |

## 8. Alternativas, para comparar

| | Tailscale | ngrok Free | Cloudflare Tunnel |
|---|---|---|---|
| Abre puertos | no | no | no |
| URL pública | no (solo tu red) | sí | sí (o con Access) |
| HTTPS | sí (con `serve`) | sí | sí |
| Dominio propio | no (usa `.ts.net`) | no | sí |
| Costo | gratis personal / $8 usuario en empresa | $0 con intersticial | $0 + dominio |
| Instalar en cada PC | sí | no | no |
