# QA estricto — 11/11/2026

Revisión en cuatro frentes (lógica backend, seguridad, frontend, pruebas/config),
más verificación en vivo contra la app en marcha. Suite: **76 pruebas en verde**.

## Corregido

### Crítico
- **Edesur tumbaba la corrida completa** ante un timeout, un `ConnectionError` o una
  respuesta no-JSON: ahora se traduce a `EdesurError` y el bucle por NIC sigue con el
  siguiente cliente (`app/edesur.py`, `app/runner.py`).
- **Sin CSRF**: se valida `Origin`/`Referer` en todo método que cambia datos
  (`app/dashboard.py`). Un POST con origen ajeno devuelve 403.

### Alto
- **Reenvío de facturas**: `guardar_factura` usaba `INSERT OR REPLACE`, que borraba y
  recreaba la fila (perdía `enviada`). Ahora es upsert por `ref_pago` y conserva
  id, `enviada` y `created_at`.
- **Día de corte equivocado en Edenorte**: se aprendía de la fecha de emisión (cae 1-3
  días después del corte). Ahora se lee del PDF más reciente (`EdenorteClient.ultimo_corte`).
- **Rutas destructivas sin rol**: `/api/corrida`, `/api/historial`, `/api/test-telegram`,
  `/api/telegram/*`, `/export/excel`, eliminar contrato/proyecto → solo admin.
- **Fuerza bruta en el login**: 5 fallos por IP o por usuario en 15 minutos → 429.
  Además, cuando el usuario no existe se verifica igual un hash de relleno (no se
  pueden enumerar usuarios por tiempo de respuesta).
- **Pendientes que nunca se cerraban** si la factura llegaba por el histórico.
- **Doble corrida simultánea** (panel + tarea de Windows): lock de archivo
  `data/runner.lock`; la segunda devuelve "ya hay una corrida en curso".
- **Se marcaban como enviadas facturas que no se mandaron** (y al revés): ahora se
  marcan exactamente las del álbum.
- **Referencia de pago visible con Edenorte**: `.campo[hidden]` no ocultaba por el
  `display:flex` de la rejilla.
- **XSS en el `confirm()` de usuarios** (`onsubmit` con el nombre del usuario): ahora
  va por `tojson`.

### Medio / bajo
- Redirección abierta en el login (`next=//otro-sitio`).
- `/api/docs` y `/openapi.json` públicos: deshabilitados.
- Cabeceras `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`.
- Fórmulas en el Excel exportado (`=`, `+`, `-`, `@`).
- Captions de Telegram sin escapar (un `&` o `<` en el nombre hacía fallar el envío).
- `ver_pdf` sirve solo archivos dentro de `facturas/`.
- `eliminar_contrato` borra también sus pendientes.
- Clientes inactivos/borrados ya no se procesan ni se consultan.
- El histórico corre aunque no haya cortes hoy.
- `telegram.html` con manejo de errores (ya no queda "Buscando…" colgado).
- Botones de acción deshabilitados mientras corren (evita doble clic).
- `dia_corte`, latitud y longitud con rango en el formulario.
- Rol y nombre de usuario validados; no se sobrescribe un usuario existente.

## Pendiente (no bloquea, decisión del cliente)

1. **`.secret_key` vieja en el historial de git** (commit `cf2dd53`). La clave actual es
   distinta y ya rotó, así que ese valor no sirve para firmar sesiones. Si se quiere
   borrar del historial: `git filter-repo --path .secret_key --invert-paths` (reescribe
   el repo; hay que avisar a quien tenga clones).
2. **Revocación de sesión**: el token dura 10 h y no se invalida al cambiar la
   contraseña. Requiere una columna `token_version` y comprobarla al leer el token.
3. **HTTPS**: la cookie `Secure` solo se activa con `COOKIE_SECURE=1`. Publicar por
   Tailscale/Cloudflare (ver `docs/despliegue.md`), nunca el puerto 8000 directo.
4. **Cobertura de pruebas**: `db.py`, `auth.py`, `dashboard.py`, `excel.py` y
   `edenorte.py` siguen sin pruebas propias (se añadieron 5 en `runner`/`db`).
   `httpx` no está en `requirements.txt`, que hace falta para probar el panel.
5. **`requirements.txt`** sin pin (`>=`): conviene `~=` o un lock; y separar
   `requirements-dev.txt` para `pytest`.
6. **`@app.on_event("startup")`** está obsoleto en FastAPI (usar `lifespan`).
7. **Tarea programada**: se llama "…Edenorte" pero corre las dos distribuidoras;
   `data/runner.log` crece sin rotar y no recupera una corrida perdida si la PC
   estaba apagada a las 7:00.
8. **`guardar_equipo` sobrescribe** todos los campos de la ficha: guardar el
   formulario vacío borra dirección/teléfono/coordenadas.
