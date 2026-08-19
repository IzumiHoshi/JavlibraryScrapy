# 测试 IPTD-929 复制（模拟脚本逻辑）
$folder = '\\192.168.0.47\团队文件-我的地盘\Private\JAV\2012-07\IPTD-929 【佳苗るか】 スーパーアイドルナースのHな癒し看護 佳苗るか'
$src = Join-Path $folder 'IPTD-929 スーパーアイドルナースのHな癒し看護 佳苗るか-poster.jpg'
$target = Join-Path $folder 'poster.jpg'

Write-Host "Source exists: $([System.IO.File]::Exists($src))"
Write-Host "Target exists: $([System.IO.File]::Exists($target))"

# 这是脚本的判断逻辑
$exists = [System.IO.File]::Equals($src, $target)
Write-Host "Equals(src, target): $exists"

if (-not $exists -and [System.IO.File]::Exists($target) -and -not $Force) {
    Write-Host "Path: exists branch"
} else {
    Write-Host "Path: copy branch"
    try {
        [System.IO.File]::Copy($src, $target, [bool]$Force)
        Write-Host "Copy: OK"
    } catch {
        Write-Host "Copy: ERROR - $($_.Exception.Message)"
    }
}