"""任务管理：磁力抓取任务 + 单部刷新队列。

并发模型与原 ``gallery_server.py`` 保持一致：
- 磁力抓取：每次启动一个后台 daemon 线程跑 ``asyncio.run``，通过 ``JobLogHandler``
  把爬虫日志转发到 ``ScrapeJob.logs``，供前端轮询。
- 单部刷新：FIFO 队列 + 单消费者 worker 线程。
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
# 单部刷新队列
# --------------------------------------------------------------------------- #
class RescanJob:
    """单条刷新任务的状态容器（线程安全）。"""

    def __init__(self, carid: str, folder: Path):
        self.carid = carid
        self.folder = Path(folder)
        self.status = "queued"  # queued | running | done | error
        self.error: Optional[str] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.title: str = ""
        self.samples_downloaded: int = 0
        self.logs: Deque[str] = deque(maxlen=200)
        self._lock = threading.Lock()

    def add_log(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            logs = list(self.logs)
        return {
            "carid": self.carid,
            "folder": str(self.folder),
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "title": self.title,
            "samples_downloaded": self.samples_downloaded,
            "logs": logs,
        }


class RescanQueue:
    """FIFO 单消费者队列：一个后台线程逐个处理刷新任务。"""

    def __init__(
        self,
        library_root_getter: Callable[[], Optional[Path]],
        javbus_url_getter: Callable[[], str],
        proxy_getter: Callable[[], Optional[str]],
    ):
        self._queue: Deque[RescanJob] = deque()
        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._current: Optional[RescanJob] = None
        self._on_complete: Optional[Callable[[Path], None]] = None
        # 通过 getter 在任务执行时拉取最新配置（避免启动后 .env 变更的 stale 引用）
        self._library_root_getter = library_root_getter
        self._javbus_url_getter = javbus_url_getter
        self._proxy_getter = proxy_getter

    def set_on_complete(self, cb: Callable[[Path], None]) -> None:
        """注册单条任务完成后的回调（用于重扫该目录更新索引）。"""
        self._on_complete = cb

    def start_worker(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()

    def enqueue(self, carid: str, folder: Path) -> RescanJob:
        job = RescanJob(carid, folder)
        with self._lock:
            self._queue.append(job)
            self._wakeup.set()
        return job

    def status_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            current = self._current.snapshot() if self._current else None
            queued = [
                {**j.snapshot(), "position": i + 1}
                for i, j in enumerate(self._queue)
            ]
        return {
            "active": current is not None,
            "current": current,
            "queued": queued,
            "total": (1 if current else 0) + len(queued),
        }

    def _worker(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._current = None
                else:
                    self._current = self._queue.popleft()
            if self._current is None:
                self._wakeup.wait(timeout=1.0)
                self._wakeup.clear()
                continue
            self._process_one(self._current)

    def _process_one(self, job: RescanJob) -> None:
        job.status = "running"
        job.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def log_cb(level: int, msg: str) -> None:
            stamp = datetime.now().strftime("%H:%M:%S")
            job.add_log(f"{stamp} [{logging.getLevelName(level)}] {msg}")

        # 延迟导入避免循环
        from javlibraryscrapy.library.refresher import refresh_library_movie

        library_root = self._library_root_getter()
        if library_root is None:
            job.status = "error"
            job.error = "未配置 LIBRARY_ROOT"
            return

        try:
            result = asyncio.run(
                refresh_library_movie(
                    folder=job.folder,
                    carid=job.carid,
                    javbus_url=self._javbus_url_getter(),
                    proxy=self._proxy_getter(),
                    log_callback=log_cb,
                )
            )
            if result.get("ok"):
                job.status = "done"
                job.title = result.get("title", "")
                job.samples_downloaded = int(result.get("samples_downloaded") or 0)
                if self._on_complete is not None:
                    try:
                        self._on_complete(job.folder)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"刷新后更新索引失败：{e}")
            else:
                job.status = "error"
                job.error = result.get("error", "未知错误")
        except Exception as e:  # noqa: BLE001
            job.status = "error"
            job.error = str(e)
        finally:
            job.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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