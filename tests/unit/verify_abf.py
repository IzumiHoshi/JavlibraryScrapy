"""验证 ABF-007 文件夹：列出文件 + scan_movie_folder 返回的 has_* 标志"""
import sys
from pathlib import Path

# 把 src/ 加到 sys.path，方便直接 python 跑
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from javlibraryscrapy.library.scanner import scan_movie_folder

# UNC 路径
base = Path(r"\\192.168.0.47\团队文件-我的地盘\Private\JAV\2023-07")
print(f"Base exists: {base.exists()}")
print(f"Base resolved: {base.resolve()}")

target = next((d for d in base.iterdir() if d.name.startswith("ABF-007")), None)
if target is None:
    print("ABF-007 folder not found")
else:
    print(f"\nFolder: {target}")
    print("Files:")
    for f in sorted(target.iterdir()):
        size = f.stat().st_size
        print(f"  {f.name}  ({size:,} bytes)")
    entry = scan_movie_folder(target)
    print()
    print("scan_movie_folder result:")
    print(f"  has_nfo:    {entry.has_nfo}")
    print(f"  has_poster: {entry.has_poster}")
    print(f"  has_fanart: {entry.has_fanart}")
    print(f"  has_video:  {entry.has_video}")
    print(f"  title:      {entry.title!r}")
    print(f"  actors:     {entry.actors}")