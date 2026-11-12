# Base de datos en Supabase

El proyecto funciona con **SQLite local** o con **Postgres (Supabase)**, sin cambiar
el código: se elige con `DATABASE_URL` en el `.env`.

- `DATABASE_URL` vacío → `data/enpower.db` (lo de siempre).
- `DATABASE_URL=postgresql://…` → Supabase.

## 1. Crear el proyecto

1. Entra a <https://supabase.com>, crea el proyecto (una región cercana: `us-east-1` o `us-east-2`). Guarda la **contraseña de la base**, la vas a necesitar.
2. En **Project Settings → Database → Connection string** elige
   **Transaction pooler** (puerto **6543**), no la conexión directa: el pooler
   aguanta muchas PC conectadas y evita el límite de conexiones del plan free.
3. Copia la cadena y reemplaza `[YOUR-PASSWORD]` por la contraseña. Si tiene
   caracteres especiales (`@ : / # % ?`), van codificados en la URL: `@` → `%40`,
   `#` → `%23`, `%` → `%25`. Debe quedar así:

   ```
   postgresql://postgres.<project-ref>:MIPASSWORD@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
   ```

4. Pega esa línea en el `.env` del proyecto:

   ```
   DATABASE_URL=postgresql://postgres.<project-ref>:MIPASSWORD@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
   ```

   El `.env` está en `.gitignore`: esa contraseña no se sube al repo.

## 2. Crear las tablas y llevar los datos

Con el `.env` ya configurado:

En Windows (PowerShell o cmd):

```powershell
.venv\Scripts\python.exe -m app.db --init        # crea las 7 tablas en Supabase
.venv\Scripts\python.exe -m app.db --importar    # copia los datos del SQLite local
.venv\Scripts\python.exe -m app.db --estado      # motor + conteos
```

En Linux / WSL es lo mismo con `.venv/bin/python`.

`--importar` **deja vacías las tablas de destino antes de copiar** (es una copia
completa, no una fusión). Se puede repetir mientras se prueba.

## 3. Volver a SQLite

Borra o comenta `DATABASE_URL` en el `.env`. La base local `data/enpower.db` sigue
intacta.

## 4. Lo que cambia en el panel

- Los datos son los mismos: la capa `app/pg.py` traduce las consultas (`?` → `%s`),
  las filas a diccionarios y las fechas a texto, así que el resto del código no se toca.
- **Latencia medida** (Supabase en EE. UU., desde RD): login 0,6 s; páginas del panel
  0,43–0,79 s. En SQLite local eran 5–20 ms. Es el costo de tener la base fuera.
  Se usa un pool de conexiones (psycopg_pool) porque abrir una conexión TLS nueva
  cuesta ~1 s contra el pooler.
- Los PDFs **no se guardan en la base** (ver `docs/qa.md`): la factura que corta se
  manda a Telegram y el panel la sirve desde ahí.

## 5. Columnas nuevas

`facturas.tg_file_id` y `facturas.tg_message_id` existen ya en el esquema de
Postgres: guardan la referencia del mensaje de Telegram para poder volver a abrir
el PDF sin almacenarlo.

## 6. Notas de operación

- **Backups**: Supabase free no hace respaldos automáticos. De vez en cuando exporta
  desde el panel SQL (`Database → Backups`) o usa `pg_dump` con la conexión directa.
- **Pausa por inactividad**: un proyecto free se pausa tras ~7 días sin uso; como el
  worker escribe a diario, en la práctica no pasa. Si se pausa, se reactiva desde el
  panel de Supabase.
- **Conexion directa**: en el plan free es solo IPv6; para `pg_dump` desde la
  oficina usa el pooler o pide la IPv4.
- **Conexiones**: usa siempre el pooler (6543). La conexión directa (5432) tiene un
  límite bajo y con varias PC se agota.
