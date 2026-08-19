"""Wanted 服务：JAVLibrary Most Wanted 的状态 + 后台刷新任务管理。

设计：
- 单实例运行：同一时刻只允许一个刷新任务（再触发返回 already_running）
- ``movie_list`` 在内存缓存 + 磁盘持久化（``output/javlibrary_movies.json``）
  启动时从磁盘加载，运行中由 ``refresh_wanted`` 改写
- 按月分桶：``_bucket`` 字段（YYYY-MM）由 ``release_date`` 推导
  没有 release_date 的归入 ``unknown``，前端在 UI 单独显示
"""

from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .wanted_refresh import (
    WantedRefreshJob,
    _bucket_for_release_date,
    new_job,
    refresh_wanted,
)

logger = logging.getLogger("gallery.wanted")

__all__ = ["WantedService", "WantedRefreshJob", "_bucket_for_release_date"]


class WantedService:
    """Wanted 状态 + 后台任务管理。

    ``javlibrary_proxy``：JAVLibrary 镜像（c99i.com）抓取时使用的代理。
    当前默认 None——c99i.com 直连；将来若换镜像需要代理再设。
    ``javbus_proxy``：JavBus 详情抓取时使用的代理（JavBus 需代理绕过
    Cloudflare）；与磁力抓取（GalleryState）共用 ``settings.proxy``。
    """

    def __init__(
        self,
        data_path: Path,
        javlibrary_proxy: Optional[str] = None,
        javbus_proxy: Optional[str] = None,
    ):
        self.data_path = Path(data_path)
        self.javlibrary_proxy = javlibrary_proxy
        self.javbus_proxy = javbus_proxy
        self._lock = threading.Lock()
        self._movies: List[Dict[str, Any]] = []
        self.job: Optional[WantedRefreshJob] = None
        self._thread: Optional[threading.Thread] = None
        self._loaded_at: Optional[str] = None
        self.reload()

    # ---- 加载 / 持久化 ----
    def reload(self) -> None:
        """从磁盘重新加载（覆盖内存列表）。"""
        with self._lock:
            if not self.data_path.exists():
                self._movies = []
                self._loaded_at = None
                return
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"wanted JSON 加载失败：{e}")
                self._movies = []
                self._loaded_at = None
                return
            # 兜底：补 _bucket
            for entry in raw:
                if "_bucket" not in entry and "release_date" in entry:
                    rd = entry.get("release_date") or ""
                    entry["_bucket"] = rd[:7] if (len(rd) >= 7 and rd[4] == "-") else "unknown"
            self._movies = raw
            self._loaded_at = datetime.now().isoformat(timespec="seconds")
            logger.info(f"wanted 已加载 {len(self._movies)} 部（{self.data_path}）")

    def save(self, movies: List[Dict[str, Any]]) -> None:
        """原子写。``refresh_wanted`` 内部用，外部不直接调用。"""
        with self._lock:
            self._movies = movies
            self._loaded_at = datetime.now().isoformat(timespec="seconds")
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.data_path.with_suffix(self.data_path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(movies, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.data_path)

    # ---- 列表查询 ----
    def list(
        self,
        month: str = "",
        page: int = 1,
        size: int = 60,
        include_missing: bool = True,
    ) -> Dict[str, Any]:
        """按月份筛选 + 分页。同时返回 ``months`` 列表（月份桶摘要）。"""
        with self._lock:
            movies = list(self._movies)

        # 计算月份桶摘要
        bucket_counter: Counter = Counter()
        for m in movies:
            if m.get("missing_in_remote") and not include_missing:
                continue
            bkt = m.get("_bucket") or "unknown"
            if bkt == "":
                bkt = "unknown"
            bucket_counter[bkt] += 1
        months = [
            {"month": b, "count": c}
            for b, c in sorted(
                bucket_counter.items(),
                key=lambda x: (x[0] == "unknown", x[0]),
                reverse=True,
            )
        ]

        # 月份筛选
        if month:
            movies = [m for m in movies if (m.get("_bucket") or "unknown") == month]
        if not include_missing:
            movies = [m for m in movies if not m.get("missing_in_remote")]

        # 按 release_date 倒序（最新在前）
        movies.sort(key=lambda m: (m.get("release_date") or ""), reverse=True)

        total = len(movies)
        page = max(1, page)
        size = min(200, max(1, size))
        start = (page - 1) * size
        page_items = movies[start:start + size]

        # 统计整个集合里 missing_in_remote 的总数（不依赖月份筛选）
        with self._lock:
            missing_count = sum(1 for m in self._movies if m.get("missing_in_remote"))

        return {
            "months": months,
            "items": page_items,
            "total": total,
            "page": page,
            "size": size,
            "month": month,
            "missing_in_remote_count": missing_count,
        }

    # ---- 任务管理 ----
    def start_refresh(
        self,
        max_pages: Optional[int] = None,
    ) -> Dict[str, Any]:
        """启动后台刷新任务。

        返回：``{"job_id": str, "is_already_running": bool}``
        """
        with self._lock:
            if self.job is not None and self.job.status == "running":
                return {
                    "job_id": self.job.id,
                    "is_already_running": True,
                }
            job = new_job()
            self.job = job
            t = threading.Thread(
                target=refresh_wanted,
                args=(self.data_path, self.javlibrary_proxy, self.javbus_proxy, job),
                kwargs={"max_pages": max_pages, "on_complete": self.reload},
                daemon=True,
            )
            self._thread = t
            t.start()
            return {
                "job_id": job.id,
                "is_already_running": False,
            }

    def get_refresh_status(self) -> Optional[Dict[str, Any]]:
        """当前任务快照；没有时返回 None。"""
        with self._lock:
            if self.job is None:
                return None
            return self.job.snapshot()

    def get_data_path(self) -> Path:
        return self.data_path