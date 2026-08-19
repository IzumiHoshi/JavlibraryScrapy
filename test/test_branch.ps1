# 模拟 Sync-LibraryCoverNames.ps1 主循环里 poster/fanart 的判断逻辑
$folder = '\\192.168.0.47\团队文件-我的地盘\Private\JAV\2012-07\IPTD-929 【佳苗るか】 スーパーアイドルナースのHな癒し看護 佳苗るか'

# Get-FolderAssets 模拟：poster 已经存在（被前一次同步创建的）
$assets = @{
    poster = Join-Path $folder 'IPTD-929 スーパーアイドルナースのHな癒し看護 佳苗るか-poster.jpg'
    posterFound = $true
}

# 完全照抄主循环逻辑
$target = Join-Path $folder 'poster.jpg'
$exists = [System.IO.File]::Equals($assets.poster, $target)
Write-Host "Equals(src, target): $exists"

# PowerShell 默认 $Force 是 $false（switch 参数）
$Force = $false  # 模拟脚本里的 $Force 默认值

if (-not $exists -and [System.IO.File]::Exists($target) -and -not $Force) {
    Write-Host "Branch: exists"
} else {
    Write-Host "Branch: copy"
    try {
        [System.IO.File]::Copy($assets.poster, $target, [bool]$Force)
        Write-Host "Copy OK"
    } catch {
        Write-Host "Copy ERROR: $($_.Exception.InnerException.Message)"
    }
}