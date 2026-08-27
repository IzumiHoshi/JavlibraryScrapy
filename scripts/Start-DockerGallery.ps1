#requires -Version 5.1
<#
.SYNOPSIS
    画廊服务的一键 Docker 管理脚本（构建 / 启动 / 停止 / 状态 / 查看日志）。

.DESCRIPTION
    对应 Start-GalleryServer.ps1 的容器版本。流程：
      1. 检查 docker / docker compose CLI 可用；
      2. 检查 .env.docker 是否存在（首次跑会从 .env.docker.example 复制）；
      3. Build - 构建镜像（带 BuildKit 缓存，避免每次重下 chromium）；
      4. Up   - 后台启动容器（端口 8000）；
      5. Down - 优雅停容器（不掉卷）；
      6. Restart / Status / Logs 子命令。

    镜像名 / 容器名 / 端口都以 compose 文件为准，本脚本不另设默认。

.PARAMETER Action
    操作类型：Build / Up / Down / Restart / Status / Logs / Help

.EXAMPLE
    pwsh scripts/Start-DockerGallery.ps1 -Action Build
    pwsh scripts/Start-DockerGallery.ps1 -Action Up
    pwsh scripts/Start-DockerGallery.ps1 -Action Restart
    pwsh scripts/Start-DockerGallery.ps1 -Action Logs -Tail 100
#>

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Build', 'Up', 'Down', 'Restart', 'Status', 'Logs', 'Help')]
    [string]$Action,

    [int]$Tail = 50
)

$ErrorActionPreference = 'Stop'

# ---------- 路径 ----------
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$ComposeFile    = Join-Path $ProjectRoot 'docker-compose.yml'
$EnvDocker      = Join-Path $ProjectRoot '.env.docker'
$EnvExample     = Join-Path $ProjectRoot '.env.docker.example'
$Dockerfile     = Join-Path $ProjectRoot 'Dockerfile'
$ServiceName    = 'gallery'   # 与 docker-compose.yml 的 services.<name> 一致

# ---------- 配色 ----------
function Write-Step    { param($m) Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok      { param($m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn    { param($m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Err     { param($m) Write-Host "[ERR] $m" -ForegroundColor Red }

# ---------- 工具函数 ----------
function Test-Docker {
    try {
        $null = & docker version --format '{{.Server.Version}}' 2>&1
        if ($LASTEXITCODE -ne 0) { throw "docker CLI 不可用" }
    } catch {
        Write-Err "Docker Desktop / Engine 没起。请先启动 Docker Desktop 后再跑本脚本。"
        throw
    }
    try {
        $null = & docker compose version 2>&1
        if ($LASTEXITCODE -ne 0) { throw "compose v2 不可用" }
    } catch {
        Write-Err "docker compose v2 缺失。Docker Desktop 4.x+ 自带，老版请装 docker-compose-plugin。"
        throw
    }
}

function Initialize-EnvDocker {
    if (-not (Test-Path $EnvDocker)) {
        if (-not (Test-Path $EnvExample)) {
            Write-Err ".env.docker 和 .env.docker.example 都不存在，文件结构不完整。"
            throw "missing .env.docker.example"
        }
        Write-Warn ".env.docker 不存在，从 .env.docker.example 复制初始模板（请按需修改后再启动）。"
        Copy-Item -Path $EnvExample -Destination $EnvDocker
        Write-Ok "已生成 .env.docker，请编辑挂载源路径（LIBRARY_HOST_PATH 等）后再 Up。"
        if ($Action -in @('Up', 'Restart')) {
            throw ".env.docker 刚生成，请检查路径后重跑。"
        }
    }
}

function Invoke-Compose {
    param([string[]]$Args)
    & docker compose -f $ComposeFile @Args
    if ($LASTEXITCODE -ne 0) {
        Write-Err "docker compose $Args 失败（exit=$LASTEXITCODE）"
        throw
    }
}

# ---------- 各 Action ----------
switch ($Action) {
    'Help' {
        Get-Help $MyInvocation.MyCommand.Path -Detailed | Out-String | Write-Host
        break
    }

    'Build' {
        Test-Docker
        Write-Step "构建镜像（首次约 5-10 分钟，主要在装 chromium）..."
        # DOCKER_BUILDKIT=1 启用 BuildKit；缓存到项目下 .docker-cache，避免反复下载 chromium
        $env:DOCKER_BUILDKIT = '1'
        Invoke-Compose @('build', '--pull=false')
        Write-Ok "镜像构建完成。"
        break
    }

    'Up' {
        Test-Docker
        Initialize-EnvDocker
        Write-Step "后台启动容器..."
        Invoke-Compose @('up', '-d')
        Write-Ok "启动成功，端口转发 8000 → 容器 8000。"
        Write-Host "    浏览器打开 http://localhost:8000  （或 http://<本机内网IP>:8000）"
        Write-Host "    日志：pwsh scripts/Start-DockerGallery.ps1 -Action Logs"
        break
    }

    'Down' {
        Test-Docker
        Write-Step "停掉容器（数据卷保留）..."
        Invoke-Compose @('down')
        Write-Ok "已停止。"
        break
    }

    'Restart' {
        Test-Docker
        Initialize-EnvDocker
        Write-Step "重启容器..."
        Invoke-Compose @('restart')
        Write-Ok "已重启。"
        break
    }

    'Status' {
        Test-Docker
        Write-Step "容器状态："
        & docker compose -f $ComposeFile ps
        Write-Host ""
        Write-Step "镜像："
        & docker images --filter "reference=javlibraryscrapy*" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}"
        Write-Host ""
        Write-Step "端口探测："
        try {
            $r = Invoke-WebRequest -Uri 'http://localhost:8000/api/health' -TimeoutSec 3 -UseBasicParsing
            Write-Ok "服务响应 $($r.StatusCode)"
        } catch {
            Write-Warn "http://localhost:8000 未就绪 —— $_"
        }
        break
    }

    'Logs' {
        Test-Docker
        Write-Step "容器日志（Ctrl+C 退出）："
        & docker compose -f $ComposeFile logs --tail=$Tail --follow $ServiceName
        break
    }
}