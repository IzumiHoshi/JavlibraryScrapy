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
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    # 仅类型提示；运行时仍是鸭子类型，避免循环导入。
    # sample_cache 位于 services/sample_cache.py，与本文件同层。
    from .sample_cache import SampleCountCache

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
    # 本地库落地（仅在 MOSTWANTED_LIBRARY_ROOT 设了时递增）
    local_saved: int = 0
    local_skipped: int = 0
    logs: List[str] = field(default_factory=list)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            remaining = max(0, self.javbus_total - self.javbus_done)
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
                "queue_length": remaining,    # 待抓 JavBus 的车牌数（含当前正在处理的）
                "current_code": self.current_code,
                "local_saved": self.local_saved,
                "local_skipped": self.local_skipped,
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
        - ``local`` 会被就地修改（更新 / 标记 missing + **追加新增**）
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
                "magnet": "",                 # 等待 JavBus 抓（与 release_date 同一调用；后续 scrape 不必再跑 JavBus）
                "_status": "pending",         # pending → ready / failed
                "_bucket": "unknown",
                "_seen_at": datetime.now().isoformat(timespec="seconds"),
                "_updated_at": datetime.now().isoformat(timespec="seconds"),
                "missing_in_remote": False,
            }
            by_code[code] = new_entry
            result.added += 1
            result.needs_javbus.append(new_entry)
            # 必须 append 到 local，否则 save_wanted_json(local) 永远不会把新车牌落盘
            local.append(new_entry)
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
# 本地库落地（Phase 3 之后）
# --------------------------------------------------------------------------- #
def _save_per_movie_folder(
    spider: "JavbusSpider",
    info: Dict[str, Any],
    code: str,
    root_dir: Path,
    sample_cache: Optional["SampleCountCache"] = None,
) -> Dict[str, int]:
    """把单部影片的 cover + samples 落地到 ``<root>/<CARID> <title>/``。

    返回 ``{"cover": 0/1, "samples": N}`` 用于上层统计。

    行为约定：
      - 文件夹不存在 → 创建
      - cover.jpg 已存在 → 跳过写入（幂等），但仍清理临时 ``<CARID>.png``
      - sample_NNN.jpg 已存在 → 跳过写入，清理临时 ``<CARID>_sample_NNN.jpg``
      - 任何一步失败不抛异常（让 Phase 3 主流程不受影响）
      - 落盘完成后，若 sample_cache 不为空且 folder 存在 → 写一条
        ``code -> (existing_count + new_samples, mtime)``，
        避免下次 /api/wanted 列表扫 NFS
    """
    result = {"cover": 0, "samples": 0}
    title = (info.get("title") or "").strip()
    if not title:
        return result

    folder = root_dir / f"{code} {title}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"建文件夹失败 {folder}: {e}")
        return result

    # cover
    cover_raw = info.get("cover")
    if cover_raw:
        cover_path = Path(cover_raw) if not isinstance(cover_raw, Path) else cover_raw
        dest = folder / "cover.jpg"
        if dest.exists():
            logger.debug(f"cover.jpg 已存在，跳过：{dest}")
            try:
                cover_path.unlink()
            except OSError:
                pass
        else:
            try:
                if cover_path.exists():
                    cover_path.rename(dest)
                    logger.info(f"已保存 cover.jpg：{code}")
                    result["cover"] = 1
                else:
                    logger.debug(f"cover 临时文件不存在：{cover_path}")
            except OSError as e:
                logger.warning(f"移动 cover 失败 {code}: {e}")

    # samples
    sample_urls = info.get("samples") or []
    if sample_urls:
        try:
            downloaded = spider.download_samples(sample_urls, code)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"调用 download_samples 失败 {code}: {e}")
            downloaded = []

        for i, src in enumerate(downloaded, start=1):
            dest = folder / f"sample_{i:03d}.jpg"
            if dest.exists():
                try:
                    src.unlink()
                except OSError:
                    pass
                continue
            try:
                if src.exists():
                    src.rename(dest)
                    result["samples"] += 1
            except OSError as e:
                logger.warning(f"移动樣品 {i} 失败 {code}: {e}")

        # 清理可能残留的 <CARID>_sample_NNN.jpg（download_samples 跳过/失败的）
        for leftover in spider.root_dir.glob(f"{code}_sample_*.jpg"):
            try:
                leftover.unlink()
            except OSError:
                pass

    # 落盘完成 → 回填 cache。这里直接 glob 一次拿到「本地已有」总数（含之前手动下载的）
    # 把 folder 传进去：cache 内部就不用再做 iterdir 查找（P0 优化）。
    if sample_cache is not None and folder.exists():
        try:
            existing = sum(1 for _ in folder.glob("sample_*.jpg"))
            sample_cache.put(code, existing, folder=folder)
        except OSError as e:
            logger.warning(f"回填 cache 失败 {code}: {e}")

    return result


# --------------------------------------------------------------------------- #
# 落盘（每次抓完一部后增量写；崩了只丢当前这一部）
# --------------------------------------------------------------------------- #
def save_wanted_json(
    local: List[Dict[str, Any]],
    data_path: Path,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    """原子写：``<data_path>.tmp`` → rename ``data_path``。

    - 每部 JavBus 抓完 + 落 folder 后调用一次（崩了 JSON 也有最近状态）
    - ``local`` 中所有 ``release_date`` 已填但缺 ``_bucket`` 的，自动补
    - 失败抛异常让上层捕获（不要静默吞）
    """
    data_path.parent.mkdir(parents=True, exist_ok=True)
    for entry in local:
        if "release_date" in entry and "_bucket" not in entry:
            entry["_bucket"] = _bucket_for_release_date(entry.get("release_date"))
    tmp = data_path.with_suffix(data_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(local, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(data_path)
    if log:
        log(f"已写入 {data_path.name}（{len(local)} 部）")


# --------------------------------------------------------------------------- #
# 主编排
# --------------------------------------------------------------------------- #
def refresh_wanted(
    data_path: Path,
    javlibrary_proxy: Optional[str],
    javbus_proxy: Optional[str],
    job: WantedRefreshJob,
    *,
    max_pages: Optional[int] = 2,
    on_complete: Optional[Callable[[], None]] = None,
    sample_cache: Optional["SampleCountCache"] = None,
) -> None:
    """在线程中运行：抓 Most Wanted → merge → 抓 JavBus → 落盘。

    - ``javlibrary_proxy``: 传给 JAVLibrary 镜像 (c99i.com 默认 None)
    - ``javbus_proxy``: 传给 JavBus 详情抓取 (绕过 Cloudflare)
    - ``sample_cache``: 外部注入的样本计数缓存；_save_per_movie_folder
      落盘成功后回写 (count)，避免下次扫描 NFS
    - ``max_pages``: 默认 2（Most Wanted 头部热度最高，2 页 ≈ 40 部够日常用）；
      传 None 显式抓全站
    """
    try:
        from javlibraryscrapy.scraping.javbus import JavbusSpider
        from javlibraryscrapy.scraping.javlibrary import JAVLibrarySpider
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

        # merge 完立刻落盘：新增/更新/missing 标记先写一次，Phase 3 崩了也比
        # 之前「全跑完再写」更接近当前状态
        try:
            save_wanted_json(local, data_path, log=log)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"merge 阶段落盘失败：{e}")
            log(f"⚠ merge 阶段落盘失败：{e}")

        # ---- Phase 3: 抓 JavBus ----
        if result.needs_javbus:
            job._set(phase="fetch_javbus")
            log("开始抓 JavBus 详情…")

            # 本地库落地：若 .env 配置了 MOSTWANTED_LIBRARY_ROOT，把 cover/樣品
            # 落到 <root>/<CARID> <title>/ 下；为空则跳过（保持原行为）。
            mw_root_str = os.getenv("MOSTWANTED_LIBRARY_ROOT", "").strip()
            mw_root: Optional[Path] = Path(mw_root_str) if mw_root_str else None
            if mw_root:
                log(
                    f"本地库落地：{mw_root}（每部 → <CARID> <title>/cover.jpg + sample_NNN.jpg）"
                )
                # 让 JavbusSpider 把 cover 临时 <CARID>.png 直接落到 mw_root，
                # 方便 _save_per_movie_folder 直接 rename（避免覆盖 output/）
                spider_root = mw_root
            else:
                spider_root = data_path.parent

            bus_spider = JavbusSpider(root_dir=spider_root)
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
                            # JavBus 对不存在的车牌返回 404，但 scrapling 仍返回
                            # Response 对象；parse() 拿到一个空的 div.info，
                            # 返回的 dict 里 release_date 是空串。原代码只看
                            # `if info and isinstance(info, dict)` 就标
                            # `_status="ready"`，导致 404 页被错误标记。
                            # 先看 HTTP 状态码 + release_date 是否真拿到了。
                            status_code = getattr(page, "status", None) or getattr(page, "status_code", None)
                            info = await bus_spider.parse(page)
                            if (
                                info
                                and isinstance(info, dict)
                                and (info.get("release_date") or "").strip()
                                and (status_code is None or status_code == 200)
                            ):
                                entry["release_date"] = (info.get("release_date") or "").strip()
                                entry["actors"] = (info.get("actors") or "").strip()
                                entry["producer"] = (info.get("producer") or "").strip()
                                entry["publisher"] = (info.get("publisher") or "").strip()
                                entry["category"] = (info.get("category") or "").strip()
                                entry["magnet"] = (info.get("magnet") or "").strip()
                                entry["_bucket"] = _bucket_for_release_date(entry["release_date"])
                                entry["_status"] = "ready"
                                log(f"    ✓ {code} → {entry['_bucket']}（{entry['release_date'][:10]}，magnet={'✓' if entry['magnet'] else '无'}）")
                                # 本地库落地
                                if mw_root:
                                    try:
                                        saved = _save_per_movie_folder(
                                            bus_spider, info, code, mw_root,
                                            sample_cache=sample_cache,
                                        )
                                        if saved["cover"] or saved["samples"]:
                                            job._set(local_saved=job.local_saved + 1)
                                            log(
                                                f"    💾 {code} → cover={saved['cover']}, "
                                                f"samples={saved['samples']}"
                                            )
                                        else:
                                            job._set(local_skipped=job.local_skipped + 1)
                                    except Exception as e:  # noqa: BLE001
                                        logger.warning(
                                            f"本地库落地异常 {code}: {e}"
                                        )
                                        job._set(local_skipped=job.local_skipped + 1)
                            else:
                                entry["release_date"] = ""
                                entry["_bucket"] = "unknown"
                                entry["_status"] = "failed"
                                job._set(javbus_failed=job.javbus_failed + 1)
                                reason = (
                                    f"http={status_code}" if status_code and status_code != 200
                                    else "无 release_date"
                                )
                                log(f"    ✗ {code} JavBus 抓取失败（{reason}）")
                        except Exception as e:  # noqa: BLE001
                            entry["_status"] = "failed"
                            job._set(javbus_failed=job.javbus_failed + 1)
                            log(f"    ✗ {code} 异常：{e}")
                        finally:
                            job._set(javbus_done=job.javbus_done + 1)
                            # 每部完成（无论成功/失败）就增量落盘：
                            # 服务崩 / 网络断 → JSON 反映「最近一次成功」状态，
                            # 不会停留在几十部前的 release_date
                            try:
                                save_wanted_json(local, data_path, log=None)
                            except Exception as save_err:  # noqa: BLE001
                                logger.warning(f"增量落盘失败 {code}: {save_err}")
                            await asyncio.sleep(1.5)

            try:
                asyncio.run(_fetch_all())
            except Exception as e:  # noqa: BLE001
                log(f"批量抓 JavBus 出错（已抓到的会保留）：{e}")

        job._set(current_code=None)

        # ---- Phase 3.5: 清理 failed/unknown 死条目 ----
        # JavBus 永久 404 的车牌（JUR-XXX/MIAB-XXX 等冷门厂牌）会留下
        # _status=failed + _bucket=unknown 的死条目——永远抓不到磁力，
        # 只会污染 unknown 月份桶计数。批量刷新完成后自动修剪。
        # 保留 pending / missing_in_remote / ready 三类条目（详见函数 docstring）。
        job._set(phase="cleanup")
        removed_count = cleanup_failed_unknown(local)
        if removed_count:
            log(f"🧹 自动清理 {removed_count} 部 failed/unknown 条目")

        # ---- Phase 4: 最终落盘 ----
        # 每部循环内已经增量写过一次；这里再写一次兜底（万一循环里 save 全失败）
        job._set(phase="save")
        try:
            save_wanted_json(local, data_path, log=log)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"最终落盘失败：{e}")
            log(f"⚠ 最终落盘失败：{e}")

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


# --------------------------------------------------------------------------- #
# 清理 failed/unknown 死条目
# --------------------------------------------------------------------------- #
def cleanup_failed_unknown(local: List[Dict[str, Any]]) -> int:
    """清理 ``_status=failed`` 且 ``_bucket=unknown`` 的死条目（in-place）。

    死条目的来源：JavBus 永久 404 / 无 release_date 的车牌（如 JUR-XXX、MIAB-XXX、
    部分冷门厂牌），批量刷新时会留下这种条目。它们永远不会成功抓取磁力，
    只会污染 unknown 月份桶计数；用户手动清空时也要点几十下。

    保留规则（**不会被清理**）：
    - ``_status=pending`` 的（刚加载还没抓完，留着等下次重抓 / 手动重试）
    - ``missing_in_remote=true`` 的（远端已下架但本地保留的历史条目）
    - ``_status=ready`` 的（成功抓取过，肯定有数据）

    调用时机：批量刷新完成后（JavBus 循环跑完，落盘前 in-place 修剪）。

    返回：移除的条目数（用于 log / CLI 输出）。
    """
    before = len(local)
    kept: List[Dict[str, Any]] = []
    removed_codes: List[str] = []
    for m in local:
        if (
            m.get("_status") == "failed"
            and (m.get("_bucket") or "unknown") == "unknown"
        ):
            removed_codes.append((m.get("code") or "?").strip() or "?")
        else:
            kept.append(m)
    local[:] = kept
    if removed_codes:
        preview = ", ".join(removed_codes[:20])
        suffix = (
            f" ... (+{len(removed_codes) - 20} more)"
            if len(removed_codes) > 20
            else ""
        )
        logger.info(
            f"清理 failed/unknown {len(removed_codes)} 条目: {preview}{suffix}"
        )
    return before - len(local)


def new_job() -> WantedRefreshJob:
    """工厂：生成一个带 UUID 和时间戳的新 job。"""
    return WantedRefreshJob(
        id=uuid.uuid4().hex[:12],
        started_at=_now(),
    )


# --------------------------------------------------------------------------- #
# 单车 JavBus 抓取（用于手动重试 / 增量添加）
# --------------------------------------------------------------------------- #
def scrape_one_javbus(
    code: str,
    javbus_proxy: Optional[str],
    *,
    mw_root: Optional[Path] = None,
    sample_cache: Optional["SampleCountCache"] = None,
) -> Dict[str, Any]:
    """同步入口：抓一个车牌的 JavBus 详情。

    在 **独立线程** 里跑 asyncio.run，避免与 uvicorn 的事件循环冲突
    （``asyncio.run() cannot be called from a running event loop``）。

    返回 dict（便于调用方直接 jsonify）：
        ``{
            "code": str,
            "ok": bool,                       # 是否拿到 release_date
            "info": dict | None,             # 原始 JavBus info（成功时）
            "status_code": int | None,       # HTTP 状态码（无法读取时 None）
            "error": str | None,             # 失败原因描述
            "saved": {cover, samples} | None # 本地库落地结果
        }``

    与 ``refresh_wanted`` 内 Phase 3 单部循环共用同一套判断：
    - 必须 ``info and isinstance(info, dict) and release_date.strip() and status==200``
    - 404 / 缺 release_date → ``ok=False``，由调用方写回 ``_status=failed``

    ``mw_root`` 非空时调用 ``_save_per_movie_folder`` 把 cover + samples 落到
    ``<root>/<CARID> <title>/`` 下，并回填 ``sample_cache`` 计数。
    """
    import concurrent.futures

    code = (code or "").strip().upper()
    if not code:
        return {"code": code, "ok": False, "error": "空车牌"}

    try:
        from javlibraryscrapy.scraping.javbus import JavbusSpider
        from scrapling.fetchers import AsyncDynamicSession
    except ImportError as e:
        return {"code": code, "ok": False, "error": f"导入爬虫失败：{e}"}

    # 让 JavbusSpider 把 cover 临时 <CARID>.png 直接落到 mw_root（如果设置），
    # 方便 _save_per_movie_folder 直接 rename 避免覆盖 output/；否则落到 JSON 同目录
    spider_root = mw_root if mw_root else Path(".")
    bus_spider = JavbusSpider(root_dir=spider_root)
    bus_spider.proxy_enabled = javbus_proxy is not None
    bus_spider.proxy = javbus_proxy

    async def _do() -> Dict[str, Any]:
        async with AsyncDynamicSession(
            load_dom=bus_spider.load_dom,
            network_idle=bus_spider.network_idle,
            disable_resources=bus_spider.disable_resources,
            proxy=bus_spider.proxy,
            headless=bus_spider.headless,
            timeout=bus_spider.timeout,
        ) as session:
            url = f"{bus_spider.javbus_url}{code}"
            try:
                page = await session.fetch(url)
            except Exception as e:  # noqa: BLE001
                return {"code": code, "ok": False, "error": f"网络异常：{e}"}
            status_code = getattr(page, "status", None) or getattr(page, "status_code", None)
            try:
                info = await bus_spider.parse(page)
            except Exception as e:  # noqa: BLE001
                return {
                    "code": code,
                    "ok": False,
                    "status_code": status_code,
                    "error": f"解析异常：{e}",
                }
            release_date = (info.get("release_date") or "").strip() if isinstance(info, dict) else ""
            if (
                info
                and isinstance(info, dict)
                and release_date
                and (status_code is None or status_code == 200)
            ):
                saved = None
                if mw_root:
                    try:
                        saved = _save_per_movie_folder(
                            bus_spider, info, code, mw_root,
                            sample_cache=sample_cache,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"本地库落地异常 {code}: {e}")
                        saved = None
                return {
                    "code": code,
                    "ok": True,
                    "info": info,
                    "status_code": status_code,
                    "saved": saved,
                }
            reason = (
                f"http={status_code}" if status_code and status_code != 200
                else "无 release_date"
            )
            return {
                "code": code,
                "ok": False,
                "status_code": status_code,
                "error": reason,
            }

    # 用独立线程跑 asyncio.run —— uvicorn 进程已有事件循环，
    # 直接 asyncio.run 会报 "cannot be called from a running event loop"。
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(asyncio.run, _do())
            return future.result(timeout=180)  # 单次最多 3 分钟
    except concurrent.futures.TimeoutError:
        return {"code": code, "ok": False, "error": "抓取超时（>180s）"}
    except RuntimeError as e:
        # ThreadPoolExecutor 自身跑不出来时的兜底
        return {"code": code, "ok": False, "error": f"线程执行失败：{e}"}
    except Exception as e:  # noqa: BLE001
        return {"code": code, "ok": False, "error": f"抓取异常：{e}"}