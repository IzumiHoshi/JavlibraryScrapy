"""给老的目标目录补 fanart.jpg + poster.jpg。

历史 wanted refresh（MovieExporter 迁移 #10 之前）+ 早期 organize 调用，目标目录里
只有 cover.jpg，没 fanart.jpg / poster.jpg。Plex / Kodi / Infuse 都用 fanart.jpg + poster.jpg
做海报展示，没有就显示缩略图占位。

本脚本扫描 library_root 下所有 ``<CARID> <title>/`` 子目录，对每个：
1. 有 cover.{jpg,png} + 无 fanart.{jpg,png} → 重命名为 fanart，删 cover
2. 有 fanart.{jpg,png} + 无 poster.{jpg,png} → split_poster_from_fanart 派生 poster

策略跟 library/refresher.refresh_library_movie 一致 —— 跟当前代码保持单一逻辑。

用法::

    # 默认 dry-run：只列出会被改的文件
    uv run python scripts/derive_fanart_poster.py

    # 真正执行
    uv run python scripts/derive_fanart_poster.py --yes

    # 指定库根（默认读 .env 的 LIBRARY_ROOT，回退 Z:/Private/JAV）
    uv run python scripts/derive_fanart_poster.py --library-root Z:/JAV

    # 限制到某个月份（修某个批次时用）
    uv run python scripts/derive_fanart_poster.py --month 2026-08
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# 让 ``from javlibraryscrapy...`` 可解析
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from javlibraryscrapy.library.scanner import (  # noqa: E402
    COVER_NAMES,
    VIDEO_EXTENSIONS,
)
from javlibraryscrapy.utils.fanart import split_poster_from_fanart  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("derive_fanart_poster")

# 要排除的顶层目录（不是影片库）
SKIP_TOP_NAMES = {
    ".git", ".venv", "output", "temp", "tests", "scripts",
    ".pytest_cache", "src", "docs", "__pycache__",
}

def is_movie_folder(sub_entries: List[Path]) -> bool:
    """判断一个目录是不是影片目录。

    接受任一信号：
    - 有视频文件
    - 有 movie.nfo 或 <CARID>.nfo
    - 有 cover.{jpg,png,jpeg} 且至少一张 sample_*.jpg（历史 wanted refresh 漏 NFO 时的特征）
    """
    has_video = False
    has_nfo = False
    has_cover = False
    has_sample = False
    for e in sub_entries:
        if not e.is_file():
            continue
        lname = e.name.lower()
        if e.suffix.lower() in VIDEO_EXTENSIONS:
            has_video = True
        elif e.suffix.lower() == ".nfo":
            has_nfo = True
        elif e.name.startswith("cover.") or e.name.startswith("poster."):
            has_cover = True
        elif e.name.startswith("sample_"):
            has_sample = True
    return has_video or has_nfo or (has_cover and has_sample)


def find_movie_folders(library_root: Path, month: str = "") -> Iterable[Path]:
    """遍历 library_root，返回所有疑似 ``<CARID> <title>/`` 子目录。

    判别标准（任一满足）：
    - 有视频文件
    - 有 ``movie.nfo`` 或 ``<carid>.nfo``
    - 有 cover.jpg + 至少一张 sample_*.jpg（历史漏 NFO 时）

    不递归进影片目录内部（避免误判片子的子目录）。

    ``month`` 非空时只返回 ``<month>/<CARID> <title>/`` 这条路径下的目录。
    """
    roots = [library_root / month] if month else [library_root]
    for root in roots:
        if not root.exists():
            continue
        try:
            entries = list(root.iterdir())
        except (PermissionError, OSError) as e:
            logger.warning(f"无法枚举 {root}: {e}")
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name in SKIP_TOP_NAMES or entry.name.startswith("."):
                continue
            try:
                sub_entries = list(entry.iterdir())
            except (PermissionError, OSError) as e:
                logger.warning(f"无法枚举 {entry}: {e}")
                continue
            if is_movie_folder(sub_entries):
                yield entry


def find_cover(folder: Path) -> Tuple[Path, str]:
    """返回 (cover 路径, 后缀 ``.jpg`` / ``.png``)。不存在抛 FileNotFoundError。"""
    for ext in (".jpg", ".png", ".jpeg"):
        candidate = folder / f"cover{ext}"
        if candidate.exists():
            return candidate, ext
    raise FileNotFoundError(folder)


def derive_one(folder: Path) -> List[Tuple[str, str]]:
    """对一个目录执行派生操作。返回 [(action, filename), ...] 列表（用于汇报）。

    action ∈ ``{created, skipped, failed}``，filename 是被操作的文件。
    """
    actions: List[Tuple[str, str]] = []

    has_fanart = any((folder / f"fanart{ext}").exists() for ext in (".jpg", ".png", ".jpeg"))
    has_poster = any((folder / f"poster{ext}").exists() for ext in (".jpg", ".png", ".jpeg"))

    # 1) cover → fanart
    if not has_fanart:
        try:
            cover, ext = find_cover(folder)
        except FileNotFoundError:
            pass  # 没有 cover，跳过 fanart 派生
        else:
            fanart_dst = folder / f"fanart{ext}"
            try:
                shutil.copy2(cover, fanart_dst)
                try:
                    cover.unlink()
                except OSError:
                    pass
                actions.append(("created", fanart_dst.name))
            except OSError as e:
                actions.append(("failed", f"{cover.name}→{fanart_dst.name}: {e}"))

    # 2) fanart → poster
    if not has_poster:
        # 找最新的 fanart（j 优先 png）
        fanart_path: Path = None  # type: ignore[assignment]
        for ext in (".jpg", ".png", ".jpeg"):
            cand = folder / f"fanart{ext}"
            if cand.exists():
                fanart_path = cand
                break
        if fanart_path is not None:
            poster_dst = folder / "poster.jpg"
            try:
                split_poster_from_fanart(fanart_path, poster_dst)
                actions.append(("created", poster_dst.name))
            except Exception as e:  # noqa: BLE001
                actions.append(("failed", f"{fanart_path.name}→poster.jpg: {e}"))

    return actions


def main() -> int:
    parser = argparse.ArgumentParser(
        description="给老的本地影片目录补 fanart.jpg + poster.jpg（默认 dry-run）"
    )
    parser.add_argument(
        "--library-root",
        type=Path,
        default=None,
        help=(
            "本地影片库根目录。默认读 .env 的 LIBRARY_ROOT，"
            "再回退 Z:/Private/JAV。"
        ),
    )
    parser.add_argument(
        "--month",
        type=str,
        default="",
        help="只处理某个月份桶（如 2026-08），不传则全库",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="真正执行（不传则 dry-run：只打印，不改盘）",
    )
    args = parser.parse_args()

    # 解析 library_root
    if args.library_root is not None:
        library_root = args.library_root
    else:
        env_root = os.environ.get("LIBRARY_ROOT", "").strip()
        library_root = Path(env_root) if env_root else Path("Z:/Private/JAV")
    logger.info(f"library_root = {library_root}")
    if not library_root.exists():
        logger.error(f"library_root 不存在：{library_root}")
        return 1

    dry_run = not args.yes
    if dry_run:
        logger.warning("DRY-RUN 模式：不改盘，加 --yes 真正执行")

    folders = list(find_movie_folders(library_root, args.month))
    logger.info(f"扫描到 {len(folders)} 个疑似影片目录")

    created_count = 0
    skipped_count = 0
    failed_count = 0
    touched_folders: List[str] = []

    for folder in folders:
        actions = derive_one(folder)
        if not actions:
            skipped_count += 1
            continue

        # dry-run 模式：只打印，不写盘
        if dry_run:
            for action, name in actions:
                mode = "🔧" if action == "created" else "❌"
                rel = folder.relative_to(library_root)
                logger.info(f"  {mode} [{action}] {rel}/{name}")
            created_count += sum(1 for action, _ in actions if action == "created")
            continue

        touched_folders.append(str(folder.relative_to(library_root)))
        for action, name in actions:
            if action == "created":
                created_count += 1
                rel = folder.relative_to(library_root)
                logger.info(f"  ✓ [{action}] {rel}/{name}")
            elif action == "failed":
                failed_count += 1
                rel = folder.relative_to(library_root)
                logger.error(f"  ✗ [{action}] {rel}/{name}")

    logger.info("=" * 60)
    if dry_run:
        logger.warning(
            f"DRY-RUN 完成：{created_count} 个文件待创建，{failed_count} 个失败"
        )
    else:
        logger.info(
            f"执行完成：{created_count} 个文件创建，{failed_count} 个失败"
        )
    if touched_folders and not dry_run:
        logger.info(f"受影响目录数：{len(touched_folders)}")
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())