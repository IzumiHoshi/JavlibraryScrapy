"""本地库「补齐缺失文件」核心逻辑。

检查每个车牌目录下 ``movie.nfo / poster.jpg / fanart.jpg / sample_NNN.jpg``
的存在性，缺失则调 :class:`MovieExporter` 走 JAVBus 抓取 + 文件落地；
MovieExporter 内部的 ``_place_fanart`` / ``_download_javlibrary_cover`` /
``_move_samples_to_target`` 自带 ``.exists()`` 幂等检查，``overwrite_nfo=False``
显式控制 NFO 不被覆写 —— **绝不触碰已有文件**。

不修改 / 移动 / 删除视频文件；车牌目录下**必须含视频文件**才会被处理
（纯空目录无意义），厂牌排除列表（HEYZO / PONDO / CARIB / OKYOHOT / LUXU / MIUM）
与 :mod:`javlibraryscrapy.cli.export_mostwanted` 和 :mod:`javlibraryscrapy.utils.car`
保持一致。

CLI：
    uv run python -m javlibraryscrapy.cli.backfill \\
        --library-root "Z:\\\\JAV" [--limit 10] [--dry-run] [--only-kinds ...]

服务层：``server.services.library_backfill.LibraryBackfillService`` 复用本模块。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Tuple,
)

from javlibraryscrapy.library.scanner import (
    VIDEO_EXTENSIONS,
    scan_movie_folder,
)
from javlibraryscrapy.scraping.exporter import MovieExporter

logger = logging.getLogger("javlibraryscrapy.backfill")

# 厂牌排除列表：JAVBus 上没有页面；与 ``utils.car.find_car_bus`` 的硬编码排除 +
# ``cli.export_mostwanted.EXCLUDED_CAR_PREFIXES`` 合并。
EXCLUDED_CAR_PREFIXES: Tuple[str, ...] = (
    "HEYZO", "PONDO", "CARIB", "OKYOHOT", "LUXU", "MIUM",
)

# sample 文件识别（仿 scanner.py::COVER_NAMES，单独维护因为 scanner 不跟踪 sample）
_SAMPLE_RE = re.compile(r"^sample_(\d+)\.jpg$", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# 缺失检查
# --------------------------------------------------------------------------- #
@dataclass
class BackfillPlan:
    """单部影片缺失检查的结果。"""

    folder: Path
    carid: str
    title: str
    has_video: bool
    has_nfo: bool
    has_poster: bool
    has_fanart: bool
    sample_count: int

    @property
    def missing_kinds(self) -> List[str]:
        """缺失的文件种类（按 ``['nfo', 'poster', 'fanart', 'samples']`` 顺序）。"""
        kinds: List[str] = []
        if not self.has_nfo:
            kinds.append("nfo")
        if not self.has_poster:
            kinds.append("poster")
        if not self.has_fanart:
            kinds.append("fanart")
        if self.sample_count == 0:
            kinds.append("samples")
        return kinds

    @property
    def needs_backfill(self) -> bool:
        """是否需要补齐：必须有视频文件 + 至少缺一种目标文件。"""
        return self.has_video and bool(self.missing_kinds)

    @property
    def is_complete(self) -> bool:
        """所有目标文件都齐了（包含 ``sample_count > 0``）。"""
        return (
            self.has_video
            and self.has_nfo
            and self.has_poster
            and self.has_fanart
            and self.sample_count > 0
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "folder": str(self.folder),
            "carid": self.carid,
            "title": self.title,
            "has_video": self.has_video,
            "has_nfo": self.has_nfo,
            "has_poster": self.has_poster,
            "has_fanart": self.has_fanart,
            "sample_count": self.sample_count,
            "missing_kinds": list(self.missing_kinds),
            "needs_backfill": self.needs_backfill,
            "is_complete": self.is_complete,
        }


def check_missing(folder: Path) -> Optional[BackfillPlan]:
    """扫描单个影片目录，返回缺失检查结果。

    非影片目录 / 排除厂牌 / 文件夹名无法解析为车牌 → 返回 None。

    复用 :func:`scan_movie_folder` 的 NFO / 视频 / poster / fanart 识别；
    额外 glob ``sample_*.jpg`` 统计数量（scanner 不单独跟踪 sample 字段）。
    """
    entry = scan_movie_folder(folder)
    if entry is None:
        return None

    # 排除无车牌前缀的厂牌（JAVBus 上没页面）
    carid_upper = entry.carid.upper()
    if any(carid_upper.startswith(p) for p in EXCLUDED_CAR_PREFIXES):
        return None

    sample_count = 0
    try:
        for p in folder.iterdir():
            if p.is_file() and _SAMPLE_RE.match(p.name):
                sample_count += 1
    except OSError as e:
        logger.debug(f"无法列目录 {folder}：{e}")

    return BackfillPlan(
        folder=folder,
        carid=entry.carid,
        title=(entry.title or folder.name).strip(),
        has_video=entry.has_video,
        has_nfo=entry.has_nfo,
        has_poster=entry.has_poster,
        has_fanart=entry.has_fanart,
        sample_count=sample_count,
    )


def iter_movie_folders(root: Path) -> Iterator[Path]:
    """递归扫描 root 下所有「含视频文件」的目录（按 walker 顺序）。

    仿 :func:`scanner.scan_library` 的 walk 策略，但只跟踪视频存在性（更轻）。
    隐藏目录（``.`` 开头）跳过。
    """
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        if any(f.lower().endswith(tuple(VIDEO_EXTENSIONS)) for f in files):
            yield Path(d)


# --------------------------------------------------------------------------- #
# 补齐
# --------------------------------------------------------------------------- #
async def backfill_one(
    folder: Path,
    *,
    javbus_proxy: Optional[str] = None,
    cover_url: Optional[str] = None,
    on_progress: Optional[Callable[[str, str], None]] = None,
    timeout_seconds: int = 180,
    overwrite_nfo: Optional[bool] = None,
    download_samples: Optional[bool] = None,
    plan: Optional[BackfillPlan] = None,
    session: Optional["AsyncDynamicSession"] = None,
) -> Dict[str, Any]:
    """补齐单个影片目录下的缺失文件。

    调 :class:`MovieExporter`（output_root = folder.parent），让其在
    ``<parent>/<CARID> <title>/`` 下重新走完 JAVBus 抓取 + 文件落地流程。
    MovieExporter 内部的 cover / poster / fanart / samples ``.exists()`` 检查
    + ``overwrite_nfo`` 开关保证**绝不覆写已有文件**。

    Args:
        folder: 目标影片目录，绝对路径。
        javbus_proxy: JAVBus 抓取代理（None 走 ``.env`` 配置）。
        cover_url: JAVLibrary 缩略图 URL，传了会下载 poster.jpg。
        on_progress: 可选回调 ``(carid, status)``，status ∈ ``{"ok", "failed"}``。
        timeout_seconds: 单部抓取超时秒数，默认 180。
        overwrite_nfo: 显式控制 NFO 覆写（None 时自动：缺失才覆写）。
        download_samples: 显式控制 sample 下载（None 时自动：缺失才下）。
        plan: 已计算好的 BackfillPlan（性能优化：caller 多次复用同一目录时
            避免重复 ``check_missing``；None 时内部会自行计算一次）。

    Returns:
        ``{
            "code": str | None,            # 车牌（None = 非影片目录）
            "skipped": bool,
            "skipped_reason": str | None,  # "complete" / "no_video" / "no_carid_or_excluded"
            "failed": bool,
            "error": str | None,
            "stats": dict | None,          # MovieExporter.export_movies 返回值
            "plan_before": dict | None,    # 补齐前 check_missing 结果
            "plan_after": dict | None,     # 补齐后 check_missing 结果
        }``
    """
    if plan is None:
        plan = check_missing(folder)
    if plan is None:
        return {
            "code": None,
            "skipped": True,
            "skipped_reason": "no_carid_or_excluded",
            "failed": False,
            "error": None,
            "stats": None,
            "plan_before": None,
            "plan_after": None,
        }

    if not plan.has_video:
        return {
            "code": plan.carid,
            "skipped": True,
            "skipped_reason": "no_video",
            "failed": False,
            "error": None,
            "stats": None,
            "plan_before": plan.to_dict(),
            "plan_after": plan.to_dict(),
        }

    if plan.is_complete:
        return {
            "code": plan.carid,
            "skipped": True,
            "skipped_reason": "complete",
            "failed": False,
            "error": None,
            "stats": None,
            "plan_before": plan.to_dict(),
            "plan_after": plan.to_dict(),
        }

    code = plan.carid
    parent = folder.parent
    # 自动策略：仅在缺失时启用覆写 / 下载，避免覆盖已有文件
    if overwrite_nfo is None:
        overwrite_nfo = not plan.has_nfo
    if download_samples is None:
        download_samples = plan.sample_count == 0

    try:
        exporter = MovieExporter(
            output_root=parent,
            move_video=False,
            download_samples=download_samples,
            collect_magnets=False,
            javlibrary_proxy=javbus_proxy,
            bucket_by_month=False,
            overwrite_nfo=overwrite_nfo,
        )
    except Exception as e:  # noqa: BLE001
        return {
            "code": code,
            "skipped": False,
            "failed": True,
            "error": f"构造 exporter 失败：{e}",
            "stats": None,
            "plan_before": plan.to_dict(),
            "plan_after": None,
        }

    cover_urls_dict = {code: cover_url} if cover_url else None

    # 在 async 上下文里直接 await（caller 在 asyncio.run 内调本函数）。
    # 全库补齐时 caller 共用一个 AsyncDynamicSession（不再每部重建 Chromium），
    # 每部省 3-5 秒。
    try:
        stats = await asyncio.wait_for(
            exporter.export_movies(
                [(code, "")],
                cover_urls=cover_urls_dict,
                on_progress=on_progress,
                session=session,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        return {
            "code": code,
            "skipped": False,
            "failed": True,
            "error": f"抓取超时（>{timeout_seconds}s）",
            "stats": None,
            "plan_before": plan.to_dict(),
            "plan_after": None,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "code": code,
            "skipped": False,
            "failed": True,
            "error": f"抓取异常：{e}",
            "stats": None,
            "plan_before": plan.to_dict(),
            "plan_after": None,
        }

    # 再 check 一次看补上了哪些（用于前端精确反馈）
    final_plan = check_missing(folder)
    return {
        "code": code,
        "skipped": False,
        "failed": stats["failed"] > 0,
        "error": None,
        "stats": stats,
        "plan_before": plan.to_dict(),
        "plan_after": final_plan.to_dict() if final_plan else None,
    }


async def backfill_library(
    root: Path,
    *,
    cover_urls: Optional[Dict[str, str]] = None,
    javbus_proxy: Optional[str] = None,
    on_progress: Optional[Callable[[str, str], None]] = None,
    on_per_movie: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    delay_seconds: float = 3.0,
    timeout_seconds: int = 180,
    max_count: Optional[int] = None,
    session: Optional["AsyncDynamicSession"] = None,
) -> Dict[str, Any]:
    """全库补齐：递归扫描 + 逐部调 :func:`backfill_one`。

    Args:
        root: 本地库根目录。
        cover_urls: ``{carid: javlibrary_cover_url, ...}``，用于下 poster.jpg。
        javbus_proxy: 抓取代理。
        on_progress: 进度回调 ``(carid, status)``，由 ``MovieExporter.export_movies``
            内部在 process_movie 完成后触发。
        on_per_movie: 每部 ``backfill_one`` 跑完后触发（不论 success/fail/skip），
            回调签名 ``on_per_movie(result: dict)``。``result`` 包含
            ``code / skipped / failed / stats / skipped_reason`` 等字段。
            **用于服务层实时更新进度计数**（避免 UI 永远显示 0/0）。
        cancel_event: 取消信号；调用方 ``.set()`` 即停止后续。
        delay_seconds: 每部抓取间隔（防被 JAVBus 封），默认 3 秒。
        timeout_seconds: 单部超时，默认 180 秒。
        max_count: 最多处理多少个需要补齐的目录（``None`` = 全跑）。
            达到上限后停止后续 needs_backfill 处理；已扫到的 skip 类不计。

    Returns:
        ``{
            "total": int,                  # 总影片目录数（含非影片）
            "needs_backfill": int,         # 需补齐的目录数（实际触发了多少）
            "skipped_complete": int,
            "skipped_no_video": int,
            "skipped_no_carid": int,
            "backfilled": int,             # 实际补齐成功数（stats.written >= 1）
            "failed": int,
            "cancelled": bool,
            "limit_reached": bool,         # 是否因 max_count 限制而提前停止
            "results": list[dict],         # 每部 backfill_one 返回值
        }``
    """
    results: List[Dict[str, Any]] = []
    counts = {
        "total": 0,
        "needs_backfill": 0,
        "skipped_complete": 0,
        "skipped_no_video": 0,
        "skipped_no_carid": 0,
        "backfilled": 0,
        "failed": 0,
    }

    cancelled = False
    limit_reached = False
    for folder in iter_movie_folders(root):
        if cancel_event and cancel_event.is_set():
            logger.info("用户取消，停止补齐")
            cancelled = True
            break
        counts["total"] += 1
        plan = check_missing(folder)
        if plan is None:
            counts["skipped_no_carid"] += 1
            continue
        if not plan.has_video:
            counts["skipped_no_video"] += 1
            continue
        if plan.is_complete:
            counts["skipped_complete"] += 1
            continue

        # needs_backfill 计数 + max_count 检查（在 backfill_one 之前，
        # 这样 limit 限制的是"触发补齐的部数"，skip 类目录不计入 limit）
        counts["needs_backfill"] += 1
        if max_count is not None and counts["needs_backfill"] > max_count:
            # 把这次循环的计数回退（不算 needs_backfill）
            counts["needs_backfill"] -= 1
            limit_reached = True
            logger.info(f"--limit={max_count} 已达到，停止后续")
            break

        cover_url = (cover_urls or {}).get(plan.carid)

        result = await backfill_one(
            folder,
            javbus_proxy=javbus_proxy,
            cover_url=cover_url,
            on_progress=on_progress,
            timeout_seconds=timeout_seconds,
            plan=plan,  # 复用上面已算的 plan，省一次 check_missing
            session=session,  # 全库补齐时共享 AsyncDynamicSession
        )
        results.append(result)

        if result["failed"]:
            counts["failed"] += 1
        elif result.get("stats", {}).get("written", 0) >= 1:
            counts["backfilled"] += 1

        # 每部完成后回调（用于服务层实时更新进度计数；前端轮询时不再显示 0/0）
        if on_per_movie is not None:
            try:
                on_per_movie(result)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"on_per_movie 回调异常：{e}")

        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

    counts["cancelled"] = cancelled
    counts["limit_reached"] = limit_reached
    logger.info(
        f"补齐完成：total={counts['total']}, "
        f"needs_backfill={counts['needs_backfill']}, "
        f"backfilled={counts['backfilled']}, "
        f"failed={counts['failed']}, "
        f"skipped_complete={counts['skipped_complete']}, "
        f"cancelled={cancelled}, limit_reached={limit_reached}"
    )
    return {**counts, "results": results}


__all__ = [
    "EXCLUDED_CAR_PREFIXES",
    "BackfillPlan",
    "check_missing",
    "iter_movie_folders",
    "backfill_one",
    "backfill_library",
]