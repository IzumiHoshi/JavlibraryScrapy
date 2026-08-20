# 同步跑主循环的一个 iteration，模拟已存在的情况
$ErrorActionPreference = 'Stop'

# 读取索引
$json = [System.IO.File]::ReadAllText("D:\Code\JavlibraryScrapy\output\library_index.json", [System.Text.Encoding]::UTF8)
$index = $json | ConvertFrom-Json

# 找一个有 error 记录的文件夹（从前一次跑的结果）：看 IPTD-999
$entry = $index.movies.'IPTD-999'
Write-Host "Entry folder: $($entry.folder)"
Write-Host "Entry has_nfo=$($entry.has_nfo) has_poster=$($entry.has_poster) has_fanart=$($entry.has_fanart)"
Write-Host ""

$folderPath = $entry.folder
$carid = $entry.carid
$Force = $false  # 默认

# 模拟 Get-FolderAssets（手动列出 IPTD-999 文件）
Write-Host "Files in folder:"
Get-ChildItem -LiteralPath $folderPath -File | Select-Object Name | Format-Table -HideTableHeaders

# 模拟资产判断
$bestPoster = Get-ChildItem -LiteralPath $folderPath -File | Where-Object { $_.BaseName -match '-poster$' -and $_.Extension -match '^\.(jpg|jpeg|png)$' } | Select-Object -First 1
Write-Host "Poster source: $($bestPoster.FullName)"

$target = Join-Path $folderPath 'poster.jpg'
Write-Host "Target: $target"
Write-Host "Target exists: $([System.IO.File]::Exists($target))"

# 关键判断
$exists = [System.IO.File]::Equals($bestPoster.FullName, $target)
Write-Host "Equals(src, target): $exists"
Write-Host "-not `$exists: $(-not $exists)"
Write-Host "`$Force is: $Force"
Write-Host "-not `$Force: $(-not $Force)"

if (-not $exists -and [System.IO.File]::Exists($target) -and -not $Force) {
    Write-Host "Branch: exists"
} else {
    Write-Host "Branch: copy"
}