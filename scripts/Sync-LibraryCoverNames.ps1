<#
.SYNOPSIS
    把本地影片库里的 NFO / 海报 / Fanart 复制成 library_scanner 能识别的标准名。

.DESCRIPTION
    原 ``library_scanner.scan_movie_folder`` 只识别：
        - poster.{jpg,png,jpeg}  / folder.{jpg,png}     / cover.{jpg,png,jpeg}
        - fanart.{jpg,png,jpeg}
        - movie.nfo  /  <carid>.nfo

    但本机实际命名约定是 ``<carid> <title>-poster.jpg``、``...-fanart.jpg``、
    ``<carid> <title>.nfo``（含车牌与标题），导致库全部判为无 NFO/无海报。

    本脚本不动原文件，把每部影片对应的 NFO / poster / fanart 复制一份成标准名。
    优化：
        - 一次 Get-ChildItem 拿全文件夹下所有相关文件，避免重复 IO
        - 跳过 Get-ChildItem 用 [System.IO.File]::Exists 测单个文件
        - 批量处理（避免大量 PSObject 实例化）

.PARAMETER LibraryRoot
    本地影片库根目录（默认 ``Z:\JAV``）。脚本实际从索引读取每个文件夹路径，不直接 walk。

.PARAMETER IndexPath
    已扫描索引文件路径（默认 ``output\library_index.json``）。

.PARAMETER DryRun
    只打印计划，不真正复制。

.PARAMETER Force
    当目标已存在时覆盖。默认跳过。

.EXAMPLE
    .\Sync-LibraryCoverNames.ps1 -DryRun

.EXAMPLE
    .\Sync-LibraryCoverNames.ps1

.EXAMPLE
    .\Sync-LibraryCoverNames.ps1 -Force
#>

param(
    [Parameter(Mandatory=$false)]
    [string]$LibraryRoot = 'Z:\JAV',

    [Parameter(Mandatory=$false)]
    [string]$IndexPath = (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) '..\output\library_index.json'),

    [Parameter(Mandatory=$false)]
    [switch]$DryRun,

    [Parameter(Mandatory=$false)]
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# ---- 标准名表（与 scripts\library_scanner.py 的 COVER_NAMES / FANART_NAMES 对齐） ----
$StandardPoster = @('poster.jpg', 'poster.png', 'poster.jpeg',
                    'folder.jpg', 'folder.png',
                    'cover.jpg', 'cover.png', 'cover.jpeg')
$StandardFanart = @('fanart.jpg', 'fanart.png', 'fanart.jpeg')

function Write-Info { param($msg) Write-Host "ℹ $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "✓ $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "⚠ $msg" -ForegroundColor Yellow }
function Write-Err  { param($msg) Write-Host "✗ $msg" -ForegroundColor Red }

# ---- 一次性收集文件夹里"看起来像 NFO/poster/fanart"的文件，避免重复 IO ----
function Get-FolderAssets {
    <#
        返回：
            nfo      = 第一个 .nfo（优先 ``<carid>.nfo``，否则任意 .nfo）
            nfoFound = bool
            poster   = 第一个 poster/fanart 同类文件（按优先级排序）
            posterFound = bool
            fanart   = 同上
            fanartFound = bool

        来源优先级（每类）：
            1. 标准名（如 poster.jpg）→ 用之
            2. ``<carid> <title>-poster.jpg`` 等带后缀的（你的命名约定）
            3. 任意含关键字的（如 myposter.jpg → 不采用，可能误判，跳过）
        返回值中 _Found=False 表示没找到任何候选；poster._File 可能是 $null
    #>
    param(
        [Parameter(Mandatory)] $Folder,
        [Parameter(Mandatory)] [string]$Carid
    )

    $result = @{
        nfo      = $null; nfoFound = $false
        poster   = $null; posterFound = $false
        fanart   = $null; fanartFound = $false
    }

    if (-not (Test-Path -LiteralPath $Folder -PathType Container)) {
        return $result
    }

    # 单次 Get-ChildItem
    $files = @()
    try {
        $files = Get-ChildItem -LiteralPath $Folder -File -ErrorAction Stop
    } catch {
        return $result
    }

    # ---- 纯 .NET 路径：兼容 UNC + 加速 ----
    # 1. 找 NFO
    $nfoCandidates = @()
    foreach ($f in $files) {
        if ($f.Extension -eq '.nfo') { $nfoCandidates += $f }
    }
    if ($nfoCandidates.Count -gt 0) {
        # 优先 ``<carid>.nfo``（精确匹配）
        $caridNfo = $nfoCandidates | Where-Object { $_.BaseName -eq $Carid } | Select-Object -First 1
        if ($caridNfo) {
            $result.nfo = $caridNfo.FullName
        } else {
            # 否则用 ``<carid> <title>.nfo``（BaseName 以 <carid> 开头）
            $prefixed = $nfoCandidates | Where-Object { $_.BaseName.StartsWith($Carid) } | Select-Object -First 1
            if ($prefixed) {
                $result.nfo = $prefixed.FullName
            } else {
                $result.nfo = $nfoCandidates[0].FullName
            }
        }
        $result.nfoFound = $true
    }

    # 2. 找 poster —— 优先级：标准名 → ``-poster`` 结尾 → 含 poster 关键字
    foreach ($name in $StandardPoster) {
        $p = Join-Path $Folder $name
        if ([System.IO.File]::Exists($p)) {
            $result.poster = $p
            $result.posterFound = $true
            break
        }
    }
    if (-not $result.posterFound) {
        # 任何 -poster.* 结尾的（你的命名约定）
        $best = $files | Where-Object { $_.BaseName -match '-poster$' -and $_.Extension -match '^\.(jpg|jpeg|png)$' } | Select-Object -First 1
        if ($best) {
            $result.poster = $best.FullName
            $result.posterFound = $true
        }
    }

    # 3. 找 fanart
    foreach ($name in $StandardFanart) {
        $p = Join-Path $Folder $name
        if ([System.IO.File]::Exists($p)) {
            $result.fanart = $p
            $result.fanartFound = $true
            break
        }
    }
    if (-not $result.fanartFound) {
        $best = $files | Where-Object { $_.BaseName -match '-fanart$' -and $_.Extension -match '^\.(jpg|jpeg|png)$' } | Select-Object -First 1
        if ($best) {
            $result.fanart = $best.FullName
            $result.fanartFound = $true
        }
    }

    return $result
}

function Copy-Asset {
    param(
        [Parameter(Mandatory)] $Source,
        [Parameter(Mandatory)] $Target
    )

    if ($DryRun) {
        return @{ Action = 'dry-run'; Source = $Source; Target = $Target }
    }
    try {
        [System.IO.File]::Copy($Source, $Target, [bool]$Force)
        return @{ Action = 'copied'; Source = $Source; Target = $Target }
    } catch {
        return @{ Action = 'error'; Source = $Source; Target = $Target; Reason = $_.Exception.Message }
    }
}

# ---- 主流程 ----
if (-not (Test-Path -LiteralPath $IndexPath)) {
    Write-Err "索引文件不存在：$IndexPath"
    exit 1
}

Write-Info "读取索引：$IndexPath"
$index = $null
try {
    $json = [System.IO.File]::ReadAllText($IndexPath, [System.Text.Encoding]::UTF8)
    $index = $json | ConvertFrom-Json
} catch {
    Write-Err "解析索引失败：$($_.Exception.Message)"
    exit 1
}

if (-not $index.movies) {
    Write-Err "索引中无 movies 字段。"
    exit 1
}

$movieCount = $index.movies.PSObject.Properties.Count
Write-Info ("索引 {0} 部影片。" -f $movieCount)
if ($DryRun) {
    $script:DryRunPrinted = 0
    Write-Warn "Dry-run 模式：只显示计划，不真正复制。"
}

$stats = @{
    folders = 0
    nfo_copied = 0; nfo_exists = 0; nfo_skipped = 0; nfo_error = 0
    poster_copied = 0; poster_exists = 0; poster_skipped = 0; poster_error = 0
    fanart_copied = 0; fanart_exists = 0; fanart_skipped = 0; fanart_error = 0
}

$folders = $index.movies.PSObject.Properties | ForEach-Object { $_.Value }
foreach ($entry in $folders) {
    $stats.folders++
    $folderPath = $entry.folder
    if (-not $folderPath) { continue }

    $assets = Get-FolderAssets -Folder $folderPath -Carid $entry.carid

    # ---- NFO ----
    $nfoAction = 'skipped'
    if (-not $assets.nfoFound) {
        $stats.nfo_skipped++
    } else {
        $movieNfo = Join-Path $folderPath 'movie.nfo'
        $caridNfo = Join-Path $folderPath ($entry.carid + '.nfo')

        # 优先 carid.nfo（扫描器第二优先级）
        $target = $caridNfo
        $exists = [System.IO.File]::Exists($target)
        if ($exists -and -not $Force) {
            $target = $movieNfo
            $exists = [System.IO.File]::Exists($target)
        }
        if ($exists -and -not $Force) {
            $nfoAction = 'exists'
            $stats.nfo_exists++
        } else {
            $r = Copy-Asset -Source $assets.nfo -Target $target
            $nfoAction = $r.Action
            switch ($r.Action) {
                'copied' { $stats.nfo_copied++ }
                'error'  { $stats.nfo_error++ }
            }
        }
    }

    # ---- poster ----
    $posterAction = 'skipped'
    if (-not $assets.posterFound) {
        $stats.poster_skipped++
    } else {
        $target = Join-Path $folderPath 'poster.jpg'
        $exists = [System.IO.File]::Equals($assets.poster, $target)  # 源等于目标
        if (-not $exists -and [System.IO.File]::Exists($target) -and -not $Force) {
            $posterAction = 'exists'
            $stats.poster_exists++
        } else {
            $r = Copy-Asset -Source $assets.poster -Target $target
            $posterAction = $r.Action
            switch ($r.Action) {
                'copied' { $stats.poster_copied++ }
                'error'  { $stats.poster_error++ }
            }
        }
    }

    # ---- fanart ----
    $fanartAction = 'skipped'
    if (-not $assets.fanartFound) {
        $stats.fanart_skipped++
    } else {
        $target = Join-Path $folderPath 'fanart.jpg'
        $exists = [System.IO.File]::Equals($assets.fanart, $target)
        if (-not $exists -and [System.IO.File]::Exists($target) -and -not $Force) {
            $fanartAction = 'exists'
            $stats.fanart_exists++
        } else {
            $r = Copy-Asset -Source $assets.fanart -Target $target
            $fanartAction = $r.Action
            switch ($r.Action) {
                'copied' { $stats.fanart_copied++ }
                'error'  { $stats.fanart_error++ }
            }
        }
    }

    # 仅在 Dry-run 或错误时打印
    $hadError = ($nfoAction -eq 'error') -or ($posterAction -eq 'error') -or ($fanartAction -eq 'error')
    if ($DryRun -and ($script:DryRunPrinted -lt 5) -and ($nfoAction -ne 'skipped' -or $posterAction -ne 'skipped' -or $fanartAction -ne 'skipped')) {
        $script:DryRunPrinted++
        $summary = "nfo=$nfoAction poster=$posterAction fanart=$fanartAction"
        Write-Host ("[{0}] {1}  -- {2}" -f $entry.carid, $summary, $folderPath)
    } elseif ($hadError) {
        Write-Warn "[$($entry.carid)] nfo=$nfoAction poster=$posterAction fanart=$fanartAction -- $folderPath"
    }
}

Write-Host ""
Write-Host "=== 汇总 ===" -ForegroundColor Cyan
Write-Host ("扫描：   {0} 个文件夹" -f $stats.folders)
Write-Host ("NFO:    copied={0}  exists={1}  skipped={2}  error={3}" -f $stats.nfo_copied, $stats.nfo_exists, $stats.nfo_skipped, $stats.nfo_error)
Write-Host ("海报:   copied={0}  exists={1}  skipped={2}  error={3}" -f $stats.poster_copied, $stats.poster_exists, $stats.poster_skipped, $stats.poster_error)
Write-Host ("Fanart: copied={0}  exists={1}  skipped={2}  error={3}" -f $stats.fanart_copied, $stats.fanart_exists, $stats.fanart_skipped, $stats.fanart_error)

if ($DryRun) {
    Write-Warn "Dry-run 模式：未真正复制。请去除 -DryRun 重新运行以应用更改。"
} else {
    Write-Info "重扫库索引（重启画廊服务或调 POST /api/library/rescan）后即可看到 has_nfo/has_poster/has_fanart 全部为 true。"
}