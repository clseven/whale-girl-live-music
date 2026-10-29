param(
    [ValidateSet('Start', 'Restart', 'Stop', 'Status')][string]$Action = 'Start',
    [switch]$NoOpen,
    [ValidateRange(5, 180)][int]$StartupTimeoutSeconds = 90
)

$ErrorActionPreference = 'Stop'
$studioRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$studioUrl = 'http://127.0.0.1:18763/'
$runtimeDir = Join-Path $studioRoot '.runtime'
$pidFile = Join-Path $runtimeDir 'server.pid'
$pythonPath = Join-Path $studioRoot '.venv\Scripts\python.exe'
$serverPath = Join-Path $studioRoot 'server.py'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

function Request-Studio([string]$Path, [switch]$Post) {
    $request = [Net.HttpWebRequest]::Create($studioUrl + $Path)
    $request.Proxy = $null
    $request.Timeout = 2500
    $request.ReadWriteTimeout = 2500
    if ($Post) {
        $request.Method = 'POST'
        $request.ContentType = 'application/json'
        $bytes = [Text.Encoding]::UTF8.GetBytes('{}')
        $request.ContentLength = $bytes.Length
        $stream = $request.GetRequestStream()
        try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
    }
    $response = $request.GetResponse()
    try {
        $reader = New-Object IO.StreamReader($response.GetResponseStream())
        try { return ($reader.ReadToEnd() | ConvertFrom-Json) }
        finally { $reader.Dispose() }
    } finally { $response.Dispose() }
}

function Get-StudioStatus {
    try {
        $status = Request-Studio 'api/status'
        if ($null -ne $status.PSObject.Properties['voice'] -and
            $null -ne $status.PSObject.Properties['provider']) { return $status }
    } catch { }
    return $null
}

function Get-Listener {
    $owners = @(Get-NetTCPConnection -LocalPort 18763 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -gt 1) { throw '18763 端口有多个所属进程，未结束任何进程。' }
    if ($owners.Count -eq 1) { return (Get-CimInstance Win32_Process -Filter "ProcessId = $($owners[0])") }
    return $null
}

function Get-RecordedProcess {
    if (-not (Test-Path -LiteralPath $pidFile)) { return $null }
    $number = 0
    if (-not [int]::TryParse(([IO.File]::ReadAllText($pidFile)).Trim(), [ref]$number) -or $number -le 0) { return $null }
    return (Get-CimInstance Win32_Process -Filter "ProcessId = $number")
}

function Test-ProjectProcess($Process, $Status) {
    if (-not $Process -or $Process.Name -notin @('python.exe', 'pythonw.exe')) { return $false }
    # All new starts use the absolute server path. Never terminate all Python apps.
    if ($Process.CommandLine -and $Process.CommandLine.IndexOf($serverPath, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    # Verified HTTP identity plus process identity, for a running new backend.
    if ($Status -and $Status.service -and $Status.service.pid -eq $Process.ProcessId -and
        [string]::Equals($Status.service.root, $studioRoot, [StringComparison]::OrdinalIgnoreCase) -and
        $Process.CommandLine -match '(?i)\bserver\.py(?:["\s]|$)') { return $true }
    # Compatibility with the previous venv launcher: its Windows Python stub is
    # the recorded PID while the actual listening Python process is its child.
    $recorded = Get-RecordedProcess
    if ($recorded -and [string]::Equals($recorded.ExecutablePath, $pythonPath, [StringComparison]::OrdinalIgnoreCase) -and
        ($Process.ProcessId -eq $recorded.ProcessId -or $Process.ParentProcessId -eq $recorded.ProcessId) -and
        $Process.CommandLine -match '(?i)\bserver\.py(?:["\s]|$)') { return $true }
    return $false
}

function Wait-ProjectExit($Process, [int]$Seconds) {
    $until = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $until) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($Process.ProcessId)"
        if (-not $current -or $current.CreationDate -ne $Process.CreationDate) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Stop-Studio {
    $status = Get-StudioStatus
    $owner = Get-Listener
    if (-not $owner) {
        $owner = Get-RecordedProcess
        if (-not $owner) { return }
    }
    if (-not (Test-ProjectProcess $owner $status)) { throw '无法确认18763端口所属本项目，未结束进程。请查看任务管理器命令行。' }
    $graceful = $false
    if ($status -and $status.features -and $status.features.lifecycle) {
        Request-Studio 'api/studio/lifecycle/stop' -Post | Out-Null
        $graceful = Wait-ProjectExit $owner 20
    }
    if (-not $graceful) {
        # Old versions have no shutdown endpoint. Recheck identity and PID reuse.
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($owner.ProcessId)"
        if ($current -and $current.CreationDate -eq $owner.CreationDate -and (Test-ProjectProcess $current $status)) {
            $codexChildren = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($owner.ProcessId) AND Name = 'codex.exe'")
            Stop-Process -Id $owner.ProcessId -ErrorAction Stop
            foreach ($child in $codexChildren) {
                $now = Get-CimInstance Win32_Process -Filter "ProcessId = $($child.ProcessId)"
                if ($now -and $now.CreationDate -eq $child.CreationDate) { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
            }
        }
        if (-not (Wait-ProjectExit $owner 5)) { throw '项目进程尚未退出，未启动第二份后台。' }
    }
    # Allow the Windows venv launcher stub to finish after its child exits.
    $until = [DateTime]::UtcNow.AddSeconds(5)
    while ((Get-Listener) -and [DateTime]::UtcNow -lt $until) { Start-Sleep -Milliseconds 200 }
    if (Get-Listener) { throw '18763端口仍被占用，未启动第二份后台。' }
    if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile }
}

function Start-Studio {
    if (Get-StudioStatus) { return }
    if (Get-Listener) { throw '18763端口被占用但后台不可用，未重复启动；请先使用重启。' }
    $pending = Get-RecordedProcess
    if ($pending -and (Test-ProjectProcess $pending $null)) {
        # A prior launcher may still be warming up. Do not create a second copy.
        $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
        while (-not (Get-StudioStatus)) {
            if (-not (Get-CimInstance Win32_Process -Filter "ProcessId = $($pending.ProcessId)")) { break }
            if ([DateTime]::UtcNow -ge $deadline) { throw '此前后台仍在启动或卡住，请运行重启，未重复启动。' }
            Start-Sleep -Milliseconds 400
        }
        if (Get-StudioStatus) { return }
    }
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw '找不到项目 .venv\Scripts\python.exe。' }
    $backend = Start-Process -FilePath $pythonPath -ArgumentList '-u', ('"' + $serverPath + '"') `
        -WorkingDirectory $studioRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir 'server.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeDir 'server.stderr.log')
    Set-Content -LiteralPath $pidFile -Value $backend.Id -Encoding ASCII
    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    while (-not (Get-StudioStatus)) {
        $backend.Refresh()
        if ($backend.HasExited) { throw '后台启动后退出，请查看 .runtime\server.stderr.log。' }
        if ([DateTime]::UtcNow -ge $deadline) { throw '后台启动超时，请查看 .runtime\server.stderr.log。' }
        Start-Sleep -Milliseconds 400
    }
}

$mutex = New-Object Threading.Mutex($false, 'Local\WhaleStudioLauncher18763')
$owns = $false
try {
    try { $owns = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owns = $true }
    if (-not $owns) { Write-Output '另一个启动/重启操作正在进行，请等待完成。'; exit 0 }
    if ($Action -eq 'Status') {
        $status = Get-StudioStatus
        [pscustomobject]@{ running = [bool]$status; url = $studioUrl; pid = $status.service.pid; watch = $status.features.watch } | ConvertTo-Json -Compress
    } elseif ($Action -eq 'Stop') {
        Stop-Studio
        Write-Output '大肥鱼后台已停止。聊天、统计和配置保留。'
    } else {
        if ($Action -eq 'Restart') { Stop-Studio }
        Start-Studio
        if (-not $NoOpen) {
            $chromePaths = @(
                "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
                "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
                "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
            )
            $chrome = $chromePaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            if (-not $chrome) { throw '后台已运行，但未找到Chrome，请手动打开 http://127.0.0.1:18763/。' }
            Start-Process -FilePath $chrome -ArgumentList $studioUrl
        }
        Write-Output ('大肥鱼后台已就绪：' + $studioUrl)
    }
} catch {
    $message = $_.Exception.Message
    Add-Content -LiteralPath (Join-Path $runtimeDir 'launcher.log') -Value ("$(Get-Date -Format s) $Action $message") -Encoding UTF8
    if (-not $NoOpen) { (New-Object -ComObject WScript.Shell).Popup($message, 0, '大肥鱼项目管理', 16) | Out-Null }
    Write-Error $message
    exit 1
} finally {
    if ($owns) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
