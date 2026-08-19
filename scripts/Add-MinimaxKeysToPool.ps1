<#
.SYNOPSIS
    把 6 个 Minimax token plan key 加入 Hermes 的 minimax-cn credential pool。

.DESCRIPTION
    反复调 ``hermes auth add minimax-cn --label <name>``，每次通过
    ``Read-Host -AsSecureString`` 让你输入 key。SecureString 在传给
    ``hermes auth add`` 进程 stdin 之前解密，避免 key 写入：
        - shell history（PowerShell 不会记录 Read-Host 输入）
        - .env / config.yaml
        - 本脚本文件本身（脚本里没有 key 字面值）
    - 任何日志文件

    适合用 Read-Host -AsSecureString 的原因：脚本本身没有任何 key 字面量，
    所有密钥只在你的键盘 → PowerShell SecureString → heredoc 给 hermes add
    进程的 stdin 这个链路上存在。

.PARAMETER Count
    要添加的 key 数量（默认 6）。

.PARAMETER LabelPrefix
    key 的标签前缀（默认 "minimax-key"），最终标签是 ``<prefix>-1``、``<prefix>-2`` 等。

.EXAMPLE
    pwsh scripts\Add-MinimaxKeysToPool.ps1

    交互：每次提示 "Enter key #1 (no echo):"，粘贴 key，回车。

.EXAMPLE
    pwsh scripts\Add-MinimaxKeysToPool.ps1 -Count 3 -LabelPrefix "batch-a"
#>

[CmdletBinding()]
param(
    [ValidateRange(1, 50)]
    [int]$Count = 6,

    [ValidateNotNullOrEmpty()]
    [string]$LabelPrefix = 'minimax-key'
)

$ErrorActionPreference = 'Stop'

# 颜色辅助（与现有 scripts/*.ps1 风格一致）
function Write-Info { param($msg) Write-Host "ℹ $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "✓ $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "⚠ $msg" -ForegroundColor Yellow }
function Write-Err  { param($msg) Write-Host "✗ $msg" -ForegroundColor Red }

# 校验 hermes CLI 在 PATH 上
if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) {
    Write-Error "hermes CLI 未在 PATH 中。请先安装并配置 Hermes。"
    exit 1
}

# 当前 pool 状态预览
Write-Host ""
Write-Host "=== 当前 minimax-cn pool ===" -ForegroundColor Cyan
& hermes auth list | Select-String -Pattern 'minimax-cn' -Context 0,5 | ForEach-Object { Write-Host $_ }
Write-Host ""

Write-Host "将逐个添加 $Count 个 key。每个提示：先输入 label 后缀确认，再粘贴 key（不回显）。" -ForegroundColor Yellow
Write-Host "按 Ctrl+C 中止。" -ForegroundColor Yellow
Write-Host ""

$added = 0
$failed = 0

for ($i = 1; $i -le $Count; $i++) {
    $label = "$LabelPrefix-$i"
    Write-Host ("--- [{0}/{1}] 添加 {2} ---" -f $i, $Count, $label) -ForegroundColor Cyan

    # 1. 让用户输入 key，用 SecureString（不显示、不入历史）
    Write-Host "请粘贴 Minimax token plan key（输入时不回显）：" -NoNewline
    $secure = Read-Host -AsSecureString -Prompt "    key"
    if ($null -eq $secure -or $secure.Length -eq 0) {
        Write-Warn "  空输入，跳过。"
        $failed++
        continue
    }

    # 2. 解密到 BSTR（这是 SecureString API 的固有限制 —— 出内存一瞬）
    #    立即通过 .NET Marshal 复制成普通 string
    $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        # 立即清零 BSTR 内存（防止残留）
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }

    if ([string]::IsNullOrWhiteSpace($plain)) {
        Write-Warn "  key 为空，跳过。"
        $failed++
        continue
    }

    # 3. 通过 stdin 传给 hermes auth add（不通过 --api-key 参数，避免 shell history）
    #    PowerShell 的 `& cmd <@(...)` 模式：这里不能用 < heredoc 传给 hermes 这种
    #    CLI 解释器，所以用 [Console]::In 没法 —— 改用 ProcessStartInfo + RedirectStandardInput
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = (Get-Command hermes).Source
        $psi.Arguments = "auth add minimax-cn --label `"$label`" --type api-key"
        $psi.RedirectStandardInput = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true

        $proc = [System.Diagnostics.Process]::Start($psi)
        $proc.StandardInput.WriteLine($plain)
        $proc.StandardInput.Close()
        $stdout = $proc.StandardOutput.ReadToEnd()
        $stderr = $proc.StandardError.ReadToEnd()
        $proc.WaitForExit()
        $exitCode = $proc.ExitCode

        if ($exitCode -eq 0) {
            Write-Ok "  ✓ 已添加 $label"
            $added++
        } else {
            Write-Err "  ✗ hermes auth add 失败（exit=$exitCode）"
            if ($stderr) { Write-Host "    stderr: $stderr" -ForegroundColor Red }
            $failed++
        }
    } catch {
        Write-Err "  ✗ 异常：$($_.Exception.Message)"
        $failed++
    } finally {
        # 立即清零 plain 字符串（.NET string 不可主动释放，但失去引用后会被 GC）
        $plain = $null
        [System.GC]::Collect()
    }
    Write-Host ""
}

Write-Host "=== 完成 ===" -ForegroundColor Cyan
Write-Host ("  添加成功：{0}" -f $added)
Write-Host ("  添加失败：{0}" -f $failed)
Write-Host ""
Write-Host "下一步：运行 'hermes auth list' 验证 pool 大小，" -ForegroundColor Yellow
Write-Host "或 'hermes auth status minimax-cn' 看健康度。" -ForegroundColor Yellow