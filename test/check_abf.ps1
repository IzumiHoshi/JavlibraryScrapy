$folder = '\\192.168.0.47\团队文件-我的地盘\Private\JAV\2023-07'
Write-Host "Listing: $folder"
Get-ChildItem -Path $folder -Directory -Filter 'ABF-007*' -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Folder: $($_.FullName)"
    Get-ChildItem -Path $_.FullName -File | Select-Object Name, Length | Format-Table -AutoSize
}