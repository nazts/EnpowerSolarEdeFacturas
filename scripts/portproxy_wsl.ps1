# Puente entre la LAN y el panel que corre dentro de WSL.
#
# WSL usa una red NAT propia (172.x): las otras PC de la oficina no pueden llegar
# ahi. Esto reenvia el puerto de Windows a la IP actual de la distro.
#
#   .\scripts\portproxy_wsl.ps1 -Crear      crea o actualiza el puente
#   .\scripts\portproxy_wsl.ps1 -Estado     muestra las reglas
#   .\scripts\portproxy_wsl.ps1 -Quitar     lo elimina
#   .\scripts\portproxy_wsl.ps1 -Tarea      crea la tarea que lo rehace al iniciar sesion
#
# Requiere PowerShell como administrador (netsh portproxy y firewall lo exigen).

#Requires -Version 5.1
[CmdletBinding()]
param(
    [int]$Puerto = 8000,
    [string]$Distro = "",
    [switch]$Crear,
    [switch]$Quitar,
    [switch]$Estado,
    [switch]$Tarea
)

$ErrorActionPreference = "Stop"

function Bien($t) { Write-Host "   OK  $t" -ForegroundColor Green }
function Aviso($t) { Write-Host "   !!  $t" -ForegroundColor Yellow }

function Es-Admin {
    ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Netsh-Silencioso([string]$comando) {
    $previo = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { cmd /c "netsh $comando 2>&1" | Out-Null } catch { }
    finally { $ErrorActionPreference = $previo }
}

function Ip-Wsl {
    $argumentos = @("-4")
    if ($Distro) { $argumentos = @("-d", $Distro, "hostname", "-I") }
    else { $argumentos = @("hostname", "-I") }

    $previo = $ErrorActionPreference
    $ErrorActionPreference = "Continue"      # wsl.exe escribe en stderr y PS5.1 corta
    try { $salida = & wsl.exe @argumentos 2>$null } catch { $salida = $null }
    finally { $ErrorActionPreference = $previo }
    if (-not $salida) { return $null }
    $partes = ($salida -replace "`0", "").Trim() -split "\s+"
    return ($partes | Where-Object { $_ -match "^\d{1,3}(\.\d{1,3}){3}$" } | Select-Object -First 1)
}

function Ip-Lan {
    $ip = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -like "192.168.*" -or $_.IPAddress -like "10.*" } |
        Where-Object { $_.InterfaceAlias -notlike "*WSL*" -and $_.InterfaceAlias -notlike "*Loopback*" } |
        Select-Object -First 1
    return $ip.IPAddress
}

if (-not $Crear -and -not $Quitar -and -not $Estado -and -not $Tarea) { $Estado = $true }

if ($Estado) {
    Write-Host "`n== Reglas de portproxy =="
    $reglas = (cmd /c "netsh interface portproxy show v4tov4 2>&1") | Out-String
    if ($reglas -match "Address|Direccion|-----") { $reglas | Select-Object -Skip 4 } else { Write-Host "   (ninguna)" }

    Write-Host "`n== Puertos escuchando (WSL) =="
    $ipWsl = Ip-Wsl
    if ($ipWsl) { Write-Host "   IP de WSL: $ipWsl" } else { Aviso "no pude leer la IP de WSL (la distro esta corriendo?)" }

    Write-Host "`n== Regla de firewall =="
    $fw = Get-NetFirewallRule -DisplayName "Enpower $Puerto" -ErrorAction SilentlyContinue
    if ($fw) { Write-Host ("   Enpower {0}: {1}" -f $Puerto, $fw.Enabled) } else { Aviso "no hay regla 'Enpower $Puerto'" }
    exit 0
}

if (-not (Es-Admin)) {
    throw "Hay que correr PowerShell como administrador (clic derecho > Ejecutar como administrador)"
}

if ($Tarea) {
    $bat = Join-Path $env:TEMP "portproxy_enpower.bat"
    $linea = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -Crear"
    Set-Content -Path $bat -Value "@echo off`r`n$linea" -Encoding ASCII

    $accion = New-ScheduledTaskAction -Execute $bat
    $disparo = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName "EnpowerSolar Portproxy WSL" -Action $accion -Trigger $disparo `
        -Principal $principal -Force `
        -Description "Rehace el puente LAN -> WSL para el panel de Enpower" | Out-Null
    Bien "tarea 'EnpowerSolar Portproxy WSL' creada (se ejecuta al iniciar sesion)"
    exit 0
}

if ($Quitar) {
    Netsh-Silencioso "interface portproxy delete v4tov4 listenport=$Puerto listenaddress=0.0.0.0"
    Bien "regla del puerto $Puerto eliminada"
    exit 0
}

# ------------------------------------------------------------------ crear
$ipWsl = Ip-Wsl
if (-not $ipWsl) { throw "No pude leer la IP de WSL. Esta corriendo la distro? Proba: wsl hostname -I" }
Bien "IP de WSL: $ipWsl"

$ipLan = Ip-Lan
if ($ipLan) { Write-Host "   IP de esta PC en la LAN: $ipLan" }

Netsh-Silencioso "interface portproxy delete v4tov4 listenport=$Puerto listenaddress=0.0.0.0"
Netsh-Silencioso ("interface portproxy add v4tov4 listenport={0} listenaddress=0.0.0.0 " +
                  "connectport={0} connectaddress={1}") -f $Puerto, $ipWsl
Bien "puerto $Puerto -> ${ipWsl}:$Puerto"

if (-not (Get-NetFirewallRule -DisplayName "Enpower $Puerto" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Enpower $Puerto" -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Puerto -Profile Any | Out-Null
    Bien "regla de firewall 'Enpower $Puerto' creada"
} else {
    Bien "la regla de firewall ya existia"
}

# el servicio que hace el reenvio tiene que estar arriba
$svc = Get-Service iphlpsvc -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -ne "Running") {
    Start-Service iphlpsvc
    Bien "servicio iphlpsvc iniciado"
}

Write-Host "`n=============================================================="
Write-Host "  Listo."
if ($ipLan) { Write-Host "  Desde otra PC:  http://${ipLan}:$Puerto" }
Write-Host "  (la IP de WSL cambia al reiniciar: volve a correr -Crear o usa -Tarea)"
Write-Host "=============================================================="
