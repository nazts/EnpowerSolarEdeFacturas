# Base de datos en Supabase

El proyecto funciona con **SQLite local** o con **Postgres (Supabase)**, sin cambiar
el código: se elige con `DATABASE_URL` en el `.env`.

- `DATABASE_URL` vacío → `data/enpower.db` (lo de siempre).
- `DATABASE_URL=postgresql://…` → Supabase.

## 1. Crear el proyecto

1. Entra a <https://supabase.com>, crea el proyecto (región `us-east-1` es la más
   cercana a RD). Guarda la **contraseña de la base**, la vas a necesitar.
2. En **Project Settings → Database → Connection string** elige
   **Transaction pooler** (puerto **6543**), no la conexión directa: el pooler
   aguanta muchas PC conectadas y evita el límite de conexiones del plan free.
3. Copia la cadena y reemplaza `[YOUR-PASSWORD]` por la contraseña. Debe quedar así:

   ```
   postgresql://postgres.abcdefghij:MIPASSWORD@aws-0-us-east-1.pooler.supabase.com:6543/postgres
   ```

4. Pega esa línea en el `.env` del proyecto:

   ```
   DATABASE_URL=postgresql://postgres.abcdefghij:MIPASSWORD@aws-0-us-east-1.pooler.supabase.com:6543/postgres
   ```

   El `.env` está en `.gitignore`: esa contraseña no se sube al repo.

## 2. Crear las tablas y llevar los datos

Con el `.env` ya configurado:

```bash
.venv\Scripts\python.exe -m app.db --init        # crea las 7 tablas en Supabase
.venv\Scripts\python.exe -m app.db --importar    # copia los datos del SQLite local
.venv\Scripts\python.exe -m app.db --estado      # motor + conteos
```

`--importar` **deja vacías las tablas de destino antes de copiar** (es una copia
completa, no una fusión). Se puede repetir mientras se prueba.

## 3. Volver a SQLite

Borra o comenta `DATABASE_URL` en el `.env`. La base local `data/enpower.db` sigue
intacta.

## 4. Lo que cambia en el panel

- Los datos son los mismos: la capa `app/pg.py` traduce las consultas (`?` → `%s`)
  y las filas a diccionarios, así que el resto del código no se toca.
- Cada consulta viaja a Supabase: una página tarda ~200-400 ms más que en local.
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
- **Conexiones**: usa siempre el pooler (6543). La conexión directa (5432) tiene un
  límite bajo y con varias PC se agota.
