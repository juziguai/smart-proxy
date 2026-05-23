param(
    [string]$ProxyScript = (Join-Path $PSScriptRoot "smart-proxy.py"),
    [string]$PythonExe = "",
    [int]$ProxyPort = 8889,
    [int]$DashboardPort = 8890,
    [string]$DashboardHealthUrl = "http://127.0.0.1:8890/api/runtime-status",
    [int]$CheckIntervalSeconds = 5,
    [int]$RestartCooldownSeconds = 10,
    [switch]$Once,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$LogDir = Join-Path $PSScriptRoot "logs"
$WatchdogLog = Join-Path $LogDir "smart-proxy-watchdog.log"
$ProxyOutLog = Join-Path $LogDir "smart-proxy.out.log"
$ProxyErrLog = Join-Path $LogDir "smart-proxy.err.log"

function Write-WatchdogLog {
    param([string]$Message)

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $line = "[{0}] {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $WatchdogLog -Value $line -Encoding UTF8
    Write-Host $line
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

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutMilliseconds = 1000
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMilliseconds)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Get-SmartProxyProcess {
    $resolvedProxyScript = ""
    if (Test-Path -LiteralPath $ProxyScript) {
        $resolvedProxyScript = (Resolve-Path -LiteralPath $ProxyScript).Path
    }

    Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and
        (
            ($resolvedProxyScript -and $_.CommandLine -like "*$resolvedProxyScript*") -or
            $_.CommandLine -like "*smart-proxy.py*"
        )
    }
}

function Get-RuntimeStatus {
    try {
        return Invoke-RestMethod -Uri $DashboardHealthUrl -TimeoutSec 2
    }
    catch {
        return $null
    }
}

function Test-UpstreamProxy {
    param($RuntimeStatus)

    $upstream = [string]($RuntimeStatus.upstream_proxy)
    if (-not $upstream -or $upstream -eq "None") {
        return [pscustomobject]@{
            ok = $true
            upstream = ""
            note = "no upstream proxy configured"
        }
    }

    $hostName, $portText = $upstream -split ":", 2
    $port = 0
    if (-not [int]::TryParse($portText, [ref]$port)) {
        return [pscustomobject]@{
            ok = $false
            upstream = $upstream
            note = "invalid upstream proxy"
        }
    }

    $ok = Test-TcpPort -HostName $hostName -Port $port -TimeoutMilliseconds 1000
    return [pscustomobject]@{
        ok = $ok
        upstream = $upstream
        note = if ($ok) { "upstream reachable" } else { "upstream unreachable" }
    }
}

function Test-SmartProxyHealth {
    $proxyReady = Test-TcpPort -HostName "127.0.0.1" -Port $ProxyPort -TimeoutMilliseconds 1000
    $dashboardReady = Test-TcpPort -HostName "127.0.0.1" -Port $DashboardPort -TimeoutMilliseconds 1000
    $runtime = if ($dashboardReady) { Get-RuntimeStatus } else { $null }
    $upstream = if ($runtime) {
        Test-UpstreamProxy -RuntimeStatus $runtime
    }
    else {
        [pscustomobject]@{
            ok = $false
            upstream = ""
            note = "runtime status unavailable"
        }
    }

    [pscustomobject]@{
        ok = ($proxyReady -and $dashboardReady -and [bool]$runtime)
        proxy_ready = $proxyReady
        dashboard_ready = $dashboardReady
        runtime_ready = [bool]$runtime
        upstream_ok = $upstream.ok
        upstream_proxy = $upstream.upstream
        upstream_note = $upstream.note
    }
}

function Restart-SmartProxy {
    $python = Resolve-PythonExe
    $script = (Resolve-Path -LiteralPath $ProxyScript).Path
    $processes = @(Get-SmartProxyProcess)

    foreach ($process in $processes) {
        Write-WatchdogLog "stopping smart-proxy PID $($process.ProcessId)"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Start-Sleep -Milliseconds 500
    Write-WatchdogLog "starting smart-proxy: $python $script"
    Start-Process `
        -WindowStyle Hidden `
        -FilePath $python `
        -WorkingDirectory $PSScriptRoot `
        -ArgumentList @($script) `
        -RedirectStandardOutput $ProxyOutLog `
        -RedirectStandardError $ProxyErrLog
}

function Wait-SmartProxyHealthy {
    param([int]$TimeoutSeconds = 15)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $health = Test-SmartProxyHealth
        if ($health.ok) {
            return $health
        }
        Start-Sleep -Milliseconds 300
    }

    return Test-SmartProxyHealth
}

if ($Status) {
    Test-SmartProxyHealth | ConvertTo-Json -Depth 6
    return
}

$lastRestartAt = [datetime]::MinValue
while ($true) {
    try {
        $health = Test-SmartProxyHealth
        if (-not $health.ok) {
            $elapsed = (Get-Date) - $lastRestartAt
            if ($elapsed.TotalSeconds -ge $RestartCooldownSeconds) {
                Write-WatchdogLog "unhealthy proxy=$($health.proxy_ready) dashboard=$($health.dashboard_ready) runtime=$($health.runtime_ready); restarting"
                Restart-SmartProxy
                $lastRestartAt = Get-Date
                $after = Wait-SmartProxyHealthy -TimeoutSeconds 15
                Write-WatchdogLog "post-restart ok=$($after.ok) upstream=$($after.upstream_proxy) upstream_ok=$($after.upstream_ok)"
            }
            else {
                Write-WatchdogLog "unhealthy but restart cooldown active"
            }
        }
        elseif (-not $health.upstream_ok) {
            Write-WatchdogLog "smart-proxy healthy but upstream warning: $($health.upstream_proxy) $($health.upstream_note)"
        }
    }
    catch {
        Write-WatchdogLog "watchdog error: $($_.Exception.Message)"
    }

    if ($Once) {
        break
    }
    Start-Sleep -Seconds $CheckIntervalSeconds
}
