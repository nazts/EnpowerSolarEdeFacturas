# Noor / Weco — Plantilla de datos, funciones y alertas

Referencia completa de lo que expone la app Noor (Weco) para cada planta/cliente.
Los datos se sacan de la API `noor.olpess-api.com` (ver `app/noor.py`), sin el teléfono.

## 1. Datos por planta (en vivo)

| Campo | Español | Unidad | Endpoint |
|---|---|---|---|
| `pvPower` | Potencia FV (solar) | kW | `AggPlantEnergy/{id}` |
| `loadPower` | Carga / consumo | W | `AggPlantEnergy/{id}` |
| `pvDayEnergy` | Generación de hoy | Wh | `AggPlantEnergy/{id}` |
| `pvTotalEnergy` | Generación total | kWh | `AggPlantEnergy/{id}` |
| `gridPower` | Red (import/export) | W | `getInverterHomeEnergyV2` |
| `batteryPower` | Batería (carga/descarga) | W | `getInverterHomeEnergyV2` |
| `soc` | Estado de carga de batería | % | `getInverterHomeEnergyV2` |
| `temperature` | Temperatura del equipo | °C | `getInverterHomeEnergyV2` |

Campos adicionales de energía (series día/mes/año, `getInverterHomeEnergyV2`):
`pvSelfUse` (autoconsumo FV), `gridSelfUse`, `purchasingTotal` (comprado a la red),
`generatorTotal` (generador), `todayCharge`/`todayDischarge` (batería hoy),
`imported`/`exported` (importado/exportado de la red), `solarInverter`, `bat`, `ev`.

## 2. Estructura de la planta

- `POST /api/plant/homePlantPage` → lista de plantas: `{id, plantName}` (53 clientes de Enpower).
- `GET /api/plant/getDeviceList/{id}` → árbol de dispositivos:
  `{id, alias, dataLoggerSn, deviceSn, dtc, children}`.
  - `deviceSn` = número de serie del inversor.
  - `dataLoggerSn` = datalogger.
  - `dtc` = código de tipo de equipo (sirve para traducir alarmas).

## 3. Modelos de equipo (27) — diccionario de alarmas

`lv`, `lvhesy`, `5k0smart`, `tsth`, `thMax`, `tq`, `esx`, `stk`, `ths`,
`bat0`…`bat17` (18 baterías). Son las claves del diccionario de alarmas.

Cada código de alarma tiene: `value` (bit), `type` (1=aviso, 0=fallo) y `label`.
**2236 códigos · 657 etiquetas únicas**, todas traducidas al español en
`app/data/noor_alarmas_es.json` (mapa EN→ES en `noor_traduccion_alarmas.json`).

Ejemplos de traducción:
- `Grid Over Freq` → Sobrefrecuencia de red
- `Battery Over Voltage` → Sobretensión de batería
- `Inv Over Current` → Sobrecorriente del inversor
- `BMS Comm Fail` → Fallo de comunicación BMS
- `Discharge Over Current` → Sobrecorriente de descarga

## 4. Alarmas de una planta

`GET /api/home/getPlantErrorList` → cada alarma:
`{state (0=recuperada, 1=activa), deviceSn, openTime, closeTime, type (1=aviso, 0=fallo), description}`.
La `description` se traduce con `noor.traducir_alarma(descripcion)`.

## 5. Firmware

`POST /api/upgrade/getUpgradeLogs` → historial de actualizaciones por equipo:
`{tipo, serialNumber, version, fecha, resultado (Succeeded/Fault)}`.
La firma MD5/AES SOLO aplica a los endpoints OTA `open.*` (firmware por Bluetooth).

## 6. Funcionalidades de la app (settings)

- **Device Controls**: Basic Setting, Expert Setting, Tariff Settings.
- **Batería/ESS**: planes de carga/descarga (`setChargeDisChargePlan`), precio de
  electricidad (`setElectricityPrice`), hora del sistema.
- **Cargador EV**: `chargeGunOperation`, `reserveCharge`, `startCharge`/`stopCharge`.
- **Modo Bluetooth**: emparejamiento y OTA de firmware.
- **Plant Management**: agregar/editar plantas, compartir acceso, cuentas.

## 7. Login

`POST /api/login` con FormData (email, password, phoneOSType, system, loginTime,
mobileId, `encryption=false`) + headers `Logintool: Phone`, `System`, `Operationtime`.
Devuelve token JWT → va en `Authorization` de todas las consultas.
