"""验证 CAWD-436：sync脚本报 poster=exists，应该本就有标准名"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from javlibraryscrapy.library.scanner import scan_movie_folder

base = Path(r"\\192.168.0.47\团队文件-我的地盘\Private\JAV\2025-06")
target = next((d for d in base.iterdir() if "CAWD-436" in d.name), None)
if not target:
    print("not found")
else:
    print(f"Folder: {target.name}")
    print("Files:")
    for f in sorted(target.iterdir()):
        print(f"  {f.name}  ({f.stat().st_size:,} bytes)")
    entry = scan_movie_folder(target)
    print(f"\nhas_nfo={entry.has_nfo} has_poster={entry.has_poster} has_fanart={entry.has_fanart} has_video={entry.has_video}")