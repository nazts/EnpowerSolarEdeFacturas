# Requerimientos — Descarga automática de facturas Edenorte

Estado: **captura de requerimientos** (sin desarrollo todavía, según indicación del cliente).

## 1. Objetivo

Cada día, de forma automática, entrar a la Oficina Virtual de Edenorte
(`https://ofv.edenorte.com.do`) con la cuenta de la empresa y descargar las
facturas eléctricas **de todos los contratos asociados a esa cuenta**, guardando
cada PDF con un nombre estandarizado de razón social + NIC.

## 2. Disparo (schedule)

- **Todos los días a las 7:00 a.m.** (hora de República Dominicana, UTC-4).
- Un solo recorrido: recorre **todos** los contratos que estén agregados en la
  plataforma (`/contratos`), sin lista fija en el código. Si mañana se asocia un
  contrato nuevo desde la web, el escaneo lo toma automáticamente.
- En cada corrida se descargan **solo** las facturas de tipo "Recibos de consumos"
  cuyo **corte** (último día del período facturado, campo
  `No. De Días Facturados: dd/mm/aaaa - dd/mm/aaaa`) sea la fecha de ese día.
  Criterio completo en §4; lo que no cumpla queda como pendiente (§7), no se
  descarga "por si acaso".

## 3. Reglas de nombre del archivo

Formato: `<NOMBRE NORMALIZADO> <NIC>.pdf`

### 3.1 Normalización de la razón social

Edenorte imprime el titular como `APELLIDOS, NOMBRES`. Se invierte:

- Factura: `PEREZ GOMEZ, JUAN CARLOS`
- Archivo: `JUAN CARLOS PEREZ GOMEZ 1234567.pdf`

Reglas:

1. Si hay coma: lo que está después de la coma son nombres, antes son apellidos.
   Resultado = `NOMBRES APELLIDOS`.
2. Sin coma (personas jurídicas, p. ej. `ENPOWER SOLAR SRL`): se deja tal cual.
3. Mayúsculas sostenidas, sin tildes problemáticas para el sistema de archivos,
   sin caracteres `\, /, :, *, ?, ", <, >, |`.
4. El nombre del archivo debe coincidir exacto con la razón social normalizada +
   el NIC, para que sea identificable sin abrir el PDF.

### 3.2 Identificadores

- **NIC / contrato** = número de contrato del suministro.
  - Factura de muestra: `1234567` (campo CONTRATO, 7 dígitos).
  - Cuenta de la OFV: `7654321`.
- **Ref. Pago** = NIC + 3 dígitos de secuencia; es la clave del PDF en el portal:
  `1234567` → `1234567022`, `7654321` → `7654321045`. Sirve para armar la URL
  `/factpdf/<ref_pago>`.
- En el nombre del archivo va **solo el NIC**, nunca la Ref. de Pago.
- No se usa el No. de factura en el nombre (queda dentro del PDF y en el índice).

### 3.3 Ejemplos del nombre final

| Razón social en factura | NIC | Archivo |
|---|---|---|
| `PEREZ GOMEZ, JUAN CARLOS` | 1234567 | `JUAN CARLOS PEREZ GOMEZ 1234567.pdf` |
| `RAMIREZ DIAZ, PEDRO ANTONIO` | 7654321 | `PEDRO ANTONIO RAMIREZ DIAZ 7654321.pdf` |
| `ENPOWER SOLAR SRL` (sin coma) | 1234567 | `ENPOWER SOLAR SRL 1234567.pdf` |

## 4. Filtro: qué factura se descarga (y cuál no)

Se descarga **solo** la factura que cumple las dos condiciones:

1. **Tipo = "Recibos de consumos"** (recibo de consumo). Se descartan los demás
   tipos, p. ej. "Irregularidades intencionales".
2. **Fecha de corte = el día de la corrida** (`No. De Días Facturados` termina
   hoy). Las facturas con otro corte no se descargan.

Regla de consumo (dentro de las de tipo recibo de consumo):

- Solo es válida/facturable la que **muestre un número de consumo real**
  (kWh > 0 con cifra impresa).
- Si el consumo **no aparece** (espacio en blanco, sin número) → **no se
  descarga**.
- Consumo en **cero explícito** → *pendiente de confirmar con el cliente*
  (ver §11).

**El PDF no se modifica de ninguna forma.** No se le agregan sellos, marcas,
ni campos: la única transformación es el **nombre del archivo**.

## 5. Datos a extraer de cada factura

- NIC / contrato, No. de factura, Ref. de pago, ENCF
- Titular (razón social tal como aparece) y razón social normalizada
- Suministro, medidor, tarifa, oficina
- Período facturado (inicio – fin) y **fecha de corte**, días facturados
- Consumo (kWh), potencia
- Importe sin subsidio, subsidio, **total facturado**
- **Fecha límite de pago** (`PAGUE ANTES DE`)
- Balance pendiente

## 6. Salida

- PDFs en la carpeta de facturas del proyecto (fuera de git), sin tocar su
  contenido.
- `index.json` junto a los PDFs con todos los campos anteriores + ruta local +
  estado (`descargada` / `no disponible`), para que cada corrida sea un diff y no
  un redescubrimiento.
- Reporte de la corrida: contratos revisados, facturas nuevas descargadas,
  facturas ya existentes, y facturas listadas por el portal pero sin PDF.

## 7. Facturas pendientes: feedback y recordatorio

Una factura puede **cortar un día y no estar subida** todavía a la plataforma
(pasa seguido: la emisión real llega 2–3 días después del corte). El sistema no
debe quedarse callado: tiene que avisar qué contratos cortaban y no aparecen, y
volver a pedirlas.

**Dato necesario:** el portal **no publica** el día de corte del contrato
(`/datos_contrato` solo trae nombre, dirección y teléfono). Se aprende del propio
historial: la fecha de corte del último período conocido de ese contrato
(p. ej. NIC 1234567 corta el día **05** de cada mes). Se guarda por contrato en
`contratos.json` → `dia_corte`.

**Ciclo de cada corrida:**

1. Se calcula qué contratos **cortan hoy** (día de corte esperado = hoy; si el
   mes no tiene ese día, el último día del mes).
2. Para cada uno se busca en el historial la factura de tipo "Recibos de
   consumos" cuyo período cierre hoy.
3. Si está → se descarga, se renombra y el contrato queda **resuelto**.
4. Si **no está** → se registra como **PENDIENTE** en `pendientes.json`:
   `{nic, razon_social, fecha_corte, primer_intento, ultimo_intento, intentos, estado}`.
5. Las corridas siguientes **reintentan primero los pendientes** antes de mirar
   los cortes del día, y los dejan abiertos hasta que aparezcan.
6. El reporte diario incluye una sección explícita: *"Cortaron el dd/mm y aún no
   están en la plataforma: NIC — nombre (intentos: N, desde dd/mm)"*.

**Reglas del recordatorio:**

- Un pendiente no se cierra nunca "por cansancio": se cierra cuando el PDF se
  descarga, o cuando el cliente lo descarta a mano.
- A los **3 días** sin aparecer, el aviso sube de nivel (queda marcado como
  *atrasado*), porque el portal solo conserva las 2 facturas más recientes y una
  factura que nunca se publique se pierde.
- Ventana de reintento: hasta **30 días** desde el corte; después se marca
  `perdida` y se reporta para pedirla por mesa de servicio.

## 8. Manejo de fallas del portal (Edenorte)

Si la plataforma de Edenorte falla —no deja iniciar sesión, da error 500/502,
se cae el sitio, cambia el formulario— **no es un fallo nuestro**. En ese caso el
sistema debe:

- No reintentar en bucle ni fallar en silencio.
- Emitir un **mensaje de alerta**: *"La plataforma de Edenorte tiene problemas"*
  (con la hora del intento y el error técnico resumido), para que la empresa
  sepa que ese día no hubo descarga por causa del portal.
- Registrar el intento en el log de la corrida y reintentar en la siguiente
  ejecución programada.

## 9. Restricciones y hechos del portal (verificados)

- Login por correo + contraseña; credenciales de la empresa en `.env`
  (`EDENORTE_EMAIL`, `EDENORTE_PASSWORD`, `EDENORTE_URL`), archivo ignorado por git.
- Contratos asociados: `/contratos`. Historial por contrato: `/historial/<nic>`.
  PDF: `/factpdf/<ref_pago>`.
- **El portal solo mantiene descargables las 2 facturas más recientes por
  contrato**; las anteriores responden "Factura no disponible actualmente". Por
  eso la corrida debe ser diaria y guardar el PDF el mismo día que aparece.
- El PDF no se puede obtener sin sesión iniciada.


## 10. Caso de referencia (factura de muestra entregada por el cliente)

`Downloads/factura.pdf` — Edenorte, contrato NIC **1234567**, Ref. Pago 1234567022,
No. Factura 202611033807, ENCF E320031847759, Suministro 1112223,
Medidor 06042264, Tarifa BTD, Oficina 2144 Guayabal, titular
`PEREZ GOMEZ, JUAN CARLOS`, período **05/09/2026 – 05/10/2026**
(**corte 05/10/2026**, 30 días), 7 980 kWh, importe sin subsidio RD$355,473.04,
subsidio RD$‑166,797.83, **total RD$188,675.21**, **pagar antes del 07/11/2026**.
Nota: la emisión (08/10/2026) es posterior al corte, así que la detección por
fecha de corte debe leer el período dentro del PDF, no la fecha de emisión.

## 11. Casos de prueba de aceptación (QA)

Estos casos son obligatorios antes de dar el sistema por bueno. Cada uno debe
quedar como prueba automatizada en el repo.

### 11.1 Nombre del archivo

| Razón social en la factura | NIC | Nombre esperado |
|---|---|---|
| `PEREZ GOMEZ, JUAN CARLOS` | 1234567 | `JUAN CARLOS PEREZ GOMEZ 1234567.pdf` |
| `RAMIREZ DIAZ, PEDRO ANTONIO` | 7654321 | `PEDRO ANTONIO RAMIREZ DIAZ 7654321.pdf` |
| `ENPOWER SOLAR SRL` (sin coma) | 1234567 | `ENPOWER SOLAR SRL 1234567.pdf` |
| `A/B: SRL` | 1234568 | caracteres `\ / : * ? " < > |` sustituidos, nunca en el nombre |

- Apellidos compuestos (`RAMIREZ DIAZ`) se mantienen unidos y en orden.
- Se usa el **NIC de 7 dígitos**, nunca la Ref. de Pago (falla típica).
- Caso con Ñ y tildes (`MUÑOZ PÉREZ, JOSÉ`): resultado pendiente de decisión
  (§12), la prueba debe fijar el comportamiento elegido.

### 11.2 Filtro de descarga

- Contrato con dos facturas: una "Recibos de consumos" con corte **ayer** sin
  descargar y una "Irregularidades intencionales" con corte **hoy** → **no se
  descarga ninguna**.
- Contraprueba: "Recibos de consumos" con corte **hoy** → se descarga una sola.
- Factura de consumo con el consumo **en blanco** → no se descarga.

### 11.3 Pendientes y fallas del portal

Secuencia simulada (mock del portal):

| Día | Estado del portal | Resultado esperado |
|---|---|---|
| 0 | sin factura | PENDIENTE (intentos = 1, primer_intento = día 0) |
| 1 | error 502 al iniciar sesión | sigue PENDIENTE, intentos = 2, alerta "La plataforma de Edenorte tiene problemas" |
| 3 | sin factura | PENDIENTE marcado **atrasado** |
| 4 | factura publicada | descargada + renombrada, pendiente cerrado |

- Una corrida que falla por el portal **no debe perder ni reiniciar** los
  pendientes existentes.
- `dia_corte = 31` en un mes de 28/29/30 días → se usa el último día del mes.
- Un contrato recién asociado, sin historial, no rompe la corrida: queda sin
  `dia_corte` hasta su primera factura y se reporta como tal.

## 12. Pendientes / decisiones del cliente

1. Formato exacto del nombre: ¿`JUAN CARLOS PEREZ GOMEZ 1234567.pdf`
   (espacio) o con guion bajo / guion medio?
2. Si la factura no aparece el día del corte: ¿solo se reintenta en la corrida
   diaria de las 7:00, o quieres intentos extra el mismo día (p. ej. 12:00 y 5:00 p.m.)?
3. Consumo impreso en **0 kWh** (no en blanco): ¿se descarga igual o se omite
   como si no tuviera consumo?
4. Canal de la alerta cuando Edenorte falle: ¿aviso en el escritorio, correo,
   WhatsApp o solo registro en el log?
5. Destino final de los PDFs: carpeta local, OneDrive, correo, sistema
   contable… (lo indicará el cliente después).
6. ¿Se conservan tildes y Ñ en el nombre del archivo (`JOSÉ MUÑOZ PÉREZ`) o se
   transliteran a ASCII (`JOSE MUNOZ PEREZ`)? Windows acepta las dos, pero la
   segunda es más segura al subirlas a otros sistemas.



---

## 8. Segunda distribuidora: EdeSur

A diferencia de Edenorte (HTML), el portal de Edesur (`ov.edesur.com.do`) es una
aplicación Angular que consume su propia API: `https://ovapi.edesur.com.do/ovapi`,
autenticada con token Bearer. La integración se hace contra esa API.

**Agregar un contrato** (`Contracts/ValidateContractSGC` + `Contracts/SaveContract`):
se necesita el **NIC** y la **referencia de pago hasta el número antes del guion**
(ej. factura `7654321310-93` → `7654321310`). El nombre se manda provisional (un
solo carácter, el campo es corto); el portal responde con el titular real.
Los **tres dígitos de la referencia suben de uno en uno con cada factura**, así que
si la referencia que se tiene ya no vale se prueban hasta 50 referencias cercanas
(primero hacia adelante).

**Factura a descargar**: `Contract/GetContractBilling` y las facturas por mes
(`IndraComplaints/GetAvabledBills?contractid=NIC&mes=M`); el PDF se pide a
`IndraComplaints/GetBillpdf?facRef=<billId>` y llega en base64.
Se aplican las mismas reglas que en Edenorte: sólo tipo **"Recibos de consumos"**,
fecha de corte = fin del período de facturación, el PDF no se modifica (sólo se
renombra `NOMBRES APELLIDOS NIC.pdf`), y consumo en blanco no se descarga.

Verificado con los NIC `1234567` (corte 13/10/2026, 1,140 kWh, RD$15,051.19) y
`7654321` (corte 13/08/2026, 0 kWh, RD$42.10).


---

## 9. Histórico de facturas por cliente

Además de la factura que corta cada día, el sistema guarda **las últimas facturas
de cada cliente** (3 por defecto, mínimo las 2 anteriores), en `facturas/` y en la
base de datos, sin modificar el PDF.

Al grupo de Telegram va **solo la última factura de cada cliente**, en un
**mensaje separado por cliente** (documento + pie con NIC, nombre normalizado,
monto a pagar, asesor y vencimiento). Las facturas anteriores quedan guardadas en
el panel como histórico, pero no se envían.

Se dispara: en cada corrida diaria y con el botón **Guardar histórico** del panel
(`POST /api/historial`), o por consola:

```
.venv\Scripts\python.exe -m app.runner --historial [--por-cliente 3] [--sin-envio]
```
