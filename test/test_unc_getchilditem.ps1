$folder = '\\192.168.0.47\团队文件-我的地盘\Private\JAV\2023-07\ABF-007 【涼森れむ】涼森れむの、尻。【MGSだけのおまけ映像付き+10分】'
Write-Host "Test folder: $folder"
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$files = Get-ChildItem -LiteralPath $folder -File
$sw.Stop()
Write-Host "Get-ChildItem took $($sw.ElapsedMilliseconds) ms, count=$($files.Count)"
$files | Select-Object Name | Format-Table -HideTableHeaders