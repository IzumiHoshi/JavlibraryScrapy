# IPTD-999 检查实际状态
$base = '\\192.168.0.47\团队文件-我的地盘\Private\JAV\2012-11'
$targets = Get-ChildItem -LiteralPath $base -Directory -Filter 'IPTD-999*'
foreach ($t in $targets) {
    Write-Host "=== $($t.FullName) ==="
    Get-ChildItem -LiteralPath $t.FullName -File | Select-Object Name, Length | Format-Table -HideTableHeaders -AutoSize
}