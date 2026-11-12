# Instalador del panel de Enpower Solar en Windows.
#
# Uso (PowerShell, dentro de la carpeta del proyecto):
#   .\scripts\instalar_windows.ps1                  instala y deja todo listo
#   .\scripts\instalar_windows.ps1 -Red             abre el panel a la red local
#   .\scripts\instalar_windows.ps1 -Firewall        agrega la regla del puerto (pide administrador)
#   .\scripts\instalar_windows.ps1 -Puerto 8080     cambia el puerto
#   .\scripts\instalar_windows.ps1 -SinTareas       no crea las tareas programadas
#   .\scripts\instalar_windows.ps1 -OlvidarEnv      vuelve a copiar .env.produccion sobre .env
#
# Es idempotente: se puede correr las veces que haga falta.

#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Python = "",
    [int]$Puerto = 8000,
    [switch]$SinTareas,
    [switch]$Firewall,
    [switch]$Red,
    [switch]$OlvidarEnv
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $raiz

# UTF-8 sin BOM: con -Encoding UTF8 de PowerShell 5.1 se cuela un BOM y el
# primer nombre de variable del .env queda corrupto para config.py.
$utf8 = New-Object System.Text.UTF8Encoding($false)
$rutaEnv = Join-Path $raiz ".env"

function Paso($texto) { Write-Host "`n== $texto" -ForegroundColor Cyan }
function Bien($texto) { Write-Host "   OK  $texto" -ForegroundColor Green }
function Aviso($texto) { Write-Host "   !!  $texto" -ForegroundColor Yellow }
function Leer-Env($ruta) { return [IO.File]::ReadAllText($ruta, $utf8) }
function Escribir-Env($ruta, $texto) { [IO.File]::WriteAllText($ruta, $texto, $utf8) }
function Valor-Env($texto, $clave) {
    if ($texto -match "(?m)^$clave=([^\r\n]*)") { return $Matches[1].Trim() }
    return ""
}
function Poner-Env($texto, $clave, $valor) {
    if ($texto -match "(?m)^$clave=") {
        return ($texto -replace "(?m)^$clave=[^\r\n]*", "$clave=$valor")
    }
    return ($texto.TrimEnd() + "`r`n$clave=$valor`r`n")
}

Write-Host "=============================================================="
Write-Host "  Enpower Solar - instalacion en Windows"
Write-Host "  carpeta: $raiz"
Write-Host "=============================================================="

# --------------------------------------------------------------- 1. Python
Paso "Buscando Python 3.11 o superior"

function Probar-Python($exe, $argumentos) {
    try {
        $salida = & $exe @argumentos -c "import sys; print(sys.executable); print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $salida) { return $null }
        $lineas = @($salida)
        $ruta = $lineas[0].Trim()
        $version = [version]$lineas[1].Trim()
        if ($version -ge [version]"3.11") {
            return [pscustomobject]@{ Ruta = $ruta; Version = $version }
        }
    } catch { }
    return $null
}

$py = $null
if ($Python) {
    $py = Probar-Python $Python @()
    if (-not $py) { throw "El Python indicado no sirve: $Python (hace falta 3.11+)" }
} else {
    foreach ($candidato in @(
            @{ exe = "py"; argumentos = @("-3") },
            @{ exe = "python"; argumentos = @() },
            @{ exe = "python3"; argumentos = @() },
            @{ exe = "$env:LOCALAPPDATA\Python\bin\python.exe"; argumentos = @() })) {
        if ($candidato.exe -like "*\*" -and -not (Test-Path $candidato.exe)) { continue }
        $py = Probar-Python $candidato.exe $candidato.argumentos
        if ($py) { break }
    }
}
if (-not $py) {
    throw "No encontre Python 3.11+. Instalalo desde https://www.python.org/downloads/ (marca 'Add python.exe to PATH') y vuelve a correr este script."
}
Bien "Python $($py.Version) en $($py.Ruta)"

# --------------------------------------------------------------- 2. Entorno
Paso "Creando el entorno virtual (.venv)"
$vpy = Join-Path $raiz ".venv\Scripts\python.exe"

$venvSano = $false
if (Test-Path $vpy) {
    & $vpy -c "import sys" 2>$null
    $venvSano = ($LASTEXITCODE -eq 0)
    if (-not $venvSano) { Aviso "el .venv existe pero no arranca (cambio el Python base): se recrea" }
}

if (-not $venvSano) {
    & $py.Ruta -m venv --clear .venv
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el entorno virtual" }
    & $vpy -c "import sys"
    if ($LASTEXITCODE -ne 0) { throw "El entorno virtual creado no arranca: revisa la instalacion de Python" }
    Bien "entorno creado"
} else {
    Bien "ya existia y funciona"
}

Paso "Instalando dependencias"
& $vpy -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { Aviso "no se pudo actualizar pip (sigo con el que hay)" }
& $vpy -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Fallo la instalacion de dependencias (revisa la conexion)" }
Bien "dependencias al dia"

Paso "Verificando las dependencias que usa el proyecto"
& $vpy -c "import importlib.util as u, sys; faltan=[m for m in ('fastapi','uvicorn','jinja2','openpyxl','multipart','requests','psycopg','psycopg_pool') if u.find_spec(m) is None]; print('   faltan:', ', '.join(faltan)) if faltan else None; sys.exit(1 if faltan else 0)"
if ($LASTEXITCODE -ne 0) {
    throw "Faltan dependencias: corre  .venv\Scripts\python.exe -m pip install -r requirements.txt"
}
Bien "todas las dependencias importan"

# --------------------------------------------------------------- 3. .env
Paso "Preparando el archivo .env"

if ($OlvidarEnv) {
    if (-not (Test-Path ".env.produccion")) {
        throw "-OlvidarEnv necesita .env.produccion y no existe. No se toco nada."
    }
    if (Test-Path ".env") {
        $respaldo = ".env.respaldo-$(Get-Date -Format yyyyMMddHHmmss)"
        Copy-Item ".env" $respaldo
        Remove-Item ".env"
        Aviso "el .env anterior quedo en $respaldo"
    }
}

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.produccion") {
        Copy-Item ".env.produccion" ".env"
        Bien "creado a partir de .env.produccion (credenciales de la empresa)"
    } elseif (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Aviso "creado a partir de .env.example: hay que llenar las credenciales"
    } else {
        throw "No encuentro .env, .env.produccion ni .env.example"
    }
} else {
    Bien "ya existia"
}

# --- alcance del servidor
$envTexto = Leer-Env $rutaEnv
$hostActual = Valor-Env $envTexto "HOST"

if ($Red) {
    if ($hostActual -ne "0.0.0.0") {
        $envTexto = Poner-Env $envTexto "HOST" "0.0.0.0"
        Bien "HOST=0.0.0.0 (accesible desde otras PC de la oficina)"
    } else {
        Bien "HOST=0.0.0.0 (ya estaba)"
    }
} elseif ($hostActual -eq "0.0.0.0") {
    # .env.produccion viene abierto a la red; sin -Red se deja solo en esta PC
    $envTexto = Poner-Env $envTexto "HOST" "127.0.0.1"
    Aviso "HOST estaba en 0.0.0.0: lo deje en 127.0.0.1 (usa -Red para abrirlo a la red)"
} else {
    Bien "HOST=$(if ($hostActual) { $hostActual } else { '127.0.0.1 (por defecto)' })"
}

# --- puerto: solo cambia si se pidio uno distinto
if ($PSBoundParameters.ContainsKey("Puerto") -and (Valor-Env $envTexto "PORT") -ne "$Puerto") {
    $respaldo = ".env.respaldo-$(Get-Date -Format yyyyMMddHHmmss)"
    Copy-Item ".env" $respaldo
    $envTexto = Poner-Env $envTexto "PORT" "$Puerto"
    Aviso "PORT cambiado a $Puerto (respaldo en $respaldo)"
}
Escribir-Env $rutaEnv $envTexto

$puertoFinal = Valor-Env (Leer-Env $rutaEnv) "PORT"
if (-not $puertoFinal) { $puertoFinal = "8000" }
Bien "puerto $puertoFinal"

# --- base de datos
$databaseUrl = Valor-Env (Leer-Env $rutaEnv) "DATABASE_URL"
if ($databaseUrl) {
    Bien "base de datos: Supabase/Postgres"
} else {
    Aviso "DATABASE_URL vacio: se usara SQLite local (data\enpower.db)"
}

# --------------------------------------------------------------- 4. Base
Paso "Revisando la base de datos"
& $vpy -m app.db --init
if ($LASTEXITCODE -ne 0) { throw "No se pudo preparar la base de datos" }
& $vpy -m app.db --estado
if ($LASTEXITCODE -ne 0) { Aviso "no se pudo leer el estado de la base" }
Bien "base lista"

# --------------------------------------------------------------- 5. Carpetas
Paso "Creando carpetas de trabajo"
foreach ($carpeta in @("data", "facturas", "exports", "logs")) {
    if (-not (Test-Path $carpeta)) { New-Item -ItemType Directory $carpeta | Out-Null }
}
Bien "data, facturas, exports, logs"

# --------------------------------------------------------------- 6. pdftotext
Paso "Comprobando pdftotext (lectura de los PDF)"
$pdftotext = Get-Command pdftotext -ErrorAction SilentlyContinue
if (-not $pdftotext) {
    foreach ($ruta in @("C:\Program Files\Git\mingw64\bin\pdftotext.exe",
                        "C:\Program Files\Git\usr\bin\pdftotext.exe")) {
        if (Test-Path $ruta) { $pdftotext = $ruta; break }
    }
}
if ($pdftotext) {
    Bien "pdftotext disponible"
} else {
    Aviso "no hay pdftotext: viene con Git for Windows o con poppler."
    Aviso "sin el, las facturas se descargan pero no se pueden leer sus datos."
}

# --------------------------------------------------------------- 7. Firewall
Paso "Firewall de Windows"
if ($Firewall) {
    $esAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $esAdmin) { throw "Para -Firewall hay que abrir PowerShell como administrador" }
    $regla = "Enpower $puertoFinal"
    if (Get-NetFirewallRule -DisplayName $regla -ErrorAction SilentlyContinue) {
        Bien "la regla '$regla' ya existe"
    } else {
        New-NetFirewallRule -DisplayName $regla -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $puertoFinal -Profile Any | Out-Null
        Bien "regla '$regla' creada"
    }
} else {
    Aviso "sin regla de firewall (usa -Firewall si otras PC no pueden entrar)"
}

# --------------------------------------------------------------- 8. Tareas
Paso "Tareas programadas de Windows"
if ($SinTareas) {
    Aviso "omitidas (-SinTareas)"
} else {
    & (Join-Path $raiz "scripts\tareas_windows.ps1") -Crear
    if ($LASTEXITCODE -ne 0) { Aviso "no se pudieron crear las tareas" }
}

# --------------------------------------------------------------- 9. Prueba
Paso "Prueba rapida"
& $vpy -c "from app import config, db; print('   modulo ok, motor:', 'postgres' if db.pg.es_postgres(db.pg.limpiar_url(config.DATABASE_URL)) else 'sqlite')"
if ($LASTEXITCODE -ne 0) { throw "El proyecto no arranca: revisa el .env" }

Write-Host "`n=============================================================="
Write-Host "  Listo." -ForegroundColor Green
Write-Host "  Arrancar el panel:  .\iniciar_panel.bat"
Write-Host "  O desde consola:    .venv\Scripts\python.exe run.py"
if ($Red) {
    Write-Host "  Desde otra PC:      http://<IP-de-esta-PC>:$puertoFinal  (ipconfig)"
} else {
    Write-Host "  Solo esta PC:       http://127.0.0.1:$puertoFinal"
}
Write-Host "=============================================================="
