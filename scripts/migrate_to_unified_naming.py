"""把本地影片库迁移到 MovieExporter 的统一命名。

MovieExporter 重构后所有写入都走 ``<CARID> <title>/{movie.nfo, poster.jpg, fanart.jpg, sample_NNN.jpg}``。
旧版三套实现各用各的命名，会和现在的代码产生冲突（重复下载 / 命名混乱）：

    旧实现                                       旧文件                            新文件
    cli/workflow.py (step3)                      <CARID> <title>.nfo              movie.nfo
    cli/workflow.py (step3)                      fanart.png                       fanart.jpg
    cli/workflow.py (step3)                      poster.png (从 fanart 裁的)      poster.jpg（从 JAVLibrary 下）
    server/services/wanted_refresh.py            cover.jpg                        fanart.jpg

本脚本扫描 library_root 下所有子目录，把上述旧命名重命名成新命名（若新文件已存在则跳过，
不覆盖）。

用法：
    python scripts/migrate_to_unified_naming.py                       # 默认 dry-run：只显示会被改的
    python scripts/migrate_to_unified_naming.py --yes                 # 真正重命名
    python scripts/migrate_to_unified_naming.py --library-root Z:/JAV # 指定库根目录
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("migrate")


# 迁移规则：(旧文件名, 新文件名)。新文件存在则跳过（不覆盖）
RENAME_RULES: List[Tuple[str, str]] = [
    ("cover.jpg", "fanart.jpg"),        # 旧 wanted_refresh → 新
    ("fanart.png", "fanart.jpg"),       # 旧 workflow → 新
    ("poster.png", "poster.jpg"),       # 旧 workflow（从 fanart 裁的）→ 新（保留旧图）
]


def find_movie_folders(library_root: Path) -> List[Path]:
    """找出所有疑似 ``<CARID> <title>/`` 子目录（含视频文件 / NFO 的直接子目录）。

    排除非影片目录（如 ``.git``、``output``、``temp`` 等）。
    """
    movie_folders: List[Path] = []
    skip_names = {".git", "output", "temp", "tests", "scripts", ".pytest_cache", "src", "docs"}
    try:
        for entry in library_root.iterdir():
            if not entry.is_dir():
                continue
            if entry.name in skip_names or entry.name.startswith("."):
                continue
            movie_folders.append(entry)
    except OSError as e:
        logger.error(f"扫描 {library_root} 失败：{e}")
    return movie_folders


def plan_renames(folder: Path) -> List[Tuple[Path, Path]]:
    """为单个目录算出 (src, dst) 重命名列表。dst 已存在则跳过。"""
    plans: List[Tuple[Path, Path]] = []
    for old_name, new_name in RENAME_RULES:
        src = folder / old_name
        dst = folder / new_name
        if not src.exists():
            continue
        if dst.exists():
            logger.debug(f"  跳过 {folder.name}/{old_name}（{new_name} 已存在）")
            continue
        plans.append((src, dst))

    # NFO 重命名：旧版 workflow 用 ``<CARID> <title>.nfo``，新统一用 ``movie.nfo``。
    # 只在目录里有且仅有一个 ``*.nfo`` 时才重命名（避免误伤多个 NFO 的边角案例）。
    nfo_files = list(folder.glob("*.nfo"))
    if len(nfo_files) == 1 and nfo_files[0].name != "movie.nfo":
        src = nfo_files[0]
        dst = folder / "movie.nfo"
        if not dst.exists():
            plans.append((src, dst))
    return plans


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="把本地影片库迁移到 MovieExporter 统一命名")
    p.add_argument(
        "--library-root",
        type=Path,
        default=None,
        help="本地库根目录（默认从 .env 的 MOSTWANTED_LIBRARY_ROOT 读取）",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="真正执行重命名（默认 dry-run：只打印）",
    )
    args = p.parse_args(argv)

    library_root = args.library_root
    if library_root is None:
        try:
            from dotenv import load_dotenv
            from javlibraryscrapy._paths import REPO_ROOT
            load_dotenv(REPO_ROOT / ".env", override=False)
            import os
            mw = os.getenv("MOSTWANTED_LIBRARY_ROOT", "").strip()
            if mw:
                library_root = Path(mw)
        except Exception as e:
            logger.warning(f"读 .env 失败：{e}")

    if library_root is None:
        logger.error(
            "未指定 --library-root，且 .env 的 MOSTWANTED_LIBRARY_ROOT 也未配置"
        )
        return 1
    library_root = library_root.resolve()
    if not library_root.is_dir():
        logger.error(f"library-root 不是目录：{library_root}")
        return 1

    logger.info(f"扫描：{library_root}")
    folders = find_movie_folders(library_root)
    logger.info(f"发现 {len(folders)} 个疑似影片目录")

    all_plans: List[Tuple[Path, Path]] = []
    for folder in folders:
        plans = plan_renames(folder)
        for src, dst in plans:
            logger.info(f"  [计划] {folder.name}/{src.name} → {dst.name}")
        all_plans.extend(plans)

    if not all_plans:
        logger.info("没有需要迁移的文件 ✅")
        return 0

    if not args.yes:
        logger.info(f"\nDRY RUN：共 {len(all_plans)} 个重命名待执行。加 --yes 真正执行。")
        return 0

    moved = failed = skipped = 0
    for src, dst in all_plans:
        try:
            src.rename(dst)
            logger.info(f"  ✅ {src.parent.name}/{src.name} → {dst.name}")
            moved += 1
        except OSError as e:
            logger.warning(f"  ✗ {src} 重命名失败：{e}")
            failed += 1
    logger.info(f"\n完成：成功 {moved}，失败 {failed}，跳过 {skipped}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
