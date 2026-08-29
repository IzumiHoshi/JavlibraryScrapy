"""补齐本地库缺失的元数据文件（movie.nfo / poster.jpg / fanart.jpg / sample_NNN.jpg）。

扫描 ``--library-root`` 下所有「含视频文件」的目录，对每个缺失目标文件的目录
调 :func:`javlibraryscrapy.library.backfill.backfill_library`。
MovieExporter 内部的 ``.exists()`` 检查 + ``overwrite_nfo=False`` 自动策略保证
**绝不覆写已有文件**。

``--source``（默认从 ``MOSTWANTED_INDEX`` / ``MOSTWANTED_LIBRARY_ROOT`` /
``output/javlibrary_movies.json`` 顺序回退）提供 JAVLibrary 缩略图 URL，
让 backfill 同时下 poster.jpg。

CLI：
    uv run python -m javlibraryscrapy.cli.backfill \\
        --library-root "Z:\\\\JAV"

    uv run python -m javlibraryscrapy.cli.backfill \\
        --source output/javlibrary_movies.json \\
        --library-root "Z:\\\\JAV" --limit 10 --dry-run

    uv run python -m javlibraryscrapy.cli.backfill \\
        --library-root "Z:\\\\JAV" --delay 5
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from javlibraryscrapy._paths import REPO_ROOT as ROOT  # noqa: E402
from javlibraryscrapy.library.backfill import (
    BackfillPlan,
    backfill_library,
    check_missing,
    iter_movie_folders,
)

load_dotenv(ROOT / ".env", override=False)
logger = logging.getLogger("backfill")


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #
def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="补齐本地库缺失的元数据文件（NFO / poster / fanart / samples）",
    )
    p.add_argument(
        "--library-root",
        default=os.getenv("LIBRARY_ROOT", "").strip() or None,
        help="本地库根目录（默认从 .env 的 LIBRARY_ROOT 读取；未配置则必须显式指定）",
    )
    p.add_argument(
        "--source",
        default=(
            os.getenv("MOSTWANTED_INDEX", "").strip()
            or (
                str(Path(os.getenv("MOSTWANTED_LIBRARY_ROOT", "").strip()) / "javlibrary_movies.json")
                if os.getenv("MOSTWANTED_LIBRARY_ROOT", "").strip()
                else str(ROOT / "output" / "javlibrary_movies.json")
            )
        ),
        help=(
            "JAVLibrary 抓取结果 JSON（提供 cover_url 给 poster.jpg 下载）。"
            "默认 MOSTWANTED_INDEX → MOSTWANTED_LIBRARY_ROOT/javlibrary_movies.json"
            " → output/javlibrary_movies.json"
        ),
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只处理前 N 部（调试用）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印计划，不写任何文件",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="JAVBus 每部影片之间的间隔（秒，默认 3）",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="单部抓取超时（秒，默认 180）",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示 DEBUG 级别日志",
    )
    return p.parse_args(argv)


# --------------------------------------------------------------------------- #
# cover_urls 加载
# --------------------------------------------------------------------------- #
def _load_cover_urls(source: Optional[Path]) -> Dict[str, str]:
    """从 javlibrary_movies.json 读 ``{carid: cover_url, ...}``。

    缺文件 / 解析失败 → 返空 dict（仍可下 cover / fanart / NFO，仅 poster.jpg 缺）。
    """
    if source is None:
        return {}
    if not source.exists():
        logger.warning(f"--source 文件不存在，跳过 cover_url 加载：{source}")
        return {}
    try:
        movies = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.warning(f"--source 不是合法 JSON，跳过：{e}")
        return {}
    if not isinstance(movies, list):
        logger.warning(f"--source 顶层不是 list，跳过")
        return {}
    cover_urls: Dict[str, str] = {}
    for m in movies:
        if not isinstance(m, dict):
            continue
        code = (m.get("code") or "").strip().upper()
        cover = (m.get("cover_url") or "").strip()
        if code and cover:
            cover_urls[code] = cover
    logger.info(f"已加载 {len(cover_urls)} 条 cover_url：{source}")
    return cover_urls


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
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
    if not library_root.exists():
        logger.error(f"--library-root 不存在：{library_root}")
        return 1
    if not library_root.is_dir():
        logger.error(f"--library-root 不是目录：{library_root}")
        return 1

    source_path = Path(args.source).resolve() if args.source else None
    cover_urls = _load_cover_urls(source_path)

    # ---- 阶段 1：扫描 + 打印计划 ----
    plans: List[BackfillPlan] = []
    for folder in iter_movie_folders(library_root):
        plan = check_missing(folder)
        if plan is None:
            continue
        if not plan.has_video:
            continue
        plans.append(plan)

    needs = [p for p in plans if p.needs_backfill]
    complete = [p for p in plans if p.is_complete]

    logger.info(
        f"扫描 {library_root}：影片目录 {len(plans)} 个，"
        f"已完整 {len(complete)} 个，需补齐 {len(needs)} 个"
    )

    if args.limit is not None:
        needs = needs[: args.limit]
        logger.info(f"--limit={args.limit}，只处理前 {len(needs)} 个")

    if args.dry_run:
        logger.info("DRY RUN：只打印缺失清单，不写任何文件")
        for plan in needs:
            logger.info(
                f"  [{','.join(plan.missing_kinds):<24}] "
                f"{plan.carid} {plan.title} ({plan.folder})"
            )
        return 0

    if not needs:
        logger.info("没有需要补齐的影片目录")
        return 0

    # ---- 阶段 2：逐部补齐 ----
    logger.info(f"开始补齐 {len(needs)} 个目录（间隔 {args.delay}s）")

    def _on_progress(carid: str, status: str) -> None:
        logger.info(f"  · {carid} {status}")

    stats = backfill_library(
        library_root,
        cover_urls=cover_urls,
        on_progress=_on_progress,
        delay_seconds=args.delay,
        timeout_seconds=args.timeout,
        max_count=args.limit,
    )

    logger.info(
        f"完成：backfilled {stats['backfilled']}/{stats['needs_backfill']}，"
        f"failed {stats['failed']}，skipped_complete {stats['skipped_complete']}"
    )
    if stats["failed"]:
        for r in stats["results"]:
            if r["failed"]:
                logger.warning(
                    f"  · 失败 {r.get('code')}: {r.get('error')}"
                )
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))