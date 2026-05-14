<#
.SYNOPSIS
    将文件名中@字符前的内容去掉，只保留@之后的文件名

.DESCRIPTION
    此脚本搜索指定文件夹中的所有文件，找出文件名中包含@符号的文件，
    将@前面的内容去掉，只保留@后面的部分作为新文件名。
    例如：hkbisi.com@ABF-340-C.mp4 → ABF-340-C.mp4

.PARAMETER SourcePath
    源文件夹路径，需要扫描的目录

.PARAMETER Preview
    仅预览将进行的更改，不实际重命名文件。默认为$false

.EXAMPLE
    .\Rename-FilesByAtSymbol.ps1 -SourcePath "D:\Videos"
    
.EXAMPLE
    .\Rename-FilesByAtSymbol.ps1 -SourcePath "D:\Videos" -Preview $true
    
.NOTES
    支持所有文件类型的重命名。递归搜索所有子文件夹。
    如果清理后的文件名已存在，将自动添加数字后缀 (_1, _2, 等)。
#>

param(
    [Parameter(Mandatory=$true)]
    [ValidateScript({Test-Path $_ -PathType Container})]
    [string]$SourcePath,
    
    [Parameter(Mandatory=$false)]
    [bool]$Preview = $false
)

# 获取所有文件
Write-Host "`n正在扫描文件..." -ForegroundColor Cyan
Write-Host "源路径: $SourcePath" -ForegroundColor Gray
Write-Host "预览模式: $Preview`n" -ForegroundColor Gray

$allFiles = Get-ChildItem -Path $SourcePath -Recurse -File -ErrorAction SilentlyContinue

if ($allFiles.Count -eq 0) {
    Write-Host "✗ 未找到任何文件" -ForegroundColor Yellow
    exit 0
}

# 筛选包含@符号的文件
$filesToRename = $allFiles | Where-Object { $_.Name -match '@' }

if ($filesToRename.Count -eq 0) {
    Write-Host "✗ 未找到包含@符号的文件" -ForegroundColor Yellow
    exit 0
}

Write-Host "找到 $($filesToRename.Count) 个需要重命名的文件`n" -ForegroundColor Green



# 开始重命名
$successCount = 0
$skipCount = 0
$errorCount = 0
$currentIndex = 0
$totalFiles = $filesToRename.Count

foreach ($file in $filesToRename) {
    $currentIndex++
    
    # 显示进度条
    $percentComplete = [int](($currentIndex / $totalFiles) * 100)
    Write-Progress -Activity "处理文件" `
                   -Status "正在处理: $($file.Name)" `
                   -PercentComplete $percentComplete `
                   -CurrentOperation "[$currentIndex/$totalFiles]"
    
    try {
        $fileName = $file.Name
        $filePath = $file.FullName
        $fileDirectory = $file.Directory.FullName
        $relativePath = $filePath.Replace($SourcePath, "").TrimStart('\')
        
        # 提取@之后的文件名
        if ($fileName -match '(.+)@(.+)') {
            $newFileName = $matches[2]
            $newFilePath = Join-Path $fileDirectory $newFileName
            
            # 检查新文件名是否已存在（排除自身）
            if ((Test-Path $newFilePath) -and $newFilePath -ne $filePath) {
                # 文件名已存在，添加后缀
                $baseName = [System.IO.Path]::GetFileNameWithoutExtension($newFileName)
                $extension = [System.IO.Path]::GetExtension($newFileName)
                $counter = 1
                
                # 找到一个不存在的文件名
                while (Test-Path $newFilePath) {
                    $newFileName = "$baseName`_$counter$extension"
                    $newFilePath = Join-Path $fileDirectory $newFileName
                    $counter++
                }
                
                $addedSuffix = " (已添加后缀 _$($counter - 1))"
            }
            else {
                $addedSuffix = ""
            }
            
            if ($Preview) {
                Write-Host "📋 预览: $fileName → $newFileName$addedSuffix" -ForegroundColor Cyan
                $successCount++
            }
            else {
                # 执行重命名
                Rename-Item -Path $filePath -NewName $newFileName -Force
                Write-Host "✓ 已重命名: $fileName → $newFileName$addedSuffix" -ForegroundColor Green
                $successCount++
            }
        }
    }
    catch {
        Write-Progress -Completed
        Write-Host "✗ 重命名失败: $relativePath - $_" -ForegroundColor Red
        $errorCount++
    }
}

# 清除进度条
Write-Progress -Completed

# 输出统计信息
Write-Host "`n" + "="*50 -ForegroundColor Cyan
Write-Host "操作完成！" -ForegroundColor Cyan
Write-Host "="*50 -ForegroundColor Cyan

if ($Preview) {
    Write-Host "预览文件数: $successCount 个" -ForegroundColor Cyan
}
else {
    Write-Host "成功重命名: $successCount 个文件" -ForegroundColor Green
}

Write-Host "跳过: $skipCount 个文件" -ForegroundColor Yellow

if ($errorCount -gt 0) {
    Write-Host "失败: $errorCount 个文件" -ForegroundColor Red
}

Write-Host "="*50 -ForegroundColor Cyan
