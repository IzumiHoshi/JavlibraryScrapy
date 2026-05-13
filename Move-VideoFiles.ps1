<#
.SYNOPSIS
    将指定文件夹中的视频文件移动到目标路径，支持按大小过滤

.DESCRIPTION
    此脚本查找指定文件夹中的所有视频文件，根据大小进行过滤，
    然后将符合条件的文件移动到目标文件夹

.PARAMETER SourcePath
    源文件夹路径，需要扫描的目录

.PARAMETER DestinationPath
    目标文件夹路径，视频文件将被移动到此目录

.PARAMETER MinimumSize
    最小文件大小（以MB为单位），默认为500MB
    只有大于等于此大小的文件才会被移动

.EXAMPLE
    .\Move-VideoFiles.ps1 -SourcePath "D:\Videos" -DestinationPath "E:\Archive"
    
.EXAMPLE
    .\Move-VideoFiles.ps1 -SourcePath "D:\Videos" -DestinationPath "E:\Archive" -MinimumSize 1000
#>

param(
    [Parameter(Mandatory=$true)]
    [ValidateScript({Test-Path $_ -PathType Container})]
    [string]$SourcePath,
    
    [Parameter(Mandatory=$true)]
    [string]$DestinationPath,
    
    [Parameter(Mandatory=$false)]
    [int]$MinimumSize = 500
)

# 视频文件扩展名列表
$videoExtensions = @('.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm', '.m4v', '.ts', '.mpg', '.mpeg', '.3gp')

# 转换大小为字节
$minSizeBytes = $MinimumSize * 1MB

# 验证并创建目标文件夹
if (-not (Test-Path $DestinationPath)) {
    try {
        New-Item -ItemType Directory -Path $DestinationPath -Force | Out-Null
        Write-Host "✓ 已创建目标文件夹: $DestinationPath" -ForegroundColor Green
    }
    catch {
        Write-Host "✗ 无法创建目标文件夹: $_" -ForegroundColor Red
        exit 1
    }
}

# 获取视频文件
Write-Host "`n正在扫描视频文件..." -ForegroundColor Cyan
Write-Host "源路径: $SourcePath" -ForegroundColor Gray
Write-Host "目标路径: $DestinationPath" -ForegroundColor Gray
Write-Host "最小文件大小: ${MinimumSize}MB" -ForegroundColor Gray
Write-Host "递归搜索: 是（所有子文件夹）`n" -ForegroundColor Gray

$getChildItemParams = @{
    Path = $SourcePath
    Filter = "*.*"
    Recurse = $true
    File = $true
}

$videoFiles = @()
foreach ($ext in $videoExtensions) {
    $getChildItemParams['Filter'] = "*$ext"
    $videoFiles += Get-ChildItem @getChildItemParams -ErrorAction SilentlyContinue
}

if ($videoFiles.Count -eq 0) {
    Write-Host "✗ 未找到任何视频文件" -ForegroundColor Yellow
    exit 0
}

# 按大小过滤
$filteredFiles = $videoFiles | Where-Object { $_.Length -ge $minSizeBytes }

if ($filteredFiles.Count -eq 0) {
    Write-Host "✗ 未找到大小超过 ${MinimumSize}MB 的视频文件" -ForegroundColor Yellow
    exit 0
}

Write-Host "找到 $($filteredFiles.Count) 个符合条件的视频文件`n" -ForegroundColor Green

# 移动文件
$successCount = 0
$skipCount = 0
$errorCount = 0

# 定义处理冲突的函数
function Resolve-FileConflict {
    param(
        [string]$FileName,
        [string]$DestinationPath,
        [string]$SourceFilePath
    )
    
    $choice = $null
    $validChoice = $false
    
    while (-not $validChoice) {
        Write-Host "`n文件名冲突: $FileName" -ForegroundColor Yellow
        Write-Host "目标路径中已存在同名文件`n" -ForegroundColor Yellow
        
        Write-Host "[1] 覆盖文件" -ForegroundColor Cyan
        Write-Host "[2] 取消移动（默认）" -ForegroundColor Cyan
        Write-Host "[3] 修改文件名后移动" -ForegroundColor Cyan
        
        $input = Read-Host "请选择 (1/2/3)"
        
        switch ($input) {
            "1" {
                $choice = "overwrite"
                $validChoice = $true
            }
            "2" {
                $choice = "skip"
                $validChoice = $true
            }
            "3" {
                $choice = "rename"
                $validChoice = $true
            }
            default {
                Write-Host "输入无效，请重新选择" -ForegroundColor Red
            }
        }
    }
    
    if ($choice -eq "rename") {
        $baseName = [System.IO.Path]::GetFileNameWithoutExtension($FileName)
        $extension = [System.IO.Path]::GetExtension($FileName)
        
        Write-Host "原文件名: $FileName" -ForegroundColor Gray
        $newName = Read-Host "请输入新的文件名（不含扩展名）"
        
        # 验证新文件名
        while ([string]::IsNullOrWhiteSpace($newName)) {
            Write-Host "文件名不能为空" -ForegroundColor Red
            $newName = Read-Host "请输入新的文件名（不含扩展名）"
        }
        
        $newFileName = "$newName$extension"
        $destFilePath = Join-Path $DestinationPath $newFileName
        
        # 检查新文件名是否已存在
        $counter = 1
        while (Test-Path $destFilePath) {
            Write-Host "新文件名也已存在，正在自动调整..." -ForegroundColor Yellow
            $newFileName = "$newName`_$counter$extension"
            $destFilePath = Join-Path $DestinationPath $newFileName
            $counter++
        }
        
        return @{
            Action = "rename"
            DestPath = $destFilePath
            NewName = $newFileName
        }
    }
    elseif ($choice -eq "overwrite") {
        return @{
            Action = "overwrite"
            DestPath = (Join-Path $DestinationPath $FileName)
        }
    }
    else {
        return @{
            Action = "skip"
        }
    }
}

foreach ($file in $filteredFiles) {
    $fileSizeMB = [math]::Round($file.Length / 1MB, 2)
    $relativePath = $file.FullName.Replace($SourcePath, "").TrimStart('\')
    
    try {
        $destFilePath = Join-Path $DestinationPath $file.Name
        
        # 处理文件名冲突
        if (Test-Path $destFilePath) {
            $resolution = Resolve-FileConflict -FileName $file.Name -DestinationPath $DestinationPath -SourceFilePath $file.FullName
            
            if ($resolution.Action -eq "skip") {
                Write-Host "⊘ 已取消: $relativePath ($fileSizeMB MB)" -ForegroundColor Yellow
                $skipCount++
                continue
            }
            elseif ($resolution.Action -eq "rename") {
                $destFilePath = $resolution.DestPath
                Write-Host "✓ 已改名为: $($resolution.NewName)" -ForegroundColor Cyan
            }
            # overwrite 则继续使用原 $destFilePath
        }
        
        Move-Item -Path $file.FullName -Destination $destFilePath -Force
        Write-Host "✓ 已移动 ($fileSizeMB MB): $relativePath" -ForegroundColor Green
        $successCount++
    }
    catch {
        Write-Host "✗ 移动失败: $relativePath - $_" -ForegroundColor Red
        $errorCount++
    }
}


# 输出统计信息
Write-Host "`n" + "="*50 -ForegroundColor Cyan
Write-Host "操作完成！" -ForegroundColor Cyan
Write-Host "="*50 -ForegroundColor Cyan
Write-Host "成功移动: $successCount 个文件" -ForegroundColor Green
Write-Host "取消移动: $skipCount 个文件" -ForegroundColor Yellow
if ($errorCount -gt 0) {
    Write-Host "失败: $errorCount 个文件" -ForegroundColor Red
}
Write-Host "="*50 -ForegroundColor Cyan
