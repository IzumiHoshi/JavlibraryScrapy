"""
把 JAVLibrary「最想要」列表导出到本地库。

读 javlibrary_movies.json（默认 ``MOSTWANTED_INDEX`` > ``MOSTWANTED_LIBRARY_ROOT``/
``javlibrary_movies.json`` > ``output/javlibrary_movies.json``），对每部影片在
``<library_root>/<CARID> <title>/`` 下建一个文件夹，里面放：

  - movie.nfo    —— 从 JAVBus 详情页抓到的完整元数据（Kodi/Plex 兼容）
  - poster.jpg   —— JAVLibrary 列表的竖版缩略图（cover_url）
  - fanart.jpg   —— JAVBus 详情页的横版原图
  - sample_NNN.jpg —— JAVBus sample waterfall（默认开，可关）
  - magnets.json / magnets_links.txt —— 集中磁力索引

所有削刮逻辑走统一的 :class:`MovieExporter`；本文件只负责 CLI 参数、计划打印、
跳过/覆盖决策。

CLI：
    uv run python scripts/export_mostwanted.py
    uv run python scripts/export_mostwanted.py --source output/javlibrary_movies.json \\
        --library-root "Z:\\JAV\\MostWanted" --overwrite
    uv run python scripts/export_mostwanted.py --dry-run  # 只打印计划
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from urllib3.exceptions import InsecureRequestWarning

# 走代理时显式 ``verify=False``（MITM 自签 CA），urllib3 会刷屏；
# 统一静默，与 cli/gallery.py 入口行为一致。
warnings.filterwarnings("ignore", category=InsecureRequestWarning)

from javlibraryscrapy._paths import REPO_ROOT as ROOT  # noqa: E402
from javlibraryscrapy.scraping.exporter import MovieExporter  # noqa: E402

load_dotenv(ROOT / ".env", override=False)
logger = logging.getLogger("export_mostwanted")


# 与 utils.car.find_car_bus 的排除列表保持一致 —— 这些在 JAVBus 上没有页面
EXCLUDED_CAR_PREFIXES = ("HEYZO", "PONDO", "CARIB", "OKYOHOT")


# --------------------------------------------------------------------------- #
# poster.jpg 下载（仅 ``--skip-javbus`` 模式用；正常模式由 MovieExporter 处理）
# --------------------------------------------------------------------------- #
def _download_image(
    url: str,
    dest: Path,
    headers: Dict[str, str],
    proxy: Optional[str],
    timeout: int = 10,
) -> bool:
    """通用图片下载（同步）。"""
    if not url:
        return False
    try:
        r = requests.get(
            url,
            headers=headers,
            timeout=timeout,
            proxies=({"http": proxy, "https": proxy} if proxy else None),
            verify=False if proxy else True,
        )
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"下载失败 {url[:80]}... → {dest.name}: {e}")
        return False


def _download_javlibrary_cover(
    cover_url: str, dest: Path, proxy: Optional[str]
) -> bool:
    """下载 JAVLibrary 列表页的缩略图作为 poster.jpg（``--skip-javbus`` 专用）。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.javlibrary.com/cn/",
    }
    return _download_image(cover_url, dest, headers, proxy)


# --------------------------------------------------------------------------- #
# 计划
# --------------------------------------------------------------------------- #
def _plan_folder(movies: List[Dict[str, Any]], library_root: Path) -> List[Tuple[Dict[str, Any], Path, str]]:
    """为每部影片计算目标文件夹 + 状态（'new' / 'exists' / 'excluded' / 'invalid'）。"""
    plan: List[Tuple[Dict[str, Any], Path, str]] = []
    for m in movies:
        code = (m.get("code") or "").strip().upper()
        title = (m.get("title") or "").strip()
        if not code or not title:
            plan.append((m, Path("."), "invalid"))
            continue
        if any(code.startswith(p) for p in EXCLUDED_CAR_PREFIXES):
            plan.append((m, Path("."), "excluded"))
            continue
        folder = library_root / f"{code} {title}"
        status = "exists" if folder.exists() else "new"
        plan.append((m, folder, status))
    return plan


# --------------------------------------------------------------------------- #
# CLI 参数
# --------------------------------------------------------------------------- #
def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="把 JAVLibrary 最想要列表导出到本地库（每部一个文件夹）",
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
            "JAVLibrary 抓取结果 JSON（默认 "
            "MOSTWANTED_INDEX；未设则退回 "
            "MOSTWANTED_LIBRARY_ROOT/javlibrary_movies.json，"
            "再退回 output/javlibrary_movies.json）"
        ),
    )
    p.add_argument(
        "--library-root",
        default=os.getenv("MOSTWANTED_LIBRARY_ROOT", "").strip() or None,
        help=(
            "本地库根目录（默认从 .env 的 MOSTWANTED_LIBRARY_ROOT 读取；"
            "未配置则禁用本功能）"
        ),
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="目标文件夹已存在时仍写入（默认跳过）",
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
        "--skip-javbus",
        action="store_true",
        help="跳过 JAVBus 抓取（只下 poster.jpg，不下 fanart.jpg 不写完整 NFO）",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只处理前 N 部影片（调试用）",
    )
    return p.parse_args(argv)


# --------------------------------------------------------------------------- #
# 主编排
# --------------------------------------------------------------------------- #
async def _async_main(args: argparse.Namespace) -> int:
    if not args.library_root:
        logger.error(
            "未指定 --library-root，且 .env 的 MOSTWANTED_LIBRARY_ROOT 也未配置"
        )
        return 1

    library_root = Path(args.library_root).resolve()
    if not library_root.exists():
        logger.error(f"library-root 不存在：{library_root}")
        return 1
    if not library_root.is_dir():
        logger.error(f"library-root 不是目录：{library_root}")
        return 1

    source = Path(args.source).resolve()
    if not source.exists():
        logger.error(f"source 文件不存在：{source}")
        return 1

    try:
        movies = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error(f"source 不是合法 JSON：{e}")
        return 1

    if not isinstance(movies, list):
        logger.error("source JSON 顶层不是 list")
        return 1

    logger.info(f"读到 {len(movies)} 部影片（source: {source}）")
    logger.info(f"导出根目录：{library_root}")
    if args.skip_javbus:
        logger.warning("--skip-javbus 已启用：只下 poster.jpg，不拉 JAVBus、不写 NFO")

    plan = _plan_folder(movies, library_root)
    if args.limit is not None:
        plan = plan[: args.limit]
        logger.info(f"--limit={args.limit}，只处理前 {len(plan)} 部")

    stats = {"new": 0, "exists": 0, "excluded": 0, "invalid": 0}
    for _movie, _folder, status in plan:
        stats[status] = stats.get(status, 0) + 1
    logger.info(
        f"计划：新建 {stats['new']} 个 / 已存在 {stats['exists']} 个 / "
        f"排除 {stats['excluded']} 部 / 字段缺失 {stats['invalid']} 部"
    )

    if args.dry_run:
        logger.info("DRY RUN：只打印目标文件夹清单")
        for _movie, folder, status in plan:
            label = {
                "new": "将创建",
                "exists": "已存在（将被 --overwrite 影响）",
                "excluded": "排除列表",
                "invalid": "字段缺失",
            }.get(status, status)
            target = folder if status in ("new", "exists") else Path(".")
            logger.info(f"  [{label}] {target}")
        return 0

    # 跳过已存在的（除非 --overwrite），准备处理列表
    to_process = [m for m, _f, s in plan if s == "new" or (args.overwrite and s == "exists")]
    if not to_process:
        logger.info("没有需要处理的影片（全部已存在或被排除）")
        return 0

    # ---- skip-javbus：只下 poster.jpg ----
    if args.skip_javbus:
        # 临时构造一个 exporter 来拿 JAVBus proxy（默认沿用 .env）
        tmp = MovieExporter(output_root=library_root, move_video=False, download_samples=False, collect_magnets=False)
        proxy = tmp.proxy
        ok_count = 0
        for movie in to_process:
            code = movie["code"].strip().upper()
            title = movie["title"].strip()
            save_dir = library_root / f"{code} {title}"
            save_dir.mkdir(parents=True, exist_ok=True)
            poster_path = save_dir / "poster.jpg"
            if _download_javlibrary_cover(movie.get("cover_url"), poster_path, proxy):
                ok_count += 1
                logger.info(f"已下载 poster.jpg：{code}")
            else:
                logger.warning(f"poster.jpg 下载失败：{code}（跳过）")
        logger.info(f"完成：仅 poster.jpg 已写入 {ok_count}/{len(to_process)} 部")
        return 0

    # ---- 正常模式：调 MovieExporter 统一处理 ----
    exporter = MovieExporter(
        output_root=library_root,
        move_video=False,
        download_samples=True,
        collect_magnets=True,
        magnets_index=library_root / "magnets.json",
    )
    proxy = exporter.proxy
    cover_urls: Dict[str, str] = {
        m["code"].strip().upper(): (m.get("cover_url") or "").strip()
        for m in to_process
        if m.get("cover_url")
    }
    car_list = [(m["code"].strip().upper(), "") for m in to_process]
    logger.info(
        f"开始批量拉 JAVBus，共 {len(car_list)} 部（间隔 {args.delay}s，"
        f"代理={'开启' if proxy else '关闭'}）"
    )
    stats = await exporter.export_movies(car_list, cover_urls=cover_urls)

    logger.info(
        f"完成：处理 {stats['written']} 部，"
        f"failed {stats['failed']} 部，"
        f"magnets_ok {stats['magnets_collected']} 部，"
        f"magnets.json → {library_root / 'magnets.json'}"
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s)",
    )
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
