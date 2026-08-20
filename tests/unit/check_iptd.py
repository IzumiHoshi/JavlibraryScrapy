"""检查 IPTD-999 当前实际文件"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

# IPTD-999 的正确文件夹名 (Rio)
import subprocess
result = subprocess.run(
    ["pwsh", "-NoProfile", "-Command",
     "Get-ChildItem -LiteralPath '\\\\192.168.0.47\\团队文件-我的地盘\\Private\\JAV\\2012-11' -Directory -Filter 'IPTD-999*' | ForEach-Object { Write-Host $_.FullName }"],
    capture_output=True, text=True, timeout=10
)
print("PWSH stdout:", result.stdout)
print("PWSH stderr:", result.stderr)