# 关键诊断：在脚本进程内（无 bash 转义）跑 [System.IO.File]::Copy
$folder = '\\192.168.0.47\团队文件-我的地盘\Private\JAV\2012-07\IPTD-929 【佳苗るか】 スーパーアイドルナースのHな癒し看護 佳苗るか'
$src = Join-Path $folder 'IPTD-929 スーパーアイドルナースのHな癒し看護 佳苗るか-fanart.jpg'
$target = Join-Path $folder 'fanart.jpg'

Write-Host "Folder: $folder"
Write-Host "Source: $src"
Write-Host "Target: $target"
Write-Host "Source exists: $([System.IO.File]::Exists($src))"
Write-Host "Target exists: $([System.IO.File]::Exists($target))"

try {
    [System.IO.File]::Copy($src, $target, $false)
    Write-Host 'Copy: OK'
} catch {
    Write-Host "Copy ERROR: $($_.Exception.GetType().FullName)"
    Write-Host "Message: $($_.Exception.Message)"
    if ($_.Exception.InnerException) {
        Write-Host "Inner: $($_.Exception.InnerException.Message)"
    }
}