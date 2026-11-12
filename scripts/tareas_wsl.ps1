# Tarea programada de Windows que despierta WSL al iniciar sesion.
#
#   .\scripts\tareas_wsl.ps1 -Crear                 (distro Ubuntu)
#   .\scripts\tareas_wsl.ps1 -Crear -Distro Ubuntu-24.04
#   .\scripts\tareas_wsl.ps1 -Quitar
#   .\scripts\tareas_wsl.ps1 -Estado
#
# WSL no se enciende solo: algo tiene que lanzarlo. Esta tarea arranca la distro y,
# dentro, los servicios de systemd del panel y de la corrida diaria.

#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Distro = "Ubuntu",
    [switch]$Crear,
    [switch]$Quitar,
    [switch]$Estado
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tarea = "EnpowerSolar WSL"
$bat = Join-Path $raiz "iniciar_wsl.bat"

function Bien($t) { Write-Host "   OK  $t" -ForegroundColor Green }
function Aviso($t) { Write-Host "   !!  $t" -ForegroundColor Yellow }

if (-not $Crear -and -not $Quitar -and -not $Estado) { $Estado = $true }

if ($Estado) {
    $t = Get-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue
    if ($t) {
        $info = Get-ScheduledTaskInfo -TaskName $tarea
        $ultima = if ($info.LastRunTime -and $info.LastRunTime.Year -gt 1999) { $info.LastRunTime.ToString("yyyy-MM-dd HH:mm") } else { "nunca" }
        Write-Host ("   {0,-20} {1,-10} ultima: {2}" -f $tarea, $t.State, $ultima)
        $accion = ($t.Actions | Select-Object -First 1)
        if ($accion) { Write-Host ("      {0} {1}" -f $accion.Execute, $accion.Arguments) -ForegroundColor DarkGray }
    } else {
        Write-Host "   $tarea no existe" -ForegroundColor DarkGray
    }
    Write-Host "`n   Distros instaladas:"
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) { wsl.exe -l -q } else { Write-Host "   (WSL no esta instalado)" }
    exit 0
}

if ($Quitar) {
    if (Get-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $tarea -Confirm:$false
        Bien "eliminada: $tarea"
    } else {
        Aviso "no existia"
    }
    exit 0
}

# ------------------------------------------------------------------ crear
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL no esta instalado. Corre en PowerShell como administrador: wsl --install -d Ubuntu"
}
if (-not (Test-Path $bat)) {
    throw "Falta $bat"
}

$salida = (wsl.exe -l -q 2>$null) -replace "`0", ""
$instaladas = @($salida | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" })
if (-not ($instaladas | Where-Object { $_ -eq $Distro })) {
    throw "La distro '$Distro' no esta instalada. Instaladas: $($instaladas -join ', ')"
}

# el .bat lee la distro de este archivo (asi no hay que editarlo)
Set-Content -Path (Join-Path $raiz ".distro") -Value $Distro -Encoding ASCII -NoNewline
Bien "distro '$Distro' anotada en .distro"

$usuario = "$env:USERDOMAIN\$env:USERNAME"
$accion = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $raiz
$disparo = New-ScheduledTaskTrigger -AtLogOn -User $usuario
$config = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2)
$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $tarea -Action $accion -Trigger $disparo `
        -Settings $config -Principal $principal -Force `
        -Description "Arranca WSL y los servicios del panel de Enpower Solar" | Out-Null
    Bien "tarea '$tarea' creada (distro: $Distro, al iniciar sesion)"
} catch {
    throw "No se pudo crear la tarea: $($_.Exception.Message). Prueba con PowerShell como administrador."
}

Aviso "los servicios dentro de WSL se instalan con: bash scripts/instalar_wsl.sh"
if (Get-ScheduledTask -TaskName "EnpowerSolar Panel" -ErrorAction SilentlyContinue) {
    Aviso "existe tambien 'EnpowerSolar Panel' (Windows): los dos usan el mismo puerto."
    Aviso "deja solo uno: .\scripts\tareas_windows.ps1 -Quitar"
}
