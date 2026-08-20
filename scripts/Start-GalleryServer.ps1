<#
.SYNOPSIS
    启动 / 停止 / 查询影片画廊 FastAPI 服务（uvicorn）。

.DESCRIPTION
    包装 ``uv run python -m javlibraryscrapy.cli.gallery``，提供子命令：
        Start   —— 后台启动（用 pythonw 避免控制台闪烁），并把 PID 写到 .pid 文件
        Stop    —— 按 PID 文件 / 端口查找 uvicorn 子进程并停止
        Status  —— 显示当前运行状态（PID、端口、URL、内存、最近日志）
        Restart —— 先 Stop 再 Start

    设计选择：
      - 不使用 NSSM / Windows Service，因为本服务是开发/家庭用途，单用户后台运行足够
      - 用 ``pythonw.exe`` 后台启动避免弹出控制台窗口
      - PID 持久化到 ``.gallery_server.pid``，便于 stop 定位
      - 日志输出到 ``output/.gallery_server.log``，与 ``.cover_cache`` 同级
      - 端口占用检测在 start 前做，避免端口冲突报冗长 traceback

.PARAMETER Action
    Start | Stop | Status | Restart。默认 Status。

.PARAMETER Port
    监听端口（默认 8000）。仅 Start/Restart 使用。

.PARAMETER LibraryRoot
    本地影片库根目录，覆盖 .env 中的 LIBRARY_ROOT。仅 Start/Restart 使用。
    不传则使用 .env 的值。

.PARAMETER OpenBrowser
    启动后自动打开浏览器（默认不打开）。仅 Start/Restart 使用。

.PARAMETER NoRescanOnStartup
    启动时不自动扫描本地库。仅 Start/Restart 使用。

.PARAMETER ImageProxy
    auto | on | off。仅 Start/Restart 使用。

.PARAMETER Force
    Stop 时：先发 SIGKILL（Stop-Process -Force），不用先 TryGraceful。仅 Stop 使用。

.EXAMPLE
    .\Start-GalleryServer.ps1 -Action Start

.EXAMPLE
    .\Start-GalleryServer.ps1 -Action Status

.EXAMPLE
    .\Start-GalleryServer.ps1 -Action Restart -Port 8080

.EXAMPLE
    .\Start-GalleryServer.ps1 -Action Stop -Force
#>

param(
    [Parameter(Mandatory=$false)]
    [ValidateSet('Start', 'Stop', 'Status', 'Restart')]
    [string]$Action = 'Status',

    [Parameter(Mandatory=$false)]
    [int]$Port = 8000,

    [Parameter(Mandatory=$false)]
    [string]$LibraryRoot,

    [Parameter(Mandatory=$false)]
    [switch]$OpenBrowser,

    [Parameter(Mandatory=$false)]
    [switch]$NoRescanOnStartup,

    [Parameter(Mandatory=$false)]
    [ValidateSet('auto', 'on', 'off')]
    [string]$ImageProxy = 'auto',

    [Parameter(Mandatory=$false)]
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# ---- 路径常量 ---------------------------------------------------------- #
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir
$OutputDir = Join-Path $ProjectRoot 'output'
$LogFile = Join-Path $OutputDir '.gallery_server.log'
$PidFile = Join-Path $OutputDir '.gallery_server.pid'

# 颜色辅助（与 scripts/Move-VideoFiles.ps1 风格一致）
function Write-Ok    { param($msg) Write-Host "✓ $msg" -ForegroundColor Green }
function Write-Info  { param($msg) Write-Host "ℹ $msg" -ForegroundColor Cyan }
function Write-Warn  { param($msg) Write-Host "⚠ $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "✗ $msg" -ForegroundColor Red }

# ---- 工具函数 ---------------------------------------------------------- #
function Test-PortListening {
    param([int]$Port)
    try {
        $conn = New-Object System.Net.Sockets.TcpClient
        $iar = $conn.BeginConnect('127.0.0.1', $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(500, $false)
        if ($ok -and $conn.Connected) {
            $conn.EndConnect($iar)
            $conn.Close()
            return $true
        }
        $conn.Close()
        return $false
    } catch {
        return $false
    }
}

function Get-ListeningPidsByPort {
    <#
        通过 netstat 查找监听指定端口的进程 PID。
        返回 @() 或 PID 列表（int）。
    #>
    param([int]$Port)
    $pids = @()
    $netstatLines = & netstat -ano -p TCP 2>$null
    foreach ($line in $netstatLines) {
        if ($line -match "TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)") {
            $pids += [int]$Matches[1]
        }
    }
    return ($pids | Select-Object -Unique)
}

function Get-StoredPid {
    if (Test-Path $PidFile) {
        $raw = Get-Content $PidFile -Raw -ErrorAction SilentlyContinue
        if ($raw -match '^\s*(\d+)\s*$') {
            return [int]$Matches[1]
        }
    }
    return $null
}

function Save-StoredPid {
    param([int]$ProcessId)
    $dir = Split-Path -Parent $PidFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Set-Content -Path $PidFile -Value $ProcessId -Encoding ASCII -NoNewline
}

function Remove-StoredPid {
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
}

function Test-PidAlive {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    return ($null -ne $p)
}

function Get-ProcessInfo {
    param([int]$ProcessId)
    $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $p) { return $null }
    return [pscustomobject]@{
        Pid = $p.Id
        StartTime = $p.StartTime
        WorkingSetMB = [math]::Round($p.WorkingSet64 / 1MB, 1)
        CPU = [math]::Round($p.TotalProcessorTime.TotalSeconds, 1)
        CommandLine = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)" -ErrorAction SilentlyContinue).CommandLine
    }
}

function Get-TailLines {
    param([string]$Path, [int]$Lines = 15)
    if (-not (Test-Path $Path)) { return @() }
    Get-Content $Path -Tail $Lines -ErrorAction SilentlyContinue
}

# ---- 子命令：Start ------------------------------------------------------ #
function Invoke-Start {
    # 已在跑？
    if (Test-PortListening -Port $Port) {
        $pids = Get-ListeningPidsByPort -Port $Port
        Write-Warn "端口 $Port 已被占用（PID: $($pids -join ', ')）。服务可能已在运行。"
        Write-Info "请用 -Action Status 查看，或先 -Action Stop。"
        return
    }

    # 确保 output/ 存在
    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    }

    # 组装参数
    $argList = @('-m', 'javlibraryscrapy.cli.gallery', '--port', "$Port", '--image-proxy', $ImageProxy)
    if ($OpenBrowser)    { $argList += '--open-browser' }
    if ($NoRescanOnStartup) { $argList += '--no-rescan-on-startup' }
    if ($LibraryRoot)    { $argList += @('--library-root', $LibraryRoot) }

    Write-Info "启动画廊服务（后台模式，端口 $Port）..."
    Write-Info "命令: uv run python $($argList -join ' ')"
    Write-Info "日志: $LogFile"

    # uv run 调起 python 进程；Start-Process 在新窗口里跑避免阻塞当前 shell。
    # 使用 -WindowStyle Hidden 让 PowerShell 窗口不闪烁。
    $uvExe = (Get-Command uv).Source
    $wrapper = Join-Path $env:TEMP "gallery_server_$Port.cmd"
    @"
@echo off
cd /d "$ProjectRoot"
"$uvExe" run python $($argList -join ' ') >> "$LogFile" 2>&1
"@ | Set-Content -Path $wrapper -Encoding ASCII

    # 用 cmd /c 启动 wrapper（Start-Process 支持 .cmd），并隐藏窗口
    $proc = Start-Process -FilePath 'cmd.exe' `
        -ArgumentList '/c', "`"$wrapper`"" `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -PassThru

    # 进程树：uv → python → uvicorn。Start-Process 拿到的是 cmd（短暂），不是 python。
    # 等待 python 启动并监听端口
    $deadline = (Get-Date).AddSeconds(15)
    $pythonPid = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
        $pids = Get-ListeningPidsByPort -Port $Port
        if ($pids.Count -gt 0) {
            $pythonPid = $pids[0]
            break
        }
    }

    if ($null -eq $pythonPid) {
        Write-Err "服务未在 $Port 上监听。请检查日志：$LogFile"
        Get-TailLines -Path $LogFile -Lines 30 | ForEach-Object { Write-Host "  $_" }
        return
    }

    Save-StoredPid -ProcessId $pythonPid
    Write-Ok "已启动：PID=$pythonPid，端口 $Port"
    Write-Info "本地访问：http://127.0.0.1:$Port"
    Write-Info "查看状态：.\Start-GalleryServer.ps1 -Action Status"
}

# ---- 子命令：Stop ------------------------------------------------------- #
function Invoke-Stop {
    $storedPid = Get-StoredPid
    $portPids = Get-ListeningPidsByPort -Port $Port
    $allPids = @($storedPid) + @($portPids) | Where-Object { $_ -ne $null } | Select-Object -Unique

    if ($allPids.Count -eq 0 -or ($allPids.Count -eq 1 -and $null -eq $allPids[0])) {
        Write-Info "未发现端口 $Port 上的运行实例。"
        Remove-StoredPid
        return
    }

    foreach ($p in $allPids) {
        if ($null -eq $p -or -not (Test-PidAlive -ProcessId $p)) {
            Write-Info "PID 文件残留或进程已退出（清理）。"
            Remove-StoredPid
            continue
        }
        try {
            if ($Force) {
                Stop-Process -Id $p -Force -ErrorAction Stop
                Write-Ok "已强制停止 PID $p。"
            } else {
                Stop-Process -Id $p -ErrorAction Stop
                Write-Ok "已停止 PID $p。"
            }
        } catch {
            Write-Err "停止 PID $p 失败：$_"
        }
    }

    # 等待端口释放
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-PortListening -Port $Port)) { break }
        Start-Sleep -Milliseconds 200
    }

    Remove-StoredPid

    if (Test-PortListening -Port $Port) {
        Write-Warn "端口 $Port 仍被占用，请检查是否有其他进程。"
    } else {
        Write-Ok "端口 $Port 已释放。"
    }
}

# ---- 子命令：Status ----------------------------------------------------- #
function Invoke-Status {
    $running = Test-PortListening -Port $Port
    $storedPid = Get-StoredPid
    $portPids = Get-ListeningPidsByPort -Port $Port

    Write-Host ""
    Write-Host "=== 影片画廊服务状态 ===" -ForegroundColor Cyan
    Write-Host "端口：   $Port"
    Write-Host "PID 文件：$PidFile"

    if (-not $running) {
        Write-Host "状态：   " -NoNewline
        Write-Host "未运行" -ForegroundColor Yellow
        if ($storedPid) {
            Write-Warn "PID 文件残留（PID $storedPid），可能进程已退出。"
        }
        Write-Host ""
        return
    }

    # 找到真正监听端口的进程（优先用 PID 文件与端口 PID 的并集）
    $candidatePids = @($storedPid) + @($portPids) | Where-Object { $_ -ne $null -and (Test-PidAlive -ProcessId $_) } | Select-Object -Unique
    $primaryPid = if ($candidatePids.Count -gt 0) { $candidatePids[0] } else { $portPids[0] }

    $info = Get-ProcessInfo -ProcessId $primaryPid
    Write-Host "状态：   " -NoNewline
    Write-Host "运行中" -ForegroundColor Green
    Write-Host "PID：    $primaryPid"
    if ($info) {
        Write-Host ("启动时间：{0:yyyy-MM-dd HH:mm:ss}" -f $info.StartTime)
        Write-Host ("内存：   {0} MB" -f $info.WorkingSetMB)
        Write-Host ("CPU 时间：{0}s" -f $info.CPU)
        if ($info.CommandLine) {
            $cl = $info.CommandLine
            if ($cl.Length -gt 120) { $cl = $cl.Substring(0, 117) + '...' }
            Write-Host "命令行： $cl"
        }
    }

    Write-Host "URL：    http://127.0.0.1:$Port"
    Write-Host "日志：   $LogFile"

    # 端到端探活
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/movies" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        $movieCount = ($r.Content | ConvertFrom-Json).movies.Count
        Write-Host ("端点探活：/api/movies → HTTP {0}（{1} 部影片）" -f $r.StatusCode, $movieCount) -ForegroundColor Green
    } catch {
        Write-Warn "端点探活失败：$($_.Exception.Message)"
    }

    # 最近日志
    Write-Host ""
    Write-Host "--- 最近日志（最后 10 行） ---" -ForegroundColor Gray
    Get-TailLines -Path $LogFile -Lines 10 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
    Write-Host ""
}

# ---- 路由 -------------------------------------------------------------- #
switch ($Action) {
    'Start'   { Invoke-Start }
    'Stop'    { Invoke-Stop }
    'Status'  { Invoke-Status }
    'Restart' {
        Invoke-Stop
        Start-Sleep -Seconds 1
        Invoke-Start
    }
}