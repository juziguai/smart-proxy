param(
    [string]$TaskName = "SmartProxyWatchdog",
    [switch]$Uninstall,
    [switch]$Start,
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

$WatchdogScript = Join-Path $PSScriptRoot "smart-proxy-watchdog.ps1"
if (-not (Test-Path -LiteralPath $WatchdogScript)) {
    throw "watchdog script not found: $WatchdogScript"
}

function Get-WindowsServiceWatchdog {
    Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq $TaskName -and $_.PathName -like "*smart-proxy-service.py*"
    }
}

function Get-StartupFallbackPath {
    $startup = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
    return (Join-Path $startup "$TaskName.vbs")
}

function Install-StartupFallback {
    $path = Get-StartupFallbackPath
    $escapedWatchdog = $WatchdogScript.Replace('"', '""')
    $command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$escapedWatchdog"""
    $escapedCommand = $command.Replace('"', '""')
    $content = @(
        'Set shell = CreateObject("WScript.Shell")',
        "shell.Run ""$escapedCommand"", 0, False"
    )
    Set-Content -LiteralPath $path -Value $content -Encoding ASCII
    Write-Host "[watchdog] installed startup fallback: $path"
    return $path
}

function Remove-StartupFallback {
    $path = Get-StartupFallbackPath
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
        Write-Host "[watchdog] removed startup fallback: $path"
    }
}

function Get-RunningWatchdogProcesses {
    $resolvedWatchdogScript = (Resolve-Path -LiteralPath $WatchdogScript).Path
    Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -eq 'powershell.exe' -or $_.Name -eq 'pwsh.exe') -and
        $_.CommandLine -like "*-File*$resolvedWatchdogScript*" -and
        $_.CommandLine -notlike '*-Status*' -and
        $_.CommandLine -notlike '*-Once*' -and
        $_.CommandLine -notlike '*-Command*'
    }
}

function Start-WatchdogNow {
    $existing = @(Get-RunningWatchdogProcesses)
    if ($existing.Count -gt 0) {
        Write-Host "[watchdog] watchdog already running: PID $($existing[0].ProcessId)"
        return
    }

    Start-Process `
        -WindowStyle Hidden `
        -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $WatchdogScript)
    Write-Host "[watchdog] started watchdog process"
}

if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[watchdog] uninstalled scheduled task: $TaskName"
    }
    else {
        Write-Host "[watchdog] scheduled task not found: $TaskName"
    }
    Remove-StartupFallback
    return
}

$serviceWatchdog = Get-WindowsServiceWatchdog
if ($serviceWatchdog) {
    Remove-StartupFallback
    Write-Host "[watchdog] Windows Service '$($serviceWatchdog.Name)' already manages smart-proxy; legacy scheduled task/startup fallback will not be installed." -ForegroundColor Yellow
    return
}

$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$WatchdogScript`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 30)

$installedTask = $false
try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "Keeps smart-proxy.py healthy on 127.0.0.1:8889/8890." `
        -Force | Out-Null
    $installedTask = $true
    Write-Host "[watchdog] installed scheduled task: $TaskName"
}
catch {
    Write-Host "[watchdog] scheduled task install failed: $($_.Exception.Message)"
    Write-Host "[watchdog] falling back to current-user Startup folder."
    Install-StartupFallback | Out-Null
}

if ($Start -or $RunNow) {
    if ($installedTask) {
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "[watchdog] started scheduled task: $TaskName"
    }
    else {
        Start-WatchdogNow
    }
}
