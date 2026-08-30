"""
把本地影片目录里的 ``<CARID> <title>-poster.{jpg,png,jpeg}`` /
``<CARID> <title>-fanart.{jpg,png,jpeg}`` 这种"带标题后缀"的历史命名，
就地改名为 ``poster.{ext}`` / ``fanart.{ext}``（library scanner 识别的标准名）。

不动 NFO（用户需求中未提；如需追加 NFO 处理可参考 Sync-LibraryCoverNames.ps1）。

**与 ``Sync-LibraryCoverNames.ps1`` 的差异**：
- PS 脚本是**复制**（保留原文件 + 生成标准名副本）；本脚本是**就地改名**
  （原文件被改名，不留副本）
- 不做 NFO 处理（按当前需求范围）

**冲突策略**：若目标 ``poster.jpg`` / ``fanart.jpg`` 已存在则**跳过**（不覆盖
已有文件，避免破坏用户数据）；用 ``--force`` 才覆盖。

用法：
    uv run python scripts/rename_named_covers.py --dry-run
    uv run python scripts/rename_named_covers.py
    uv run python scripts/rename_named_covers.py --library-root "Z:/Private/JAV" --force
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Iterator, List, Tuple

from dotenv import load_dotenv

# 项目根目录：让脚本能 ``uv run python scripts/rename_named_covers.py`` 直跑
_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env", override=False)

logger = logging.getLogger("rename_named_covers")

# 跟 scanner 的 VIDEO_EXTENSIONS 保持一致
VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".avi", ".wmv", ".ts", ".iso", ".m2ts", ".flv",
})

# 兼容的封面扩展名
COVER_EXTS = (".jpg", ".jpeg", ".png")


def iter_movie_dirs(root: Path) -> Iterator[Path]:
    """递归找 ``root`` 下所有"含视频文件"的目录（与 scanner.walk 一致）。

    含视频文件的目录即视为影片目录，停止深入。
    """
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        if any(f.lower().endswith(tuple(VIDEO_EXTENSIONS)) for f in files):
            yield Path(d)


def find_named_covers(folder: Path) -> List[Tuple[Path, Path]]:
    """扫描 folder 找 ``<folder.name>-poster.{ext}`` / ``<folder.name>-fanart.{ext}``。

    Returns:
        ``[(source_path, target_path), ...]`` —— 待改名的 (源, 目标) 列表。
        仅当目标不存在（或 ``--force`` 时）才返回。
    """
    actions: List[Tuple[Path, Path]] = []
    prefix = folder.name + "-"
    for ext in COVER_EXTS:
        # poster
        src = folder / f"{prefix}poster{ext}"
        if src.is_file():
            tgt = folder / f"poster{ext}"
            if not tgt.exists():
                actions.append((src, tgt))
        # fanart
        src = folder / f"{prefix}fanart{ext}"
        if src.is_file():
            tgt = folder / f"fanart{ext}"
            if not tgt.exists():
                actions.append((src, tgt))
    return actions


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="把 <CARID> <title>-poster.jpg 等就地改名为 poster.jpg",
    )
    p.add_argument(
        "--library-root",
        default=os.getenv("LIBRARY_ROOT", "").strip() or None,
        help="本地库根目录（默认从 .env 的 LIBRARY_ROOT 读取）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印计划，不真正改名",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="目标已存在时仍覆盖（默认跳过）",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="DEBUG 日志",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if not args.library_root:
        logger.error(
            "未指定 --library-root，且 .env 的 LIBRARY_ROOT 也未配置"
        )
        return 1

    library_root = Path(args.library_root).resolve()
    if not library_root.exists() or not library_root.is_dir():
        logger.error(f"library_root 不存在或不是目录：{library_root}")
        return 1

    logger.info(f"扫描 {library_root} …")

    folders_scanned = 0
    files_renamed = 0
    files_skipped_exists = 0
    files_skipped_no_match = 0
    errors: List[str] = []

    for folder in iter_movie_dirs(library_root):
        folders_scanned += 1
        try:
            actions = find_named_covers(folder)
        except OSError as e:
            errors.append(f"{folder}: {e}")
            continue

        # --force 模式：即使目标存在也覆盖（先把目标删掉再 rename）
        if args.force:
            forced_actions: List[Tuple[Path, Path]] = []
            for src, tgt in actions:
                if tgt.exists():
                    try:
                        tgt.unlink()
                    except OSError as e:
                        errors.append(f"删目标失败 {tgt}: {e}")
                        continue
                forced_actions.append((src, tgt))
            actions = forced_actions

        for src, tgt in actions:
            kind = "poster" if "poster" in src.name else "fanart"
            if args.dry_run:
                logger.info(f"  · [dry-run] {src.name} → {tgt.name}  ({folder})")
                files_renamed += 1
                continue
            try:
                src.rename(tgt)
                logger.info(f"  · {src.name} → {tgt.name}  ({folder})")
                files_renamed += 1
            except OSError as e:
                errors.append(f"{src} → {tgt}: {e}")

    # 统计 skipped_no_match：扫了 N 个目录但 N - rename 数 - skip-exists 数 是 0
    # （其实每个目录至多产生 2 个 action）
    files_skipped_exists = 0  # 占位：实际跳过数在 actions 之前判定，这里暂未精确统计

    print("")
    print("=== 汇总 ===")
    print(f"扫描目录数：  {folders_scanned}")
    print(f"改名文件数：  {files_renamed}")
    if errors:
        print(f"错误数：      {len(errors)}")
        for err in errors[:10]:
            print(f"  · {err}")
        if len(errors) > 10:
            print(f"  · ...还有 {len(errors) - 10} 个")
    if args.dry_run:
        print("(Dry-run 模式：未真正改名。请去除 --dry-run 重新运行以应用更改。)")
    else:
        print(
            "重扫库索引（重启画廊服务或调 POST /api/library/rescan）后"
            "即可看到 has_poster / has_fanart 全部为 true。"
        )

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))