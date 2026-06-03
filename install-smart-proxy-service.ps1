param(
    [string]$ServiceName = "SmartProxyWatchdog",
    [string]$PythonExe = "",
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Restart,
    [switch]$Status,
    [switch]$KeepLegacyWatchdog
)

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-PythonExe {
    if ($PythonExe -and (Test-Path -LiteralPath $PythonExe)) {
        return (Resolve-Path -LiteralPath $PythonExe).Path
    }

    $preferred = "C:\Python314\python.exe"
    if (Test-Path -LiteralPath $preferred) {
        return $preferred
    }

    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    throw "python.exe not found"
}

function Get-ServiceScript {
    $script = Join-Path $PSScriptRoot "smart-proxy-service.py"
    if (-not (Test-Path -LiteralPath $script)) {
        throw "service script not found: $script"
    }
    return (Resolve-Path -LiteralPath $script).Path
}

function Get-LegacyWatchdogProcesses {
    $watchdogScript = Join-Path $PSScriptRoot "smart-proxy-watchdog.ps1"
    if (-not (Test-Path -LiteralPath $watchdogScript)) {
        return @()
    }
    $resolvedWatchdog = (Resolve-Path -LiteralPath $watchdogScript).Path
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.Name -eq "powershell.exe" -or $_.Name -eq "pwsh.exe") -and
        $_.CommandLine -like "*smart-proxy-watchdog.ps1*" -and
        $_.CommandLine -like "*$resolvedWatchdog*" -and
        $_.CommandLine -notlike "*install-smart-proxy-service.ps1*"
    })
}

function Remove-LegacyStartupFallback {
    if ($KeepLegacyWatchdog) {
        return
    }

    $startup = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
    $path = Join-Path $startup "$ServiceName.vbs"
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
        Write-Host "[service] removed legacy startup fallback: $path" -ForegroundColor Yellow
    }
}

function Stop-LegacyWatchdogProcesses {
    if ($KeepLegacyWatchdog) {
        return
    }

    $processes = @(Get-LegacyWatchdogProcesses)
    foreach ($process in $processes) {
        Write-Host "[service] stopping legacy watchdog PID $($process.ProcessId)" -ForegroundColor Yellow
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Remove-LegacyStartupFallback
}

function Invoke-ServiceCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command
    )

    $python = Resolve-PythonExe
    $serviceScript = Get-ServiceScript
    & $python $serviceScript $Command
    if ($LASTEXITCODE -ne 0) {
        throw "service command failed: $Command"
    }
}

function Invoke-ElevatedSelf {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "`"$PSCommandPath`""
    )

    foreach ($key in $PSBoundParameters.Keys) {
        $value = $PSBoundParameters[$key]
        if ($value -is [System.Management.Automation.SwitchParameter]) {
            if ($value.IsPresent) {
                $arguments += "-$key"
            }
        }
        else {
            $arguments += "-$key"
            $arguments += "`"$value`""
        }
    }

    Write-Host "[service] 需要管理员权限安装/管理 Windows Service，稍后会弹出 UAC。" -ForegroundColor Yellow
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    if ($process.ExitCode -ne 0) {
        throw "elevated command failed with exit code $($process.ExitCode)"
    }
}

$explicitAction = $Install -or $Uninstall -or $Start -or $Stop -or $Restart -or $Status
if (-not $explicitAction) {
    $Install = $true
    $Start = $true
}

if ($Status) {
    Invoke-ServiceCommand -Command "status"
    $legacy = @(Get-LegacyWatchdogProcesses)
    if ($legacy.Count -gt 0) {
        Write-Host "[service] watchdog running: PID $($legacy[0].ProcessId)" -ForegroundColor Green
    }
    return
}

if (-not (Test-IsAdmin)) {
    Invoke-ElevatedSelf
    return
}

if ($Uninstall) {
    Invoke-ServiceCommand -Command "remove"
    Remove-LegacyStartupFallback
    return
}

if ($Stop) {
    Invoke-ServiceCommand -Command "stop"
    return
}

if ($Restart) {
    Stop-LegacyWatchdogProcesses
    Invoke-ServiceCommand -Command "restart"
    return
}

if ($Install) {
    Invoke-ServiceCommand -Command "install"
    Stop-LegacyWatchdogProcesses
}

if ($Start) {
    Invoke-ServiceCommand -Command "start"
}
