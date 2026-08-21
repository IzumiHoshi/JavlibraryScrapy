"""清理 wanted JSON 里的 ``failed/unknown`` 死条目（独立 CLI）。

为何需要这个脚本：
    批量刷新时 JavBus 永久 404 / 无 release_date 的车牌（JUR-XXX、MIAB-XXX、
    部分冷门厂牌）会被标 ``_status=failed`` + ``_bucket=unknown``。这些死条目
    永远不会成功抓取磁力，只污染 unknown 月份桶的计数。

    服务已经在批量刷新完成时自动清理（``wanted_refresh.cleanup_failed_unknown``）；
    本脚本提供手动入口，方便用户随时清理存量历史脏数据。

用法：
    python scripts/cleanup_unknown.py            # 默认 dry-run：只显示会被删的
    python scripts/cleanup_unknown.py --yes      # 真正写入磁盘
    python scripts/cleanup_unknown.py --data <path>  # 指定非默认 JSON 路径

退出码：成功 0；用户取消（Ctrl+C / 不输入 yes）0；找不到文件 1。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


# 默认 JSON 路径与服务端 WantedService 一致（见 server/app.py）
# ``MOSTWANTED_LIBRARY_ROOT`` 设了 → JSON 在那里；否则退回 ``library_index.parent``。
# 服务跑起来时这些路径由 settings 提供；脚本里直接读两个常见位置中先存在的那个。
DEFAULT_PATHS = [
    Path("./output/javlibrary_movies.json"),
    Path("./javlibrary_movies.json"),
]


def find_default_json() -> Path | None:
    for p in DEFAULT_PATHS:
        if p.exists():
            return p
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="javlibrary_movies.json 路径；不传则在 ./output/ 和 ./ 下查找",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="真正写入磁盘；不传则 dry-run 只展示会被清理的条目",
    )
    args = parser.parse_args()

    data_path: Path | None = args.data or find_default_json()
    if data_path is None:
        print(
            "ERROR: 找不到 javlibrary_movies.json。\n"
            "  1. 在仓库根目录运行本脚本（默认路径 ./output/javlibrary_movies.json）；\n"
            "  2. 或用 --data <path> 显式指定 JSON 路径。",
            file=sys.stderr,
        )
        return 1

    try:
        with open(data_path, "r", encoding="utf-8") as f:
            movies: List[Dict[str, Any]] = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: 读取 {data_path} 失败：{e}", file=sys.stderr)
        return 1

    # 与 wanted_refresh.cleanup_failed_unknown 完全相同的规则：
    # 仅 ``_status=failed`` 且 ``_bucket=unknown`` 的条目被清理。
    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    for m in movies:
        if (
            m.get("_status") == "failed"
            and (m.get("_bucket") or "unknown") == "unknown"
        ):
            removed.append(m)
        else:
            kept.append(m)

    if not removed:
        print(f"[OK] {data_path} no failed/unknown entries ({len(movies)} total).")
        return 0

    print(f"path: {data_path}")
    print(f"total: {len(movies)} (will remove {len(removed)} -> keep {len(kept)})")
    print()
    print("Entries to remove (_status=failed AND _bucket=unknown):")
    for m in removed:
        code = (m.get("code") or "?").strip() or "?"
        title = (m.get("title") or "").strip()
        updated = (m.get("_updated_at") or "").strip()
        line = f"  - {code}"
        if title:
            line += f"  | {title}"
        if updated:
            line += f"  | _updated_at={updated}"
        print(line)
    print()

    if not args.yes:
        print("DRY-RUN: nothing written. Re-run with --yes to actually clean.")
        return 0

    # 原子写：tmp → rename，避免中途崩了损坏原文件
    tmp = data_path.with_suffix(data_path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(kept, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(data_path)
    except OSError as e:
        print(f"ERROR: write failed: {e}", file=sys.stderr)
        return 1

    print(f"[OK] removed {len(removed)}; kept {len(kept)} -> {data_path}")
    print()
    print("Note: if the gallery server is running, /api/wanted/months will")
    print("      still return stale data until the server reloads the JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())