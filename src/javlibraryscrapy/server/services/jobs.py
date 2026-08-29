"""任务管理：磁力抓取任务。

注：单部刷新队列（``RescanQueue`` / ``RescanJob``）已被「补齐缺失」接口取代，
对应逻辑在 ``server.services.library_backfill.LibraryBackfillService``。
本模块只保留磁力抓取任务状态机 + 日志 handler。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

# 注意：jobs_runner 单向 import 本模块（from .jobs import JobLogHandler, ScrapeJob），
# 顶层反向 ``from .jobs_runner import run_scrape_job`` 会让 jobs_runner 被半初始化的
# jobs 重新触发 import —— 见 :func:`start_scrape_job` 内的懒加载。

logger = logging.getLogger("gallery.jobs")

MAX_LOG_LINES = 800


# --------------------------------------------------------------------------- #
# 磁力抓取任务
# --------------------------------------------------------------------------- #
class ScrapeJob:
    """一次磁力抓取任务的状态容器（线程安全）。"""

    def __init__(self, job_id: str, codes: List[str]):
        self.id = job_id
        self.codes = codes
        self.status = "running"  # running | done | error
        self.error: Optional[str] = None
        self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.outputs: Dict[str, str] = {}
        self.logs: Deque[str] = deque(maxlen=MAX_LOG_LINES)
        # Q4：本地库已存在、被前端发起但服务端过滤掉的 codes
        self.skipped: List[str] = []
        # V2 增强：wanted 列表里已经持久化磁力的 codes——不跑 JavBus，但
        # 也要写进 magnets.json + magnets_links.txt，让 NAS 批量发送能拿到。
        # 元素结构同 ``results()`` 里 ok 项：``{code, status="ok", title, magnet, ...}``
        self.extra_cached: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._items: Dict[str, Dict[str, Any]] = {
            code: {"code": code, "status": "pending", "title": "", "magnet": None}
            for code in codes
        }

    def match_code(self, car_id: str) -> Optional[str]:
        """把 JAVBus 返回的车牌映射回请求列表中的 code。"""
        if not car_id:
            return None
        key = car_id.strip().upper()
        return key if key in self._items else None

    def mark(self, code: str, status: str, **fields: Any) -> None:
        with self._lock:
            item = self._items.get(code)
            if item is None:
                return
            item["status"] = status
            item.update(fields)

    def add_log(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)

    def results(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(self._items[code]) for code in self.codes]

    def finalize(self) -> None:
        """把仍未处理的条目标记为失败。"""
        with self._lock:
            for item in self._items.values():
                if item["status"] == "pending":
                    item["status"] = "failed"

    def snapshot(self) -> Dict[str, Any]:
        items = list(self.results())
        seen = {it["code"] for it in items}
        # V2 增强：extra_cached 也算"完成项"——前端 lastItems 需要它们来"📥 发送到NAS"
        for r in self.extra_cached:
            if r["code"] in seen:
                continue
            items.append(dict(r))
            seen.add(r["code"])
        finished = sum(1 for i in items if i["status"] != "pending")
        current = next((i["code"] for i in items if i["status"] == "pending"), None)
        with self._lock:
            logs = list(self.logs)
        return {
            "id": self.id,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "total": len(items),
            "finished": finished,
            "current": current if self.status == "running" else None,
            "succeeded": sum(1 for i in items if i["status"] == "ok"),
            "items": items,
            "logs": logs,
            "outputs": dict(self.outputs),
        }


class JobLogHandler(logging.Handler):
    """把爬虫的日志转发到任务状态里，供网页实时展示。"""

    def __init__(self, job: ScrapeJob):
        super().__init__(level=logging.INFO)
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stamp = datetime.now().strftime("%H:%M:%S")
            self.job.add_log(f"{stamp} {record.getMessage()}")
        except Exception:  # 日志本身不能拖垮任务
            pass


# --------------------------------------------------------------------------- #
# 启动 helper（从服务层调用）
# --------------------------------------------------------------------------- #
def start_scrape_job(
    job: ScrapeJob,
    magnets_index: Path,
    proxy: Optional[str],
    library_index: Any,
) -> threading.Thread:
    """在后台线程中执行磁力抓取；返回线程对象。

    ``magnets_index``：结果 JSON 的写入路径（来自 Settings.magnets_index / .env
    的 ``MAGNETS_INDEX``）；``magnets_links.txt`` 与之同目录派生。

    ``run_scrape_job`` 定义在 :mod:`jobs_runner`，那里单向 import 本模块
    （``from .jobs import JobLogHandler, ScrapeJob``）。如果本模块顶层再
    ``from .jobs_runner import run_scrape_job``，jobs_runner 会被部分
    初始化的 jobs 重新触发 import，半成品对象传给 Spider 会炸。

    因此 jobs_runner 在函数体内懒加载 —— 调用 start_scrape_job 时两个
    模块都已完全初始化，避免循环。
    """
    from .jobs_runner import run_scrape_job  # noqa: PLC0415  懒加载避免循环
    t = threading.Thread(
        target=run_scrape_job,
        args=(job, magnets_index, proxy, library_index),
        daemon=True,
    )
    t.start()
    return t