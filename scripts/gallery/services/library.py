"""服务状态：合并原 ``GalleryApp`` 中的状态与单部刷新队列管理。

提供：
- ``load_movies`` — 从 JSON/CSV 加载影片列表
- ``normalize_path_for_compare`` — 用于比较本地库 root 是否一致
- ``GalleryState`` — 全局服务状态（movies / 当前任务 / 本地库索引 / 封面代理配置）
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from library_scanner import (
    LibraryIndex,
    ScanProgress,
    load_index,
    save_index,
    scan_library,
    scan_movie_folder,
)

from .jobs import RescanQueue, ScrapeJob

logger = logging.getLogger("gallery.library")

# 车牌正则（与原服务一致）：用于 URL 拼接安全
CARID_RE = re.compile(r"[A-Z0-9_-]{2,32}")
MAX_CODES_PER_JOB = 300


def normalize_path_for_compare(p: str) -> str:
    """规范化路径用于等价比较，处理 Windows 上的常见差异：

    - 大小写不敏感
    - 正反斜杠统一
    - 展开映射盘为 UNC
    - 去掉尾部分隔符
    """
    if not p:
        return ""
    try:
        return os.path.normcase(os.path.normpath(os.path.realpath(p))).rstrip("\\/")
    except OSError:
        return os.path.normcase(os.path.normpath(p)).replace("/", "\\").rstrip("\\/")


def load_movies(data_path: Path) -> List[Dict[str, str]]:
    """从 JSON 或 CSV 加载影片列表，按 code 去重（保留首次出现的顺序）。"""
    if not data_path.exists():
        alt = data_path.with_suffix(".csv" if data_path.suffix == ".json" else ".json")
        if alt.exists():
            logger.warning(f"{data_path.name} 不存在，改用 {alt.name}")
            data_path = alt
        else:
            raise FileNotFoundError(f"未找到数据文件：{data_path}")

    if data_path.suffix.lower() == ".csv":
        with open(data_path, "r", encoding="utf-8", newline="") as f:
            raw = list(csv.DictReader(f))
    else:
        with open(data_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):  # 兼容 {"movies": [...]} 形式
            raw = raw.get("movies", [])

    movies: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = (item.get("code") or "").strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        movies.append(
            {
                "code": code,
                "title": (item.get("title") or "").strip(),
                "id": (item.get("id") or "").strip(),
                "cover_url": (item.get("cover_url") or "").strip(),
            }
        )

    logger.info(f"已加载 {len(movies)} 部影片：{data_path}")
    return movies


# --------------------------------------------------------------------------- #
# 全局状态
# --------------------------------------------------------------------------- #
class GalleryState:
    """服务器共享状态：影片数据、当前任务、封面代理配置、本地库索引。"""

    def __init__(
        self,
        data_path: Path,
        output_dir: Path,
        image_proxy_mode: str,
        proxy: Optional[str],
        proxy_enabled: bool,
        user_agent: str,
        verify_ssl: bool,
        download_timeout: int,
        javbus_url: str,
        library_root: Optional[Path] = None,
        library_index_path: Optional[Path] = None,
    ):
        self.data_path = data_path
        self.output_dir = output_dir
        self.movies = load_movies(data_path)

        # 磁力抓取始终使用 PROXY；PROXY_ENABLED 仅保留给封面 auto 模式。
        self.proxy = proxy
        self.cover_proxy = proxy if proxy_enabled else None
        self.user_agent = user_agent
        self.verify_ssl = verify_ssl
        self.download_timeout = download_timeout
        self.javbus_url = javbus_url if javbus_url.endswith("/") else javbus_url + "/"

        # auto：PROXY_ENABLED=true 且配了 PROXY 才走服务端代理拉图
        if image_proxy_mode == "auto":
            self.image_proxy = bool(self.cover_proxy)
        else:
            self.image_proxy = image_proxy_mode == "on"
            if self.image_proxy:
                self.cover_proxy = proxy

        self.cover_cache_dir = output_dir / ".cover_cache"
        self.job: Optional[ScrapeJob] = None
        self._lock = threading.Lock()

        # ---- 本地影片库 ----
        self.library_root: Optional[Path] = library_root
        self.library_index_path: Path = library_index_path or (
            output_dir / "library_index.json"
        )
        self.library_index: LibraryIndex = LibraryIndex.empty()
        self.library_stats: Dict[str, Any] = {}
        self.library_scanned_at: Optional[str] = None
        self.scan_state: ScanProgress = ScanProgress()
        self._scan_lock = threading.Lock()
        self.rescan_queue: RescanQueue = RescanQueue(
            library_root_getter=lambda: self.library_root,
            javbus_url_getter=lambda: self.javbus_url,
            proxy_getter=lambda: self.proxy,
        )
        self.rescan_queue.set_on_complete(self._refresh_index_after_rescan)
        self.rescan_queue.start_worker()
        self._maybe_load_library_index()

    # ---- 本地库 -------------------------------------------------------- #
    def _maybe_load_library_index(self) -> None:
        """启动时尝试加载已有索引。失败或 root 不一致时不报错（等手动刷新）。"""
        if not self.library_root:
            return
        if not self.library_index_path.exists():
            return
        data = load_index(self.library_index_path)
        if data is None:
            return
        stored_root = data.get("root") or ""
        current_root = str(self.library_root)
        if stored_root and normalize_path_for_compare(stored_root) != normalize_path_for_compare(
            current_root
        ):
            logger.warning(
                f"索引 root 与当前配置不一致：\n"
                f"  索引里：{stored_root}\n"
                f"  当前：  {current_root}\n"
                f"标记为待重建。点击页面「刷新库」可强制重建。"
            )
            return
        self.library_index = LibraryIndex.from_dict(data)
        self.library_stats = data.get("stats", {}) or {}
        self.library_scanned_at = data.get("scanned_at")
        logger.info(
            f"已加载本地库索引：{len(self.library_index)} 部，"
            f"上次扫描 {self.library_scanned_at}"
        )

    def start_rescan(self) -> bool:
        """触发后台扫描。返回 True 表示已启动，False 表示已在运行。"""
        with self._scan_lock:
            if self.scan_state.is_running:
                return False
            new_state = ScanProgress()
            new_state.is_running = True  # 关键：立即标记，让后续调用看到
            self.scan_state = new_state
            threading.Thread(target=self._run_rescan, daemon=True).start()
            return True

    def _run_rescan(self) -> None:
        """后台线程：扫描 → 落盘 → 替换索引。"""
        if not self.library_root:
            self.scan_state.is_running = False
            return
        try:
            logger.info(f"开始后台扫描 {self.library_root} …")
            movies, stats = scan_library(self.library_root, progress=self.scan_state)
            canonical_root = Path(normalize_path_for_compare(str(self.library_root)))
            save_index(movies, stats, self.library_index_path, canonical_root)
            data = load_index(self.library_index_path)
            if data:
                self.library_index = LibraryIndex.from_dict(data)
                self.library_stats = data.get("stats", {}) or {}
                self.library_scanned_at = data.get("scanned_at")
            self.scan_state.is_complete = True
            logger.info(
                f"扫描完成：{len(self.library_index)} 部，"
                f"耗时 {stats.duration_seconds:.1f}s"
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"扫描失败：{e}")
            self.scan_state.error = str(e)
        finally:
            self.scan_state.is_running = False

    # ---- 单部刷新（队列逐个） -------------------------------------- #
    def enqueue_rescan_movie(self, carid: str) -> RescanJob:
        entry = self.library_index.get(carid)
        if entry is None:
            raise ValueError(f"本地库中未找到车牌 {carid}")
        if not self.library_root:
            raise RuntimeError("未配置 LIBRARY_ROOT")
        if not entry.has_video:
            raise ValueError(f"该目录下未找到视频文件：{entry.folder}")
        return self.rescan_queue.enqueue(carid, Path(entry.folder))

    def get_rescan_status(self) -> Dict[str, Any]:
        return self.rescan_queue.status_snapshot()

    def _refresh_index_after_rescan(self, folder: Path) -> None:
        try:
            entry = scan_movie_folder(folder)
            if entry is None:
                logger.warning(f"刷新后重扫失败：{folder}")
                return
            self.library_index.upsert(entry)
            logger.info(f"索引已更新：{entry.carid}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"刷新后更新索引异常：{e}")

    def is_within_library(self, path: Path) -> bool:
        if not self.library_root:
            return False
        try:
            target = path.resolve()
            root = self.library_root.resolve()
            return str(target).startswith(str(root))
        except OSError:
            return False

    # ---- 任务 ---------------------------------------------------------- #
    def start_job(self, codes: List[str], run_target) -> ScrapeJob:
        """``run_target`` 通常是 ``start_scrape_job``（注入以避免循环依赖）。"""
        with self._lock:
            if self.job is not None and self.job.status == "running":
                raise RuntimeError("已有抓取任务正在运行，请等待完成")
            job = ScrapeJob(_gen_job_id(), codes)
            self.job = job
        run_target(job)
        return job

    def get_job(self, job_id: str) -> Optional[ScrapeJob]:
        job = self.job
        return job if job is not None and job.id == job_id else None


def _gen_job_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]