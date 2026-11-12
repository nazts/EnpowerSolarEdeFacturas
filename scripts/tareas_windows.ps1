# Tareas programadas del panel de Enpower Solar (Windows).
#
#   .\scripts\tareas_windows.ps1 -Crear         registra las dos tareas
#   .\scripts\tareas_windows.ps1 -Quitar        las borra
#   .\scripts\tareas_windows.ps1 -Estado        muestra si existen, con que ruta corren
#
# Tareas:
#   "EnpowerSolar Panel"     arranca el panel al iniciar sesion
#   "EnpowerSolar Facturas"  descarga diaria a las 07:00 (corre aunque nadie haya
#                            iniciado sesion, si Windows lo permite)

#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Crear,
    [switch]$Quitar,
    [switch]$Estado
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = Join-Path $raiz ".venv\Scripts\python.exe"
$tareaPanel = "EnpowerSolar Panel"
$tareaFacturas = "EnpowerSolar Facturas"
$tareaWsl = "EnpowerSolar WSL"

function Bien($t) { Write-Host "   OK  $t" -ForegroundColor Green }
function Aviso($t) { Write-Host "   !!  $t" -ForegroundColor Yellow }

if (-not $Crear -and -not $Quitar -and -not $Estado) { $Estado = $true }

function Fecha-Tarea($valor) {
    if ($valor -and $valor.Year -gt 1999) { return $valor.ToString("yyyy-MM-dd HH:mm") }
    return "-"
}

function Estado-Tareas {
    foreach ($nombre in @($tareaPanel, $tareaFacturas, $tareaWsl)) {
        $t = Get-ScheduledTask -TaskName $nombre -ErrorAction SilentlyContinue
        if (-not $t) {
            Write-Host ("   {0,-22} no existe" -f $nombre) -ForegroundColor DarkGray
            continue
        }
        $info = Get-ScheduledTaskInfo -TaskName $nombre
        Write-Host ("   {0,-22} {1,-10} proxima: {2,-16} ultima: {3}" -f `
                $nombre, $t.State, (Fecha-Tarea $info.NextRunTime), (Fecha-Tarea $info.LastRunTime))
        $accion = ($t.Actions | Select-Object -First 1)
        if ($accion) {
            $ejecuta = "$($accion.Execute) $($accion.Arguments)".Trim()
            $marca = if ($ejecuta -like "*$raiz*") { "" } else { "  <-- NO apunta a este proyecto" }
            Write-Host ("      {0}{1}" -f $ejecuta, $marca) -ForegroundColor DarkGray
        }
    }
}

if ($Quitar) {
    foreach ($nombre in @($tareaPanel, $tareaFacturas)) {
        if (Get-ScheduledTask -TaskName $nombre -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $nombre -Confirm:$false
            Bien "eliminada: $nombre"
        }
    }
    exit 0
}

if ($Estado) { Estado-Tareas; exit 0 }

# ------------------------------------------------------------------ crear
if (-not (Test-Path $python)) {
    throw "No existe $python. Corre primero scripts\instalar_windows.ps1"
}

$usuario = "$env:USERDOMAIN\$env:USERNAME"
$batPanel = Join-Path $raiz "iniciar_panel.bat"
$batFacturas = Join-Path $raiz "scripts\correr_runner.bat"

foreach ($archivo in @($batPanel, $batFacturas)) {
    if (-not (Test-Path $archivo)) { throw "Falta $archivo" }
}

# --- 1. panel al iniciar sesion (necesita la sesion: sin sesion no hay panel)
$accionPanel = New-ScheduledTaskAction -Execute $batPanel -Argument "/tarea" -WorkingDirectory $raiz
$disparoPanel = New-ScheduledTaskTrigger -AtLogOn -User $usuario
$configPanel = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$principalPanel = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $tareaPanel -Action $accionPanel -Trigger $disparoPanel `
        -Settings $configPanel -Principal $principalPanel -Force `
        -Description "Panel web de facturas de Enpower Solar (al iniciar sesion)" | Out-Null
    Bien "tarea '$tareaPanel' creada (al iniciar sesion)"
} catch {
    Aviso "no se pudo crear '$tareaPanel': $($_.Exception.Message)"
    Aviso "si dice 'acceso denegado', abre PowerShell como administrador y repite"
}

# --- 2. descarga diaria 07:00
$accionFact = New-ScheduledTaskAction -Execute $batFacturas -WorkingDirectory $raiz
$disparoFact = New-ScheduledTaskTrigger -Daily -At "07:00"
$configFact = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)

# S4U: corre sin sesion iniciada (no pide contraseña). Si Windows lo rechaza,
# se cae a Interactive y se avisa.
$creada = $false
try {
    $principalS4U = New-ScheduledTaskPrincipal -UserId $usuario -LogonType S4U -RunLevel Limited
    Register-ScheduledTask -TaskName $tareaFacturas -Action $accionFact -Trigger $disparoFact `
        -Settings $configFact -Principal $principalS4U -Force `
        -Description "Descarga diaria de facturas (Edenorte + Edesur) y envio a Telegram" | Out-Null
    $creada = $true
    Bien "tarea '$tareaFacturas' creada (diaria 07:00, corre sin sesion iniciada)"
} catch {
    Aviso "S4U no disponible ($($_.Exception.Message)); se usa sesion interactiva"
}

if (-not $creada) {
    try {
        Register-ScheduledTask -TaskName $tareaFacturas -Action $accionFact -Trigger $disparoFact `
            -Settings $configFact -Principal $principalPanel -Force `
            -Description "Descarga diaria de facturas (Edenorte + Edesur) y envio a Telegram" | Out-Null
        Bien "tarea '$tareaFacturas' creada (diaria 07:00, requiere sesion iniciada)"
        Aviso "si la PC reinicia y nadie entra, la corrida se hace al iniciar sesion"
    } catch {
        throw "No se pudo crear '$tareaFacturas': $($_.Exception.Message)"
    }
}

if (Get-ScheduledTask -TaskName $tareaWsl -ErrorAction SilentlyContinue) {
    Aviso "tambien existe '$tareaWsl': el panel de Windows y el de WSL usan el mismo puerto."
    Aviso "deja solo uno de los dos (tareas_windows.ps1 -Quitar / tareas_wsl.ps1 -Quitar)"
}
