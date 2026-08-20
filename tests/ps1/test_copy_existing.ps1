# 模拟第二次 sync 跑：folder 里 poster.jpg 已存在 → 应该走 exists 分支
# 但上次报错是因为落到 else 分支并 Copy-Asset 出错
# 这里我们直接测"假设脚本真的调了 Copy-Asset，目标已存在时它会做什么"

$src = '\\192.168.0.47\团队文件-我的地盘\Private\JAV\2012-11\IPTD-999 【Rio（椛木ティナ）】彼女の従順NTR Rio\IPTD-999 彼女の従順NTR Rio-poster.jpg'
$target = '\\192.168.0.47\团队文件-我的地盘\Private\JAV\2012-11\IPTD-999 【Rio（椛木ティナ）】彼女の従順NTR Rio\poster.jpg'

Write-Host "Target exists: $([System.IO.File]::Exists($target))"

# 这正是 Copy-Asset 里的代码
$Force = $false  # 不传 Force
try {
    [System.IO.File]::Copy($src, $target, [bool]$Force)
    Write-Host "Copy: OK (third parameter overwrite=$([bool]$Force))"
} catch {
    Write-Host "Copy ERROR: $($_.Exception.InnerException.Message)"
}