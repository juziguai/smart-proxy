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
$StartupLogDir = Join-Path $LogDir "startup"
$WatchdogLog = Join-Path $LogDir "smart-proxy-watchdog.log"

function Write-WatchdogLog {
    param([string]$Message)

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $line = "[{0}] {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $WatchdogLog -Value $line -Encoding UTF8
    Write-Host $line
}

function Initialize-LogDirs {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    New-Item -ItemType Directory -Force -Path $StartupLogDir | Out-Null
}

function Remove-StaleStartupCaptures {
    param([int]$RetentionDays = 7)

    Initialize-LogDirs
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    Get-ChildItem -LiteralPath $StartupLogDir -File -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Length -eq 0 -and $_.LastWriteTime -lt (Get-Date).AddMinutes(-5)) -or
            ($_.LastWriteTime -lt $cutoff)
        } |
        ForEach-Object {
            try {
                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop
            }
            catch {
                # Active child process may still hold the stdout/stderr handle.
            }
        }
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

function Test-LocalListeningPort {
    param([int]$Port)

    try {
        $listeners = @(
            Get-NetTCPConnection `
                -LocalAddress 127.0.0.1 `
                -LocalPort $Port `
                -State Listen `
                -ErrorAction SilentlyContinue
        )
        return $listeners.Count -gt 0
    }
    catch {
        return $false
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

function Format-ProcessSummary {
    param([array]$Processes)

    if (-not $Processes -or $Processes.Count -eq 0) {
        return "none"
    }

    return (($Processes | ForEach-Object { "$($_.ProcessId)" }) -join ",")
}

function Format-ProcessDetails {
    param([array]$Processes)

    if (-not $Processes -or $Processes.Count -eq 0) {
        return "none"
    }

    return (($Processes | ForEach-Object {
        "pid=$($_.ProcessId),ppid=$($_.ParentProcessId),created=$($_.CreationDate)"
    }) -join "; ")
}

function Get-LastStartedSmartProxyExitSummary {
    if (-not $script:LastStartedSmartProxy) {
        return "unavailable"
    }

    try {
        $script:LastStartedSmartProxy.Refresh()
        if ($script:LastStartedSmartProxy.HasExited) {
            return "pid=$($script:LastStartedSmartProxy.Id),exited=True,exit_code=$($script:LastStartedSmartProxy.ExitCode),exit_time=$($script:LastStartedSmartProxy.ExitTime.ToString("yyyy-MM-dd HH:mm:ss"))"
        }

        return "pid=$($script:LastStartedSmartProxy.Id),exited=False"
    }
    catch {
        return "unavailable,error=$($_.Exception.Message)"
    }
}

function Get-PortOwnerSummary {
    param([int[]]$Ports)

    $items = @()
    foreach ($port in $Ports) {
        try {
            $owners = @(
                Get-NetTCPConnection `
                    -LocalAddress 127.0.0.1 `
                    -LocalPort $port `
                    -State Listen `
                    -ErrorAction SilentlyContinue |
                    Select-Object -ExpandProperty OwningProcess -Unique
            )
        }
        catch {
            $owners = @()
        }

        $ownerText = if ($owners.Count -gt 0) {
            (($owners | ForEach-Object {
                $processName = "unknown"
                try {
                    $processName = (Get-Process -Id $_ -ErrorAction Stop).ProcessName
                }
                catch {
                    $processName = "exited"
                }
                "$_/$processName"
            }) -join ",")
        }
        else {
            "none"
        }
        $items += "$port=$ownerText"
    }

    return ($items -join " ")
}

function Get-TlsRelayProcess {
    $resolvedRelayScript = ""
    $relayScriptPath = Join-Path $PSScriptRoot "antigravity-tls-relay.py"
    if (Test-Path -LiteralPath $relayScriptPath) {
        $resolvedRelayScript = (Resolve-Path -LiteralPath $relayScriptPath).Path
    }

    Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and
        (
            ($resolvedRelayScript -and $_.CommandLine -like "*$resolvedRelayScript*") -or
            $_.CommandLine -like "*antigravity-tls-relay.py*"
        )
    }
}

function Get-OtherWatchdogProcesses {
    $resolvedWatchdogScript = $PSCommandPath
    if ($resolvedWatchdogScript -and (Test-Path -LiteralPath $resolvedWatchdogScript)) {
        $resolvedWatchdogScript = (Resolve-Path -LiteralPath $resolvedWatchdogScript).Path
    }

    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessId -ne $PID -and
        ($_.Name -eq 'powershell.exe' -or $_.Name -eq 'pwsh.exe') -and
        $_.CommandLine -like "*smart-proxy-watchdog.ps1*" -and
        $_.CommandLine -like "*-File*" -and
        $_.CommandLine -notlike "*-Status*" -and
        $_.CommandLine -notlike "*-Once*" -and
        $_.CommandLine -notlike "*-Command*" -and
        ((-not $resolvedWatchdogScript) -or $_.CommandLine -like "*$resolvedWatchdogScript*")
    }
}

function Exit-IfDuplicateWatchdog {
    $others = @(Get-OtherWatchdogProcesses)
    if ($others.Count -eq 0) {
        return
    }

    Write-WatchdogLog "duplicate watchdog detected; exiting current pid=$PID existing=[$(Format-ProcessDetails -Processes $others)]"
    exit 0
}

function Ensure-TlsRelayRunning {
    $processes = @(Get-TlsRelayProcess)
    $relayReady = Test-TcpPort -HostName "127.0.0.1" -Port 443 -TimeoutMilliseconds 1000

    if (-not $processes -or -not $relayReady) {
        Write-WatchdogLog "[Relay-Heal] TLS Relay (443) unhealthy or not running. Re-launching..."
        foreach ($process in $processes) {
            Write-WatchdogLog "[Relay-Heal] stopping zombie tls-relay PID $($process.ProcessId)"
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 200

        $python = Resolve-PythonExe
        $relayScript = Join-Path $PSScriptRoot "antigravity-tls-relay.py"
        if (Test-Path -LiteralPath $relayScript) {
            $resolvedPath = (Resolve-Path -LiteralPath $relayScript).Path
            $relayStamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
            $relayOutLog = Join-Path $StartupLogDir "relay-$relayStamp.out.log"
            $relayErrLog = Join-Path $StartupLogDir "relay-$relayStamp.err.log"

            Write-WatchdogLog "[Relay-Heal] starting tls-relay: $python $resolvedPath stdout=$relayOutLog stderr=$relayErrLog"
            Start-Process `
                -WindowStyle Hidden `
                -FilePath $python `
                -WorkingDirectory $PSScriptRoot `
                -ArgumentList @($resolvedPath) `
                -RedirectStandardOutput $relayOutLog `
                -RedirectStandardError $relayErrLog
        }
    }
}

function Get-RuntimeStatus {
    $started = Get-Date
    try {
        $body = Invoke-RestMethod -Uri $DashboardHealthUrl -TimeoutSec 5
        return [pscustomobject]@{
            ready = $true
            body = $body
            elapsed_ms = [int](((Get-Date) - $started).TotalMilliseconds)
            error = ""
        }
    }
    catch {
        return [pscustomobject]@{
            ready = $false
            body = $null
            elapsed_ms = [int](((Get-Date) - $started).TotalMilliseconds)
            error = $_.Exception.Message
        }
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
    $processes = @(Get-SmartProxyProcess)
    $proxyReady = Test-LocalListeningPort -Port $ProxyPort
    $dashboardReady = Test-LocalListeningPort -Port $DashboardPort
    $runtimeProbe = if ($dashboardReady) { Get-RuntimeStatus } else { $null }
    $runtime = if ($runtimeProbe -and $runtimeProbe.ready) { $runtimeProbe.body } else { $null }
    $upstream = if ($runtimeProbe -and $runtimeProbe.ready) {
        Test-UpstreamProxy -RuntimeStatus $runtimeProbe.body
    }
    else {
        [pscustomobject]@{
            ok = $false
            upstream = ""
            note = "runtime status unavailable"
        }
    }

    [pscustomobject]@{
        ok = ($proxyReady -and $dashboardReady)
        hard_down = (-not $proxyReady -or -not $dashboardReady)
        process_count = $processes.Count
        process_pids = Format-ProcessSummary -Processes $processes
        process_details = Format-ProcessDetails -Processes $processes
        proxy_ready = $proxyReady
        dashboard_ready = $dashboardReady
        runtime_ready = [bool]$runtime
        runtime_elapsed_ms = if ($runtimeProbe) { $runtimeProbe.elapsed_ms } else { 0 }
        runtime_error = if ($runtimeProbe) { $runtimeProbe.error } else { "dashboard port unavailable" }
        upstream_ok = $upstream.ok
        upstream_proxy = $upstream.upstream
        upstream_note = $upstream.note
    }
}

function Restart-SmartProxy {
    $python = Resolve-PythonExe
    $script = (Resolve-Path -LiteralPath $ProxyScript).Path
    $processes = @(Get-SmartProxyProcess)
    $beforePorts = Get-PortOwnerSummary -Ports @($ProxyPort, $DashboardPort)
    Write-WatchdogLog "restart snapshot before stop: processes=[$(Format-ProcessDetails -Processes $processes)] port_owners=[$beforePorts]"

    foreach ($process in $processes) {
        Write-WatchdogLog "stopping smart-proxy PID $($process.ProcessId)"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Start-Sleep -Milliseconds 500
    $remaining = @(Get-SmartProxyProcess)
    $afterStopPorts = Get-PortOwnerSummary -Ports @($ProxyPort, $DashboardPort)
    Write-WatchdogLog "restart snapshot after stop: processes=[$(Format-ProcessDetails -Processes $remaining)] port_owners=[$afterStopPorts]"

    Remove-StaleStartupCaptures
    $startStamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
    $startOutLog = Join-Path $StartupLogDir "smart-proxy-$startStamp.out.log"
    $startErrLog = Join-Path $StartupLogDir "smart-proxy-$startStamp.err.log"
    Write-WatchdogLog "starting smart-proxy: $python $script stdout=$startOutLog stderr=$startErrLog"
    $started = Start-Process `
        -WindowStyle Hidden `
        -FilePath $python `
        -WorkingDirectory $PSScriptRoot `
        -ArgumentList @($script) `
        -RedirectStandardOutput $startOutLog `
        -RedirectStandardError $startErrLog `
        -PassThru
    $script:LastStartedSmartProxy = $started
    Write-WatchdogLog "started smart-proxy PID $($started.Id)"
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
$consecutiveFailures = 0
$maxConsecutiveFailures = 3
$lastProxySeenAt = [datetime]::MinValue
$lastProxyHealthyAt = [datetime]::MinValue
$lastProxyPids = "none"
$lastProxyDetails = "none"
$lastProxyPortOwners = "none"
$lastDisappearanceKey = ""
$script:LastStartedSmartProxy = $null
$resolvedProxyScript = if (Test-Path -LiteralPath $ProxyScript) { (Resolve-Path -LiteralPath $ProxyScript).Path } else { $ProxyScript }
$resolvedPython = Resolve-PythonExe
Initialize-LogDirs
Remove-StaleStartupCaptures
Exit-IfDuplicateWatchdog
Write-WatchdogLog "watchdog starting pid=$PID script=$resolvedProxyScript python=$resolvedPython proxy_port=$ProxyPort dashboard_port=$DashboardPort health_url=$DashboardHealthUrl interval=${CheckIntervalSeconds}s restart_cooldown=${RestartCooldownSeconds}s"

while ($true) {
    try {
        Ensure-TlsRelayRunning
        $health = Test-SmartProxyHealth
        $portOwners = Get-PortOwnerSummary -Ports @($ProxyPort, $DashboardPort)
        if ($health.process_count -gt 0) {
            $lastProxySeenAt = Get-Date
            $lastProxyPids = $health.process_pids
            $lastProxyDetails = $health.process_details
            $lastProxyPortOwners = $portOwners
            $lastDisappearanceKey = ""
        }
        if ($health.ok) {
            $lastProxyHealthyAt = Get-Date
        }

        if ($health.hard_down) {
            $consecutiveFailures++
            Write-WatchdogLog "detecting hard-down status ($consecutiveFailures/$maxConsecutiveFailures): proxy=$($health.proxy_ready) dashboard=$($health.dashboard_ready) runtime=$($health.runtime_ready) runtime_ms=$($health.runtime_elapsed_ms) runtime_error='$($health.runtime_error)' pids=$($health.process_pids) processes=[$($health.process_details)] port_owners=[$portOwners]"

            if ($health.process_count -eq 0 -and $lastProxyPids -ne "none" -and $lastDisappearanceKey -ne $lastProxyPids) {
                $lastSeenText = if ($lastProxySeenAt -eq [datetime]::MinValue) { "unknown" } else { $lastProxySeenAt.ToString("yyyy-MM-dd HH:mm:ss") }
                $lastHealthyText = if ($lastProxyHealthyAt -eq [datetime]::MinValue) { "unknown" } else { $lastProxyHealthyAt.ToString("yyyy-MM-dd HH:mm:ss") }
                $exitSummary = Get-LastStartedSmartProxyExitSummary
                Write-WatchdogLog "process disappearance detected: current_pids=none last_seen_at=$lastSeenText last_healthy_at=$lastHealthyText last_pids=$lastProxyPids last_processes=[$lastProxyDetails] last_port_owners=[$lastProxyPortOwners] current_port_owners=[$portOwners] last_started_exit=[$exitSummary]"
                $lastDisappearanceKey = $lastProxyPids
            }

            if ($consecutiveFailures -ge $maxConsecutiveFailures) {
                $elapsed = (Get-Date) - $lastRestartAt
                if ($elapsed.TotalSeconds -ge $RestartCooldownSeconds) {
                    Write-WatchdogLog "unhealthy consecutive limit reached ($consecutiveFailures); restarting smart-proxy"
                    Restart-SmartProxy
                    $lastRestartAt = Get-Date
                    $consecutiveFailures = 0
                    $after = Wait-SmartProxyHealthy -TimeoutSeconds 15
                    $afterPorts = Get-PortOwnerSummary -Ports @($ProxyPort, $DashboardPort)
                    Write-WatchdogLog "post-restart ok=$($after.ok) proxy=$($after.proxy_ready) dashboard=$($after.dashboard_ready) runtime=$($after.runtime_ready) runtime_ms=$($after.runtime_elapsed_ms) runtime_upstream=$($after.upstream_proxy) upstream_ok=$($after.upstream_ok) pids=$($after.process_pids) port_owners=[$afterPorts]"
                }
                else {
                    Write-WatchdogLog "unhealthy but restart cooldown active elapsed=$([int]$elapsed.TotalSeconds)s required=${RestartCooldownSeconds}s"
                }
            }
        }
        else {
            $consecutiveFailures = 0
            if (-not $health.runtime_ready) {
                Write-WatchdogLog "smart-proxy ports healthy but runtime status unavailable; skip restart runtime_ms=$($health.runtime_elapsed_ms) runtime_error='$($health.runtime_error)' pids=$($health.process_pids)"
            }
            if (-not $health.upstream_ok) {
                Write-WatchdogLog "smart-proxy healthy but runtime upstream warning: $($health.upstream_proxy) $($health.upstream_note) runtime_ms=$($health.runtime_elapsed_ms)"
            }
        }
    }
    catch {
        Write-WatchdogLog "watchdog error: $($_.Exception.Message) stack=$($_.ScriptStackTrace)"
    }

    if ($Once) {
        break
    }
    Start-Sleep -Seconds $CheckIntervalSeconds
}
