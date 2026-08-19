"""抽样检查 sync 报错的文件夹：海报/封面源不存在"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from library_scanner import scan_movie_folder

base = Path(r"\\192.168.0.47\团队文件-我的地盘\Private\JAV")
codes = ["INF-003", "IPTD-929", "IPX-017"]
for code in codes:
    target = None
    for ydir in sorted(base.iterdir()):
        if not ydir.is_dir():
            continue
        for d in ydir.iterdir():
            if d.is_dir() and d.name.startswith(code):
                target = d
                break
        if target: break
    if not target:
        print(f"{code}: not found")
        continue
    print(f"\n=== {code}: {target.name} ===")
    for f in sorted(target.iterdir()):
        print(f"  {f.name}  ({f.stat().st_size:,} bytes)")
    entry = scan_movie_folder(target)
    print(f"  has_nfo={entry.has_nfo} has_poster={entry.has_poster} has_fanart={entry.has_fanart} has_video={entry.has_video}")