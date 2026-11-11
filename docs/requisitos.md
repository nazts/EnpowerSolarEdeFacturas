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
- Se descargan las facturas cuyo **corte** (último día del período facturado,
  campo `No. De Días Facturados: dd/mm/aaaa - dd/mm/aaaa`) corresponde a esa
  fecha o que aún no estén descargadas.

## 3. Reglas de nombre del archivo

Formato: `<NOMBRE NORMALIZADO> <NIC>.pdf`

### 3.1 Normalización de la razón social

Edenorte imprime el titular como `APELLIDOS, NOMBRES`. Se invierte:

- Factura: `GARCIA CASTRO, FRANCISCO ALBERT`
- Archivo: `FRANCISCO ALBERT GARCIA CASTRO 7533709.pdf`

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
  - Factura de muestra: `7533709` (campo CONTRATO, 7 dígitos).
  - Cuenta actual de la OFV: `6997885`.
- **Ref. Pago** = NIC + 3 dígitos de secuencia; es la clave del PDF en el portal:
  `7533709` → `7533709022`, `6997885` → `6997885045`. Sirve para armar la URL
  `/factpdf/<ref_pago>`.
- En el nombre del archivo va **solo el NIC**, nunca la Ref. de Pago.
- No se usa el No. de factura en el nombre (queda dentro del PDF y en el índice).

### 3.3 Ejemplos del nombre final

| Razón social en factura | NIC | Archivo |
|---|---|---|
| `GARCIA CASTRO, FRANCISCO ALBERT` | 7533709 | `FRANCISCO ALBERT GARCIA CASTRO 7533709.pdf` |
| `DE LA CRUZ TAVAREZ, JORGE RAMON` | 6997885 | `JORGE RAMON DE LA CRUZ TAVAREZ 6997885.pdf` |
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
  (ver §9).

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

## 7. Manejo de fallas del portal (Edenorte)

Si la plataforma de Edenorte falla —no deja iniciar sesión, da error 500/502,
se cae el sitio, cambia el formulario— **no es un fallo nuestro**. En ese caso el
sistema debe:

- No reintentar en bucle ni fallar en silencio.
- Emitir un **mensaje de alerta**: *"La plataforma de Edenorte tiene problemas"*
  (con la hora del intento y el error técnico resumido), para que la empresa
  sepa que ese día no hubo descarga por causa del portal.
- Registrar el intento en el log de la corrida y reintentar en la siguiente
  ejecución programada.

## 8. Restricciones y hechos del portal (verificados)

- Login por correo + contraseña; credenciales de la empresa en `.env`
  (`EDENORTE_EMAIL`, `EDENORTE_PASSWORD`, `EDENORTE_URL`), archivo ignorado por git.
- Contratos asociados: `/contratos`. Historial por contrato: `/historial/<nic>`.
  PDF: `/factpdf/<ref_pago>`.
- **El portal solo mantiene descargables las 2 facturas más recientes por
  contrato**; las anteriores responden "Factura no disponible actualmente". Por
  eso la corrida debe ser diaria y guardar el PDF el mismo día que aparece.
- El PDF no se puede obtener sin sesión iniciada.


## 9. Caso de referencia (factura de muestra entregada por el cliente)

`Downloads/factura.pdf` — Edenorte, contrato NIC **7533709**, Ref. Pago 7533709022,
No. Factura 202611033807, ENCF E320031847759, Suministro 4237690,
Medidor 06042264, Tarifa BTD, Oficina 2144 Guayabal, titular
`GARCIA CASTRO, FRANCISCO ALBERT`, período **05/09/2026 – 05/10/2026**
(**corte 05/10/2026**, 30 días), 7 980 kWh, importe sin subsidio RD$355,473.04,
subsidio RD$‑166,797.83, **total RD$188,675.21**, **pagar antes del 07/11/2026**.
Nota: la emisión (08/10/2026) es posterior al corte, así que la detección por
fecha de corte debe leer el período dentro del PDF, no la fecha de emisión.

## 10. Pendientes / decisiones del cliente

1. Formato exacto del nombre: ¿`FRANCISCO ALBERT GARCIA CASTRO 7533709.pdf`
   (espacio) o con guion bajo / guion medio?
2. ¿Qué hacer si a las 7:00 a.m. la factura aún no está publicada (reintentar más
   tarde el mismo día)?
3. Consumo impreso en **0 kWh** (no en blanco): ¿se descarga igual o se omite
   como si no tuviera consumo?
4. Canal de la alerta cuando Edenorte falle: ¿aviso en el escritorio, correo,
   WhatsApp o solo registro en el log?
5. Destino final de los PDFs: carpeta local, OneDrive, correo, sistema
   contable… (lo indicará el cliente después).

