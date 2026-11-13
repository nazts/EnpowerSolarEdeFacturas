# Errores ya corregidos

Registro de fallos reales que costaron tiempo, con la causa y la regla que evita
repetirlos. **Antes de cerrar un cambio, repasa la lista de comprobación del
final.** Cada entrada enlaza al síntoma visible (lo que vio el usuario), no a la
teoría.

---

## 1. El envío a Telegram "no hacía nada" en Supabase

| | |
|---|---|
| **Síntoma** | El panel decía "Correr ahora" y no salía ninguna factura. Sin error visible: el detalle del histórico quedaba con `errores: ["0"]` o vacío. |
| **Causa** | El código salió de SQLite y la base pasó a Postgres. Dos construcciones solo válidas en SQLite: (a) leer la fila por posición `fila[0]` — en Postgres las filas son `dict` y lanzaba `KeyError: 0` al armar el mensaje; (b) `WHERE corte IS ?` — en Postgres `IS` solo acepta `NULL/TRUE/FALSE` (`syntax error at or near "$2"`); y `? IS NULL` con el parámetro en `NULL` (`could not determine data type of parameter $3`). |
| **Arreglo** | Leer siempre por nombre (`SELECT x AS valor` + `_valor()`), y armar la consulta en dos variantes: `corte = ?` o `corte IS NULL`, sin comparar el parámetro contra `NULL`. |
| **Archivos** | `app/runner.py` (`_valor`, `_campo`, `_monto`, `_vence`, `_fila_factura`), `docs/supabase.md`. |
| **Regla** | Todo SQL nuevo tiene que ser válido en **SQLite y en Postgres**. La capa `app/pg.py` traduce placeholders y tipos, **no reescribe el SQL**. |

## 2. La factura se saltaba en silencio cuando el PDF no estaba en el servidor

| | |
|---|---|
| **Síntoma** | Clientes con facturas pendientes y ninguna enviada; cero errores en el log. |
| **Causa** | `pdf_path` guardado en la base apuntaba a otra máquina (`C:\...` en el servidor Linux o al revés) y el envío hacía `if not ruta.exists(): continue`, sin registrar nada. |
| **Arreglo** | Resolver el archivo por **nombre** dentro de `FACTURAS_DIR`; si no está, volver a bajarlo del portal, guardarlo y actualizar `pdf_path`. Si aun así no se puede, se registra el error en Actividad. Nunca un `continue` mudo. |
| **Archivos** | `app/runner.py` (`_pdf_para_enviar`, `_nombre_pdf`). |
| **Regla** | Un `continue`/`return` silencioso en un proceso por lotes es un bug esperando a ocurrir: siempre log + contador de error. |

## 3. El PDF se acumulaba en disco (y el panel lo necesitaba después)

| | |
|---|---|
| **Síntoma** | La carpeta `facturas/` crecía sin freno y las fichas seguían necesitando el archivo. |
| **Arreglo** | Tras enviar, se guarda `tg_file_id`/`tg_message_id` y **se borra el archivo**. La ruta `/facturas/{id}/pdf` lo pide a Telegram (`getFile` → descarga) cuando ya no está en disco. |
| **Archivos** | `app/runner.py` (`_borrar_pdf`), `app/db.py` (`marcar_enviadas` con `detalles`), `app/dashboard.py` (`ver_pdf`), `app/telegram.py` (`descargar_archivo`). |
| **Regla** | Si se borra el original, hay que tener el respaldo remoto **y** la ruta que lo sirve, probada con una factura ya borrada. |

## 4. Una factura sin PDF cortaba todo el histórico del cliente

| | |
|---|---|
| **Síntoma** | Cliente recién agregado sin facturas, aunque el portal tenía varias. |
| **Causa** | La factura más nueva estaba publicada **sin archivo** y `PdfNoDisponible` cortaba el bucle completo. |
| **Arreglo** | Se salta esa factura y se sigue con las anteriores. Las que el portal lista pero no entrega se guardan como `sin_pdf` para mostrarlas igual. |
| **Archivos** | `app/runner.py` (`sincronizar_historial`). |

## 5. Facturas invisibles: descartadas sin archivo y estados que no se mostraban

| | |
|---|---|
| **Síntoma** | El portal mostraba 5 facturas y el panel 2. |
| **Causa** | (a) Las facturas con "corte distinto" descargaban el PDF y lo tiraban a la basura; (b) `historico_facturas` filtraba solo `estado='descargada'`, así que las `descartada` no aparecían; (c) las que el portal lista sin PDF no se guardaban en ninguna parte. |
| **Arreglo** | Guardar el archivo también cuando se descarta; incluir `descargada` y `descartada` en el histórico; registrar las que no tienen archivo como `sin_pdf` y reintentarlas en cada corrida. |
| **Archivos** | `app/runner.py`, `app/db.py` (`historico_facturas`, `necesita_pdf`, `facturas_sin_pdf`), `app/templates/cliente_detalle.html`. |
| **Regla** | "No se puede descargar" no es lo mismo que "no existe": si el portal lo lista, el panel lo muestra. |

## 6. JSON crudo en el navegador en vez de una pantalla de error

| | |
|---|---|
| **Síntoma** | Al abrir el PDF de una factura vieja, el navegador mostraba `{"detail": "PDF no encontrado"}`. |
| **Causa** | `HTTPException` en una ruta abierta como página devuelve el JSON de FastAPI. |
| **Arreglo** | Manejador global: si la petición acepta `text/html` y no es `/api/*`, se renderiza `error.html` con código, mensaje y botones de vuelta; las rutas `/api` siguen en JSON. Además la ficha no ofrece enlace si no hay archivo disponible (`sin PDF`). |
| **Archivos** | `app/dashboard.py` (`_error_http`, `_pdf_disponible`), `app/templates/error.html`, `tests/test_errores.py`. |
| **Regla** | Cualquier error que pueda ver un humano por navegación necesita página; el JSON se reserva para el JS. |

## 7. Bloqueo de corridas: `msvcrt` en Linux

| | |
|---|---|
| **Síntoma** | `NameError: name 'msvcrt' is not defined` al pulsar "Buscar facturas" en WSL. |
| **Causa** | Se elegía el módulo de bloqueo con `hasattr(msvcrt, ...)` en vez de por plataforma. |
| **Arreglo** | Rama explícita por `sys.platform`: `fcntl.flock` en Linux, `msvcrt.locking` en Windows. |
| **Archivos** | `app/runner.py`. |

## 8. Fecha de corte vacía por el encoding de `pdftotext`

| | |
|---|---|
| **Síntoma** | Facturas guardadas sin fecha de corte; "Días" no aparecía. |
| **Causa** | En WSL `pdftotext` escupe UTF-8 y se decodificaba latin-1: `Días` → `DÃ­as` y el regex no coincidía. |
| **Arreglo** | `pdftotext -layout -enc UTF-8` y decodificación tolerante (utf-8 → cp1252 → latin-1). |
| **Archivos** | `app/pdfdata.py`. |

## 9. `pdftotext` no existe cuando corre el Programador de tareas

| | |
|---|---|
| **Síntoma** | La corrida de las 7:00 guardaba todo como "PDF ilegible" y no enviaba nada; a mano funcionaba. |
| **Causa** | La tarea programada no hereda el PATH de Git, que es de donde salía `pdftotext`. |
| **Arreglo** | `bin/pdftotext.exe` dentro del proyecto y `PDFTOTEXT_BIN` en el `.env`. |
| **Regla** | Si un proceso depende de un binario externo, fijarlo por ruta absoluta/config, no confiar en el PATH. |

## 10. Datos que no se podían guardar/leer tras migrar a Postgres

| | |
|---|---|
| **Síntoma** | `TypeError: datetime is not JSON serializable`, páginas de 4 s, `duplicate key eventos_pkey` al importar. |
| **Arreglo** | Fechas normalizadas a texto en `pg.py`, pool de conexiones (`psycopg_pool`), y `setval` de las secuencias después de importar ids explícitos. |
| **Archivos** | `app/pg.py`, `app/db.py`. |

## 11. Sesiones acumuladas en Edenorte → "credenciales rechazadas"

| | |
|---|---|
| **Síntoma** | Tras varias corridas el portal empezó a rechazar el login. |
| **Causa** | Cada corrida abría sesión y no la cerraba. |
| **Arreglo** | `GET /contratos` para obtener `_csrf` + `POST /user/logout` en un `finally`. |
| **Archivos** | `app/edenorte.py`. |

## 12. `hidden` no ocultaba un campo

| | |
|---|---|
| **Síntoma** | Un bloque con `hidden` se seguía viendo y el JS parecía roto. |
| **Causa** | Una regla del autor (`.campo{display:flex}`) vence al `display:none` del navegador. |
| **Arreglo** | Reset explícito (`.campo[hidden]{display:none}`) y comprobar con `getComputedStyle`, no con el atributo. |

## 13. CSS que rompe la plantilla

| | |
|---|---|
| **Síntoma** | HTTP 500 `Missing end of comment tag`, o toda la hoja de estilos pintada como texto. |
| **Causa** | `{#` pegado a una llave en el CSS (`@media(){#toast{…}}`) lo lee Jinja como comentario; y cortar la cadena por índice puede dejar `style>` sin `<`. |
| **Arreglo** | Espacio después de `{` en esos casos; editar con anclas únicas, no por índices; validar las plantillas offline con `Environment(FileSystemLoader("app/templates"))`. |
| **Regla** | Después de tocar plantillas: compilar todas con Jinja y revisar que `document.body.innerText` no contenga `:root{`. |

## 14. El navegador mostraba cero elementos y parecía un bug de plantilla

| | |
|---|---|
| **Causa** | Tras reiniciar uvicorn la cookie firmada queda inválida y la ruta protegida aterriza en `/login`, que responde 200 y no tiene los elementos buscados. |
| **Regla** | Antes de depurar la vista, imprimir `location.pathname` y volver a iniciar sesión. |

## 15. Pruebas que tocaban datos reales

| | |
|---|---|
| **Síntoma** | Una prueba de permisos borró el contrato `2136559` de producción; otra escribía en Supabase. |
| **Arreglo** | `conftest.py` fuerza `DATABASE_URL=""` y borra con NIC inexistentes. |
| **Regla** | Ninguna prueba escribe en la base real ni llama a los portales: siempre dobles (`MagicMock`) y base temporal. |

## 16. «Factura no disponible actualmente» NO es un fallo de la petición

| | |
|---|---|
| **Síntoma** | El portal lista 6 facturas por contrato; el panel solo bajaba 2 y el resto quedaba "sin PDF". Parecía que la petición estaba mal armada. |
| **Qué se comprobó** | Sobre tres NIC distintos (6997885, 6028583, 6501165) el patrón es idéntico: **las 2 más recientes bajan en PDF (≈119 KB) y todas las anteriores devuelven HTTP 200 con `text/html` de 91 bytes y el texto «Factura no disponible actualmente»**. Se probó con `Referer` del historial, cabeceras completas de navegador, `X-Requested-With`, `?nic=`, POST, barra final y `Cache-Control`, siempre en la misma sesión en la que 045/082/178 sí bajan. El enlace del portal es un `<a href="/factpdf/{ref}">` plano, sin token ni onclick. |
| **Causa real** | **Retención del portal**: Edenorte guarda los PDF de las 2 facturas más recientes por contrato. Las viejas siguen en el listado pero el archivo ya no existe en su servidor. No hay nada que arreglar en nuestro cliente. |
| **Qué hace el panel** | Muestra esas facturas como `sin_pdf` (con su fecha de emisión, en una sección aparte), las reintenta en cada corrida y permite pedir el PDF al portal en el momento (`/facturas/{id}/pdf`) sin guardarlo en disco. |
| **Regla** | Antes de "arreglar" una descarga, comprobar si el portal entrega el archivo a **otro** contrato en la misma sesión: si el patrón se repite (solo las N más nuevas), es retención del proveedor y el trabajo es capturarlas a tiempo, no cambiar la petición. |

---

## Comprobación antes de cerrar un cambio

1. **Motor doble**: ¿el SQL corre en SQLite *y* en Postgres? ¿leo las filas por nombre?
2. **Silencios**: ¿algún `continue`/`except: pass` deja al usuario sin saber qué pasó? Debe quedar en Actividad.
3. **Rutas compartidas**: ¿el archivo se busca por nombre si la ruta es de la otra máquina?
4. **Errores al humano**: ¿puede aparecer un JSON crudo en el navegador? Renderiza `error.html`.
5. **Plantillas**: compilar las 12 con Jinja y comprobar que no hay `{#` pegado ni `{{` dentro de `<script>`.
6. **Pruebas**: suite completa (hoy 162) y, cuando el cambio toca datos, una prueba nueva que falle sin el arreglo.
7. **Datos reales**: nada de POST de prueba contra el cliente real; usar un registro desechable.
8. **Después de enviar**: ¿se guardó `tg_file_id` antes de borrar el archivo?
