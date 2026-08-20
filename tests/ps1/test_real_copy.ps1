$src = '\\192.168.0.47\团队文件-我的地盘\Private\JAV\2012-11\IPTD-999 【Rio（椛木ティナ）】彼女の従順NTR Rio\IPTD-999 彼女の従順NTR Rio-poster.jpg'
$target = '\\192.168.0.47\团队文件-我的地盘\Private\JAV\2012-11\IPTD-999 【Rio（椛木ティナ）】彼女の従順NTR Rio\poster.jpg'

Write-Host "src exists: $([System.IO.File]::Exists($src))"
Write-Host "target exists: $([System.IO.File]::Exists($target))"

# 模拟 Copy-Asset
try {
    [System.IO.File]::Copy($src, $target, $false)
    Write-Host "Copy OK"
} catch {
    Write-Host "ERROR type: $($_.Exception.GetType().FullName)"
    if ($_.Exception.InnerException) {
        Write-Host "Inner type: $($_.Exception.InnerException.GetType().FullName)"
        Write-Host "Inner msg: $($_.Exception.InnerException.Message)"
    }
    Write-Host "Full msg: $($_.Exception.Message)"
}