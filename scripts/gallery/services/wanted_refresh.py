"""Wanted 后台刷新 pipeline。

流程（一次 ``refresh_wanted`` 调用）：
    1. 拉取 JAVLibrary Most Wanted 所有页（默认 ``max_pages=None``，整站抓）
    2. 与本地 ``output/javlibrary_movies.json`` 做增量合并：
         - 新增车牌 → 追加，标记 ``_status=pending`` 等 JavBus 抓详情
         - 已有车牌 → 更新 ``title`` / ``cover_url``（如果变了），保留 ``release_date``
         - 远端已不见 → 标记 ``missing_in_remote=true``（**不删**，保留历史）
    3. 对所有 ``_status=pending`` 的车牌逐个调 ``JavbusSpider.parse``，
       把 ``release_date``、``actors`` 等填回 JSON；失败的标 ``_status=failed``
    4. 落盘 ``output/javlibrary_movies.json``（原子写：``.tmp`` → rename）

并发：
    - 单实例运行（同一时刻只允许一个后台任务；旧任务运行中再触发返回 409）
    - 后台线程跑，``asyncio.run`` 在线程内的事件循环里跑二期 IO
    - 进度写到 ``WantedService.job``（线程安全）

进度指标（``job.snapshot()``）：
    - ``phase``：one of "fetch_wanted" / "merge" / "fetch_javbus" / "save" / "done" / "error"
    - ``wanted_total``：拉到的远端条目数
    - ``wanted_added`` / ``wanted_updated``：merge 新增 / 更新条目数
    - ``javbus_total`` / ``javbus_done`` / ``javbus_failed``：二期抓取进度
    - ``current_code``：正在抓的车牌（前端轮询展示）
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("gallery.wanted_refresh")


# 月份桶键格式：YYYY-MM。``release_date`` 通常是 ``"2023-07-22"`` 这种，
# 取前 7 位即可。如果 release_date 无法解析（空串、非标准格式），
# 该影片归入 ``_bucket="unknown"`` —— 前端可在 UI 中显示"日期未知"。
_BUCKET_RE = re.compile(r"^(\d{4})-(\d{2})")


def _bucket_for_release_date(release_date: Optional[str]) -> str:
    """把 ``"2023-07-22"`` 变成 ``"2023-07"``；无法解析返回 ``"unknown"``。"""
    if not release_date:
        return "unknown"
    m = _BUCKET_RE.match(release_date.strip())
    return f"{m.group(1)}-{m.group(2)}" if m else "unknown"


# --------------------------------------------------------------------------- #
# Job（线程安全）
# --------------------------------------------------------------------------- #
@dataclass
class WantedRefreshJob:
    """一次刷新任务的状态容器。"""

    id: str
    started_at: str
    status: str = "running"  # running | done
    phase: str = "fetch_wanted"
    error: Optional[str] = None
    finished_at: Optional[str] = None
    # fetch_wanted
    wanted_total: int = 0
    wanted_pages_done: int = 0
    # merge
    wanted_added: int = 0
    wanted_updated: int = 0
    wanted_marked_missing: int = 0
    # fetch_javbus
    javbus_total: int = 0
    javbus_done: int = 0
    javbus_failed: int = 0
    current_code: Optional[str] = None
    logs: List[str] = field(default_factory=list)

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
                "wanted_total": self.wanted_total,
                "wanted_pages_done": self.wanted_pages_done,
                "wanted_added": self.wanted_added,
                "wanted_updated": self.wanted_updated,
                "wanted_marked_missing": self.wanted_marked_missing,
                "javbus_total": self.javbus_total,
                "javbus_done": self.javbus_done,
                "javbus_failed": self.javbus_failed,
                "current_code": self.current_code,
                "logs": list(self.logs),
            }

    def add_log(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)
            if len(self.logs) > 200:
                self.logs = self.logs[-200:]

    def _set(self, **fields: Any) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(self, k, v)


# --------------------------------------------------------------------------- #
# 增量合并
# --------------------------------------------------------------------------- #
@dataclass
class MergeResult:
    added: int = 0
    updated: int = 0
    marked_missing: int = 0
    needs_javbus: List[Dict[str, Any]] = field(default_factory=list)


def merge_wanted(
    remote: List[Dict[str, Any]],
    local: List[Dict[str, Any]],
) -> MergeResult:
    """合并远端 Most Wanted 与本地列表，返回需要 JavBus 详情抓取的车牌。

    关键不变量：
        - ``local`` 会被就地修改（更新 / 标记 missing）
        - 远端不存在但本地存在的车牌 → 不删，标记 ``missing_in_remote=true``
        - 已存在的 ``release_date`` 永远不会被覆盖（即便远端 title 变了）
    """
    by_code: Dict[str, Dict[str, Any]] = {}
    for entry in local:
        code = (entry.get("code") or "").strip().upper()
        if code:
            by_code[code] = entry

    result = MergeResult()
    seen_codes: set[str] = set()

    for r in remote:
        code = (r.get("code") or "").strip().upper()
        if not code:
            continue
        seen_codes.add(code)
        existing = by_code.get(code)
        if existing is None:
            new_entry = {
                "id": (r.get("id") or "").strip(),
                "code": code,
                "title": (r.get("title") or "").strip(),
                "cover_url": (r.get("cover_url") or "").strip(),
                "release_date": "",           # 等待 JavBus 抓
                "_status": "pending",         # pending → ready / failed
                "_bucket": "unknown",
                "_seen_at": datetime.now().isoformat(timespec="seconds"),
                "_updated_at": datetime.now().isoformat(timespec="seconds"),
                "missing_in_remote": False,
            }
            by_code[code] = new_entry
            result.added += 1
            result.needs_javbus.append(new_entry)
        else:
            changed = False
            for field in ("id", "title", "cover_url"):
                new_val = (r.get(field) or "").strip()
                if new_val and existing.get(field) != new_val:
                    existing[field] = new_val
                    changed = True
            if existing.get("missing_in_remote"):
                existing["missing_in_remote"] = False
                changed = True
            if changed:
                existing["_updated_at"] = datetime.now().isoformat(timespec="seconds")
                result.updated += 1
            # 如果本地 release_date 还是空的，标记需要重抓
            if not existing.get("release_date"):
                existing["_status"] = "pending"
                result.needs_javbus.append(existing)

    for code, entry in by_code.items():
        if code not in seen_codes and not entry.get("missing_in_remote"):
            entry["missing_in_remote"] = True
            entry["_updated_at"] = datetime.now().isoformat(timespec="seconds")
            result.marked_missing += 1

    return result


# --------------------------------------------------------------------------- #
# 主编排
# --------------------------------------------------------------------------- #
def refresh_wanted(
    data_path: Path,
    javlibrary_proxy: Optional[str],
    javbus_proxy: Optional[str],
    job: WantedRefreshJob,
    *,
    max_pages: Optional[int] = None,
    on_complete: Optional[Callable[[], None]] = None,
) -> None:
    """在线程中运行：抓 Most Wanted → merge → 抓 JavBus → 落盘。

    - ``javlibrary_proxy``：传给 JAVLibrary 镜像（c99i.com 默认 None）
    - ``javbus_proxy``：传给 JavBus 详情抓取（绕过 Cloudflare）
    """
    try:
        from javlibrary_scrapling import JAVLibrarySpider
        from javbus_scrapling import JavbusSpider
        from scrapling.fetchers import AsyncDynamicSession
    except ImportError as e:
        job._set(status="done", phase="error", error=f"导入爬虫失败：{e}", finished_at=_now())
        return

    data_path = Path(data_path)
    # JAVLibrarySpider 自己的 ``proxy_enabled`` 字段仅控制 scrape 时是否走代理
    jl_proxy_enabled = javlibrary_proxy is not None

    def log(line: str) -> None:
        logger.info(line)
        job.add_log(line)

    try:
        # ---- Phase 1: 抓 Most Wanted ----
        job._set(phase="fetch_wanted", wanted_pages_done=0)
        log(f"开始抓 JAVLibrary Most Wanted（max_pages={max_pages}）…")

        wl_spider = JAVLibrarySpider(
            output_dir=data_path.parent,
            proxy=javlibrary_proxy,
        )
        wl_spider.proxy_enabled = jl_proxy_enabled

        # JAVLibrarySpider 实例本身没有 Scrapling config 属性（load_dom/headless 等）
        # —— 这些参数在它原 ``crawl()`` 里是硬编码的。我们也按相同硬编码走，
        # 这样行为和原 ``uv run javlibrary_scrapling.py`` 一致。
        async def _fetch_wanted() -> List[Dict[str, Any]]:
            remote: List[Dict[str, Any]] = []
            async with AsyncDynamicSession(
                load_dom=True,
                network_idle=True,
                disable_resources=False,
                proxy=wl_spider.proxy,
                headless=True,
                timeout=90000,
                stealth_mode=True,
            ) as session:
                total_pages = await wl_spider.get_page_count(session)
                if max_pages:
                    total_pages = min(max_pages, total_pages)
                job._set(wanted_total=total_pages)
                log(f"Most Wanted 总页数 {total_pages}")
                for page in range(1, total_pages + 1):
                    html = await wl_spider.fetch_page(session, page)
                    if html:
                        page_movies = wl_spider.parse_movies_from_html(html)
                        remote.extend(page_movies)
                    job._set(wanted_pages_done=page)
                    log(f"  第 {page}/{total_pages} 页，本页 {len(page_movies)} 部，累计 {len(remote)} 部")
                    await asyncio.sleep(3)
            return remote

        try:
            remote = asyncio.run(_fetch_wanted())
        except Exception as e:  # noqa: BLE001
            job._set(status="done", phase="error", error=f"抓 Most Wanted 失败：{e}", finished_at=_now())
            return

        # ---- Phase 2: merge ----
        job._set(phase="merge")
        log(f"开始增量合并（远端 {len(remote)} 部 vs 本地）")
        if data_path.exists():
            try:
                with open(data_path, "r", encoding="utf-8") as f:
                    local: List[Dict[str, Any]] = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log(f"读本地 JSON 失败，按空列表继续：{e}")
                local = []
        else:
            local = []

        result = merge_wanted(remote, local)
        job._set(
            wanted_added=result.added,
            wanted_updated=result.updated,
            wanted_marked_missing=result.marked_missing,
            javbus_total=len(result.needs_javbus),
        )
        log(
            f"合并：新增 {result.added}，更新 {result.updated}，"
            f"missing {result.marked_missing}，待抓 JavBus {len(result.needs_javbus)}"
        )

        # ---- Phase 3: 抓 JavBus ----
        if result.needs_javbus:
            job._set(phase="fetch_javbus")
            log("开始抓 JavBus 详情…")
            bus_spider = JavbusSpider(root_dir=data_path.parent)
            bus_spider.proxy_enabled = javbus_proxy is not None
            bus_spider.proxy = javbus_proxy

            async def _fetch_all() -> None:
                async with AsyncDynamicSession(
                    load_dom=bus_spider.load_dom,
                    network_idle=bus_spider.network_idle,
                    disable_resources=bus_spider.disable_resources,
                    proxy=bus_spider.proxy,
                    headless=bus_spider.headless,
                    timeout=bus_spider.timeout,
                ) as session:
                    for entry in result.needs_javbus:
                        code = entry["code"]
                        job._set(current_code=code)
                        log(f"  → {code}")
                        try:
                            url = f"{bus_spider.javbus_url}{code}"
                            page = await session.fetch(url)
                            info = await bus_spider.parse(page)
                            if info and isinstance(info, dict):
                                entry["release_date"] = (info.get("release_date") or "").strip()
                                entry["actors"] = (info.get("actors") or "").strip()
                                entry["producer"] = (info.get("producer") or "").strip()
                                entry["publisher"] = (info.get("publisher") or "").strip()
                                entry["category"] = (info.get("category") or "").strip()
                                entry["_bucket"] = _bucket_for_release_date(entry["release_date"])
                                entry["_status"] = "ready"
                                log(f"    ✓ {code} → {entry['_bucket']}（{entry['release_date'][:10]}）")
                            else:
                                entry["_status"] = "failed"
                                job._set(javbus_failed=job.javbus_failed + 1)
                                log(f"    ✗ {code} 解析为空")
                        except Exception as e:  # noqa: BLE001
                            entry["_status"] = "failed"
                            job._set(javbus_failed=job.javbus_failed + 1)
                            log(f"    ✗ {code} 异常：{e}")
                        finally:
                            job._set(javbus_done=job.javbus_done + 1)
                            await asyncio.sleep(1.5)

            try:
                asyncio.run(_fetch_all())
            except Exception as e:  # noqa: BLE001
                log(f"批量抓 JavBus 出错（已抓到的会保留）：{e}")

        job._set(current_code=None)

        # ---- Phase 4: 落盘 ----
        job._set(phase="save")
        for entry in local:
            if "release_date" in entry and "_bucket" not in entry:
                entry["_bucket"] = _bucket_for_release_date(entry.get("release_date"))
        data_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = data_path.with_suffix(data_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(local, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(data_path)
        log(f"已写入 {data_path}（共 {len(local)} 部）")

        job._set(status="done", phase="done", finished_at=_now())
        log("✅ wanted refresh 完成")
        if on_complete:
            try:
                on_complete()
            except Exception:  # noqa: BLE001
                logger.exception("on_complete 回调失败")

    except Exception as e:  # noqa: BLE001
        logger.exception("wanted refresh 失败")
        job._set(status="done", phase="error", error=str(e), finished_at=_now())


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_job() -> WantedRefreshJob:
    """工厂：生成一个带 UUID 和时间戳的新 job。"""
    return WantedRefreshJob(
        id=uuid.uuid4().hex[:12],
        started_at=_now(),
    )