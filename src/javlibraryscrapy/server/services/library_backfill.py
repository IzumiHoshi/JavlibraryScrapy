"""本地库「补齐缺失文件」后台服务。

把 :func:`javlibraryscrapy.library.backfill.backfill_library` 包装成 FastAPI
gallery 可用的后台任务：

- :class:`BackfillJob` — 线程安全状态机（snapshot / is_running / 进度计数）
- :class:`LibraryBackfillService` — 持有 ``GalleryState`` + ``WantedService``，
  启动后台线程跑全库补齐；同一时刻只允许一个任务（重复触发抛 RuntimeError，
  route 层翻译成 409）

依赖注入：
    ``gallery_state.library_root`` —— 扫描目标
    ``gallery_state.proxy`` —— JAVBus 抓取代理
    ``wanted_service`` —— 提供 cover_url（poster.jpg 下载）

构造时机：
    ``server/app.py:create_app`` 里 ``wanted`` 构造之后、
    ``register_routes(app)`` 之前。挂到 ``app.state.library_backfill``。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from javlibraryscrapy.library.backfill import backfill_library
from scrapling.fetchers import AsyncDynamicSession

if TYPE_CHECKING:
    from .library import GalleryState
    from .wanted import WantedService

logger = logging.getLogger("gallery.library_backfill")


# --------------------------------------------------------------------------- #
# Job
# --------------------------------------------------------------------------- #
@dataclass
class BackfillJob:
    """一次补齐任务的状态容器。

    字段命名与 :class:`WantedRefreshJob` 对齐，便于前端轮询代码复用。
    """

    id: str
    started_at: str
    status: str = "running"  # running | done | error
    phase: str = "init"      # init | scanning | backfilling | done
    error: Optional[str] = None
    finished_at: Optional[str] = None

    # 计数（最终态填齐）
    total: int = 0
    needs_backfill: int = 0
    skipped_complete: int = 0
    skipped_no_video: int = 0
    skipped_no_carid: int = 0
    backfilled: int = 0
    failed: int = 0
    cancelled: bool = False

    # 当前处理的车牌 + folder（前端轮询显示）
    current_code: Optional[str] = None
    current_folder: Optional[str] = None

    # 失败清单（最多 50 条）
    failed_codes: List[Dict[str, str]] = field(default_factory=list)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "phase": self.phase,
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "total": self.total,
                "needs_backfill": self.needs_backfill,
                "skipped_complete": self.skipped_complete,
                "skipped_no_video": self.skipped_no_video,
                "skipped_no_carid": self.skipped_no_carid,
                "backfilled": self.backfilled,
                "failed": self.failed,
                "cancelled": self.cancelled,
                "current_code": self.current_code,
                "current_folder": self.current_folder,
                "failed_codes": list(self.failed_codes),
            }

    def _set(self, **fields: Any) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(self, k, v)

    def add_failed(self, code: Optional[str], error: str) -> None:
        with self._lock:
            self.failed_codes.append({"code": code or "", "error": error})
            if len(self.failed_codes) > 50:
                self.failed_codes = self.failed_codes[-50:]


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class LibraryBackfillService:
    """本地库「补齐缺失文件」后台服务。

    单实例运行；启动后由 daemon 线程跑 :func:`backfill_library`，通过
    ``on_progress`` 回调把当前车牌 / 状态推到 :class:`BackfillJob`。
    """

    def __init__(
        self,
        gallery_state: "GalleryState",
        wanted_service: "WantedService",
        *,
        delay_seconds: float = 3.0,
        timeout_seconds: int = 180,
    ):
        self.gallery_state = gallery_state
        self.wanted_service = wanted_service
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds

        self.job: Optional[BackfillJob] = None
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---- 状态查询 ---- #
    def is_running(self) -> bool:
        """是否正在跑（route 层据此返 409）。"""
        job = self.job
        return job is not None and job.status == "running"

    def get_status(self) -> Optional[Dict[str, Any]]:
        return self.job.snapshot() if self.job is not None else None

    # ---- 启停 ---- #
    def start(self) -> BackfillJob:
        """启动后台补齐任务。已有任务在跑则抛 RuntimeError。"""
        with self._lock:
            if self.is_running():
                raise RuntimeError("已有补齐任务正在运行")
            self._cancel_event.clear()
            job = BackfillJob(
                id=uuid.uuid4().hex[:12],
                started_at=datetime.now().isoformat(timespec="seconds"),
            )
            self.job = job
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="library-backfill",
            )
            self._thread.start()
            return job

    def cancel(self) -> None:
        """发取消信号。正在跑的后台线程会在当前部处理完 + 下一轮 ``cancel_event``
        检查时退出。"""
        self._cancel_event.set()

    # ---- 内部 ---- #
    def _cover_urls_from_wanted(self) -> Dict[str, str]:
        """从 :class:`WantedService` 内存里抽 ``{carid: cover_url}``。

        直接读 :attr:`WantedService._movies`：该字段在 ``reload`` / ``save`` 时
        整体替换（immutable 替换语义），持锁读取 list snapshot 即可。无需调用
        公开 API（避免每个 code 一次锁获取的 N² 开销）。
        """
        ws = self.wanted_service
        with ws._lock:
            return {
                (m.get("code") or "").strip().upper(): (m.get("cover_url") or "").strip()
                for m in ws._movies
                if (m.get("code") or "").strip() and (m.get("cover_url") or "").strip()
            }

    def _run(self) -> None:
        job = self.job
        assert job is not None
        if self.gallery_state.library_root is None:
            job._set(
                status="error",
                error="LIBRARY_ROOT 未配置",
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
            return

        root = self.gallery_state.library_root
        cover_urls = self._cover_urls_from_wanted()
        javbus_proxy = self.gallery_state.proxy

        # 立刻预估 needs_backfill（避免 UI 在 第一部跑完前一直显示 0/0）。
        # 直接用 gallery_state.library_index（已 scan 过的内存索引），**不**走
        # ``iter_movie_folders`` + ``check_missing``——那俩在 UNC 路径上慢得离谱
        # （1200 部 ~180 秒），会让 UI 卡 3 分钟才出预估数。
        #
        # 预估规则（与 BackfillPlan.is_complete 一致）：
        #   has_video and has_nfo and has_poster and has_fanart and sample_count > 0
        #   → complete（不计入 needs）
        # 任何一项缺失 → needs_backfill
        idx = self.gallery_state.library_index
        estimated_needs = 0
        estimated_complete = 0
        estimated_no_video = 0
        for entry in idx.values():
            if not entry.has_video:
                estimated_no_video += 1
            elif (
                entry.has_nfo
                and entry.has_poster
                and entry.has_fanart
                and entry.sample_count > 0
            ):
                estimated_complete += 1
            else:
                estimated_needs += 1
        job._set(
            phase="backfilling",
            needs_backfill=estimated_needs,
            skipped_complete=estimated_complete,
            skipped_no_video=estimated_no_video,
            # skipped_no_carid 在 library_index 里没有（被 scanner 当成 invalid 跳过），
            # 跑完后由 stats 覆盖。
            skipped_no_carid=0,
        )

        # 实时进度计数（避免前端永远显示 0/0）。
        # needs_backfill 已在启动时预估并 set；这里**只**累加 backfilled/failed，
        # 不要 +1 needs_backfill，否则会和 backfilled 同步增长，UI 永远显示 "N-1/N"。
        live_counts = {
            "backfilled": 0,
            "failed": 0,
        }

        def _on_progress(carid: str, status: str) -> None:
            job._set(current_code=carid)

        def _on_per_movie(result: Dict[str, Any]) -> None:
            if result.get("failed"):
                live_counts["failed"] += 1
            elif result.get("stats", {}).get("written", 0) >= 1:
                live_counts["backfilled"] += 1
            job._set(
                backfilled=live_counts["backfilled"],
                failed=live_counts["failed"],
            )
            # 每部完成后增量更新索引（避免 GUI 显示陈旧 has_* 状态）。
            # 跳过 skipped 类（无文件改动）；只对实际触发了 backfill_one 的目录调
            # update_library_index_for_folder（仅调一次 scan_movie_folder，几 ms）。
            if not result.get("skipped"):
                plan_before = result.get("plan_before") or {}
                folder_str = plan_before.get("folder")
                if folder_str:
                    try:
                        self.gallery_state.update_library_index_for_folder(
                            Path(folder_str)
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"更新索引失败 {folder_str}: {e}")

        try:
            # 在 async 上下文里建 1 个 AsyncDynamicSession 跑完整个 batch，
            # 每部省 3-5 秒 Chromium 重建（1157 部约节省 1 小时）。
            # 整个 asyncio.run 内所有部共享 cookies + 连接池。
            asyncio.run(self._run_async(
                root, cover_urls, javbus_proxy,
                on_progress=_on_progress,
                on_per_movie=_on_per_movie,
            ))
        except Exception as e:  # noqa: BLE001
            logger.error(f"补齐任务异常：{e}")
            job._set(
                status="error",
                error=str(e),
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )

    async def _run_async(
        self,
        root: Path,
        cover_urls: Dict[str, str],
        javbus_proxy: Optional[str],
        *,
        on_progress: Any,
        on_per_movie: Any,
    ) -> None:
        # 从 gallery_state 读 JAVBus session 配置（与 .env 同步）
        gs = self.gallery_state
        async with AsyncDynamicSession(
            load_dom=gs.proxy is not None or True,  # 保持与父类 __init__ 一致行为
            network_idle=True,
            disable_resources=True,
            proxy=javbus_proxy,
            headless=True,
            timeout=int(os.getenv("SCRAPLING_TIMEOUT", "30000")),
        ) as session:
            stats = await backfill_library(
                root,
                cover_urls=cover_urls,
                javbus_proxy=javbus_proxy,
                on_progress=on_progress,
                on_per_movie=on_per_movie,
                cancel_event=self._cancel_event,
                delay_seconds=self.delay_seconds,
                timeout_seconds=self.timeout_seconds,
                session=session,
            )

        failed_count = sum(1 for r in stats["results"] if r["failed"])
        self.job._set(
            status="done",
            phase="done",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            total=stats["total"],
            needs_backfill=stats["needs_backfill"],
            skipped_complete=stats["skipped_complete"],
            skipped_no_video=stats["skipped_no_video"],
            skipped_no_carid=stats["skipped_no_carid"],
            backfilled=stats["backfilled"],
            failed=failed_count,
            cancelled=stats.get("cancelled", False),
            current_code=None,
            current_folder=None,
        )
        for r in stats["results"]:
            if r["failed"]:
                self.job.add_failed(r.get("code"), r.get("error", ""))


__all__ = ["LibraryBackfillService", "BackfillJob"]