"""Wanted 服务：JAVLibrary Most Wanted 的状态 + 后台刷新任务管理。

设计：
- 单实例运行：同一时刻只允许一个刷新任务（再触发返回 already_running）
- ``movie_list`` 在内存缓存 + 磁盘持久化（``output/javlibrary_movies.json``）
  启动时从磁盘加载，运行中由 ``refresh_wanted`` 改写
- 按月分桶：``_bucket`` 字段（YYYY-MM）由 ``release_date`` 推导
  没有 release_date 的归入 ``unknown``，前端在 UI 单独显示

P2 优化：
- ``_movies`` 在 ``reload`` / ``save`` 后立即按 ``release_date`` 倒序、一次性
  生成 ``_sorted_movies``（immutable until next save）。后续 list 只需过滤
  + 切片，不必每次都 sort。
- ``months`` 摘要（含 / 不含 missing 两份） + ``missing_in_remote_count``
  同样在 reload/save 时预算好，list() 直接读，O(1) 拿到。
- 整个 list() 路径从「3 次全量遍历 + 排序」降到「1 次过滤 + 切片」。
"""

from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .sample_cache import SampleCountCache

from .wanted_refresh import (
    WantedRefreshJob,
    _bucket_for_release_date,
    new_job,
    refresh_wanted,
)

logger = logging.getLogger("gallery.wanted")

__all__ = ["WantedService", "WantedRefreshJob", "_bucket_for_release_date"]


def _matches_query(movie: Dict[str, Any], q_lower: str) -> bool:
    """wanted 单部是否匹配搜索关键字（已 lower + strip）。

    匹配字段：车牌 / 标题 / 演员（任一命中即 True）。
    大小写不敏感、unicode 子串匹配（演员里可能有日文片假名/汉字混排，
    单纯 .lower() 对中日字符是 no-op，但留着不影响行为）。
    """
    code = (movie.get("code") or "").lower()
    if q_lower in code:
        return True
    title = (movie.get("title") or "").lower()
    if q_lower in title:
        return True
    actors = (movie.get("actors") or "").lower()
    if q_lower in actors:
        return True
    return False


class WantedService:
    """Wanted 状态 + 后台任务管理。

    ``javlibrary_url``：JAVLibrary「最想要」列表入口 URL（默认 c99i.com 镜像；
    切镜像/换原站时改 .env 的 ``JAVLIBRARY_URL``）。
    ``javlibrary_proxy``：JAVLibrary 镜像抓取时使用的代理，受 ``.env`` 的
    ``PROXY_JAVLIBRARY_ENABLED`` 控制；c99i.com 默认不需要。
    ``javbus_proxy``：JavBus 详情抓取时使用的代理，受 ``.env`` 的
    ``PROXY_JAVBUS_ENABLED`` 控制（JavBus 需代理绕过 Cloudflare）。
    """

    def __init__(
        self,
        data_path: Path,
        javlibrary_url: str = "https://www.c99i.com/cn/vl_mostwanted.php",
        javlibrary_proxy: Optional[str] = None,
        javbus_proxy: Optional[str] = None,
    ):
        self.data_path = Path(data_path)
        self.javlibrary_url = javlibrary_url
        self.javlibrary_proxy = javlibrary_proxy
        self.javbus_proxy = javbus_proxy
        self._lock = threading.Lock()
        self._movies: List[Dict[str, Any]] = []
        # ---- 派生数据（reload/save 时一次性算好）----
        self._sorted_movies: List[Dict[str, Any]] = []      # 按 release_date desc 预排
        self._months_with_missing: List[Dict[str, Any]] = []  # 月份桶计数（包含 missing_in_remote）
        self._months_without_missing: List[Dict[str, Any]] = []  # 月份桶计数（不含 missing）
        self._missing_count: int = 0  # 整个集合 missing_in_remote 总数
        self.job: Optional[WantedRefreshJob] = None
        self._thread: Optional[threading.Thread] = None
        self._loaded_at: Optional[str] = None
        self.reload()

    # ---- 加载 / 持久化 ----
    def reload(self) -> None:
        """从磁盘重新加载（覆盖内存列表）。同时重算派生数据。"""
        with self._lock:
            if not self.data_path.exists():
                self._movies = []
                self._sorted_movies = []
                self._months_with_missing = []
                self._months_without_missing = []
                self._missing_count = 0
                self._loaded_at = None
                return
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"wanted JSON 加载失败：{e}")
                self._movies = []
                self._sorted_movies = []
                self._months_with_missing = []
                self._months_without_missing = []
                self._missing_count = 0
                self._loaded_at = None
                return
            # 兜底：补 _bucket
            for entry in raw:
                if "_bucket" not in entry and "release_date" in entry:
                    rd = entry.get("release_date") or ""
                    entry["_bucket"] = rd[:7] if (len(rd) >= 7 and rd[4] == "-") else "unknown"
            self._movies = raw
            self._recompute_derived_unlocked()
            self._loaded_at = datetime.now().isoformat(timespec="seconds")
            logger.info(f"wanted 已加载 {len(self._movies)} 部（{self.data_path}）")

    def save(self, movies: List[Dict[str, Any]]) -> None:
        """原子写。``refresh_wanted`` 内部用，外部不直接调用。同时重算派生数据。"""
        with self._lock:
            self._movies = movies
            self._recompute_derived_unlocked()
            self._loaded_at = datetime.now().isoformat(timespec="seconds")
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.data_path.with_suffix(self.data_path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(movies, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.data_path)

    # ---- 派生数据（在锁内调用）----
    def _recompute_derived_unlocked(self) -> None:
        """调用方需持有 ``_lock``。

        - 预排序：``release_date`` 倒序，空日期落到最后（与原行为一致）
        - 月份桶计数：含/不含 missing 各算一份
        - missing_in_remote 总数
        """
        movies = self._movies
        # 排序：空 release_date 落到最后（reverse=True 把 "" 排到末尾）
        self._sorted_movies = sorted(
            movies,
            key=lambda m: (m.get("release_date") or ""),
            reverse=True,
        )
        # missing 总数（独立于 include_missing 参数）
        self._missing_count = sum(1 for m in movies if m.get("missing_in_remote"))
        # 两份月份桶摘要
        self._months_with_missing = self._compute_months_summary(movies, include_missing=True)
        self._months_without_missing = self._compute_months_summary(movies, include_missing=False)

    # ---- 列表查询 ----
    def list_months(
        self,
        include_missing: bool = True,
    ) -> Dict[str, Any]:
        """只返回月份桶摘要 + 整个集合的 missing_in_remote 计数。

        P2：直接读预计算的派生数据，O(1)。
        """
        with self._lock:
            months = (
                self._months_with_missing if include_missing
                else self._months_without_missing
            )
            missing_count = self._missing_count
            return {
                "months": list(months),  # 防御性 copy，调用方不要修改
                "missing_in_remote_count": missing_count,
            }

    @staticmethod
    def _compute_months_summary(
        movies: List[Dict[str, Any]],
        include_missing: bool,
    ) -> List[Dict[str, Any]]:
        """月份桶计数 + 排序（``unknown`` 排最后，按月份倒序）。"""
        bucket_counter: Counter = Counter()
        for m in movies:
            if m.get("missing_in_remote") and not include_missing:
                continue
            bkt = m.get("_bucket") or "unknown"
            if bkt == "":
                bkt = "unknown"
            bucket_counter[bkt] += 1
        return [
            {"month": b, "count": c}
            for b, c in sorted(
                bucket_counter.items(),
                key=lambda x: (x[0] == "unknown", x[0]),
                reverse=True,
            )
        ]

    def get(self, code: str) -> Optional[Dict[str, Any]]:
        """按车牌查 wanted 记录（大小写不敏感），未找到返回 None。

        镜像 :meth:`LibraryIndex.get` 接口；调用方在锁外只读快照，
        所以无需拿 ``_lock``（``_movies`` 字典本身在 reload/save 时被整体替换，
        读端可能短暂看到旧值，但语义安全）。
        """
        if not code:
            return None
        target = code.strip().upper()
        for m in self._movies:
            if (m.get("code") or "").upper() == target:
                return m
        return None

    def list(
        self,
        month: str = "",
        page: int = 1,
        size: int = 60,
        include_missing: bool = True,
        q: str = "",
    ) -> Dict[str, Any]:
        """按月份筛选 + 关键字搜索 + 分页。同时返回 ``months`` 列表（月份桶摘要）。

        P2：派生数据（months 摘要、missing 总数）已预计算，``_sorted_movies``
        已按 release_date 倒序。本方法只需一次过滤 + 切片。

        ``q`` 是大小写不敏感的子串搜索，匹配车牌 / 标题 / 演员（任一命中即返回）。
        之前的版本把搜索留在前端做（``applyLocalFilter``），导致只能搜当前页 60 部。
        现在下沉到服务端，搜全部 129 部；前端用 debounce 输入框触发重拉。
        """
        with self._lock:
            all_sorted = self._sorted_movies
            months = (
                self._months_with_missing if include_missing
                else self._months_without_missing
            )
            missing_count = self._missing_count

        # 单次过滤：月份 + include_missing + 关键字搜索
        items = all_sorted
        if month:
            items = [m for m in items if (m.get("_bucket") or "unknown") == month]
        if not include_missing:
            items = [m for m in items if not m.get("missing_in_remote")]
        q_norm = q.strip().lower()
        if q_norm:
            items = [m for m in items if _matches_query(m, q_norm)]
        # 已是倒序，直接切片
        total = len(items)
        page = max(1, page)
        size = min(200, max(1, size))
        start = (page - 1) * size
        page_items = items[start:start + size]

        return {
            "months": list(months),  # 防御性 copy
            "items": page_items,
            "total": total,
            "page": page,
            "size": size,
            "month": month,
            "q": q,
            "missing_in_remote_count": missing_count,
        }

    # ---- 任务管理 ----
    def start_refresh(
        self,
        max_pages: Optional[int] = 2,
        sample_cache: Optional["SampleCountCache"] = None,
    ) -> Dict[str, Any]:
        """启动后台刷新任务。

        返回：``{"job_id": str, "is_already_running": bool}``
        ``sample_cache`` 透传给后台线程，落盘后回填（避免下次扫描 NFS）。
        ``max_pages`` 默认 2（Most Wanted 头部热度最高，2 页 ≈ 40 部够日常用）；
        传 None 显式抓全站。
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
                args=(
                    self.data_path,
                    self.javlibrary_url,
                    self.javlibrary_proxy,
                    self.javbus_proxy,
                    job,
                ),
                kwargs={
                    "max_pages": max_pages,
                    "on_complete": self.reload,
                    "sample_cache": sample_cache,
                },
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

    # ---- 单车 JavBus 重抓（手动） ----
    def fetch_one_javbus(
        self,
        code: str,
        *,
        title: Optional[str] = None,
        cover_url: Optional[str] = None,
        mw_root: Optional[Path] = None,
        sample_cache: Optional["SampleCountCache"] = None,
    ) -> Dict[str, Any]:
        """对单个车牌手动重抓 JavBus，更新内存 + 落盘 + reload 派生数据。

        - 如果 code 不在 JSON：新建一条最小记录（仅 ``code/title/cover_url``，
          ``_status=pending``，``_bucket=unknown``），再抓 JavBus；这样可以
          手工给"曾经失败后被 cleanup 删除"的车牌重新尝试（无需先走全站刷新）。
        - 抓取成功：写入 ``release_date/actors/producer/publisher/category``，
          ``_status=ready``，``_bucket=YYYY-MM``，清 ``missing_in_remote``。
        - 抓取失败：``_status=failed``，``_bucket=unknown``，``release_date=""``。

        ``mw_root``：本地库根目录；非空时把 cover/samples 落到
        ``<root>/<CARID> <title>/``。不传则不落本地库（仍写 JSON 元数据）。

        返回：``{"code", "ok", "status_code", "error", "bucket", "release_date",
        "created", "saved"}`` 供 route 直接 jsonify。
        """
        from .wanted_refresh import scrape_one_javbus

        code_norm = (code or "").strip().upper()
        if not code_norm:
            return {"code": code_norm, "ok": False, "error": "空车牌"}

        # 取 cover_url 用于 poster.jpg 下载：caller 显式传入优先（CLI 等场景），
        # 否则从内部 JSON 状态里读已有值（前端点 ↻ 时 body 是空的，cover_url 必须
        # 从内存恢复，否则 poster.jpg 永远不下）
        if not cover_url:
            with self._lock:
                existing_for_cover = next(
                    (m for m in self._movies
                     if (m.get("code") or "").upper() == code_norm),
                    None,
                )
            if existing_for_cover:
                cover_url = (existing_for_cover.get("cover_url") or "").strip() or None

        result = scrape_one_javbus(
            code_norm,
            self.javbus_proxy,
            mw_root=mw_root,
            sample_cache=sample_cache,
            cover_url=cover_url,
        )

        ok = bool(result.get("ok"))
        info = result.get("info") or {}
        status_code = result.get("status_code")
        saved = result.get("saved")

        with self._lock:
            movies = list(self._movies)  # 防御性 copy，避免外部迭代时被改
            existing = next(
                (m for m in movies if (m.get("code") or "").upper() == code_norm),
                None,
            )
            created = False
            if existing is None:
                existing = {
                    "id": "",
                    "code": code_norm,
                    "title": (title or "").strip(),
                    "cover_url": (cover_url or "").strip(),
                    "release_date": "",
                    "magnet": "",
                    "_status": "pending",
                    "_bucket": "unknown",
                    "_seen_at": datetime.now().isoformat(timespec="seconds"),
                    "_updated_at": datetime.now().isoformat(timespec="seconds"),
                    "missing_in_remote": False,
                }
                movies.append(existing)
                created = True

            if ok:
                existing["release_date"] = (info.get("release_date") or "").strip()
                existing["actors"] = (info.get("actors") or "").strip()
                existing["producer"] = (info.get("producer") or "").strip()
                existing["publisher"] = (info.get("publisher") or "").strip()
                existing["category"] = (info.get("category") or "").strip()
                existing["magnet"] = (info.get("magnet") or "").strip()
                # title 优先用 JavBus 返回的（通常更准），回退到 caller 传值
                info_title = (info.get("title") or "").strip()
                if info_title:
                    existing["title"] = info_title
                # cover_url 不从 JavBus 写回：
                #   - JavbusSpider.parse 把 ``cover`` 存成本地 Path（不是远程 URL），
                #     写入 JSON 会让前端 ``<img src=...>`` 拿到 ``Z:\\Private\\...`` 失败
                #   - cover_url 应该由 JAVLibrary 抓取阶段提供；保持原值不变
                # 已存在条目：保留 JAVLibrary URL；新建条目若 caller 没传：保持空串
                # （下次 JAVLibrary refresh 会补上）
                existing["_bucket"] = _bucket_for_release_date(existing["release_date"])
                existing["_status"] = "ready"
                existing["missing_in_remote"] = False
                existing["_updated_at"] = datetime.now().isoformat(timespec="seconds")
            else:
                # 失败时：若是本次新建的条目 → 直接回滚不入盘，避免 JSON 被
                # "对不存在车牌试一次就多一条 failed/unknown" 污染；若是已存在的
                # 条目 → 保留为 failed（让用户看到"我之前确实试过"），便于下次重试
                if created:
                    movies.remove(existing)
                    created = False
                    logger.info(
                        f"单车 JavBus 重抓 {code_norm} 失败且条目是新建的，回滚不入盘"
                    )
                else:
                    existing["release_date"] = ""
                    existing["_bucket"] = "unknown"
                    existing["_status"] = "failed"
                    existing["_updated_at"] = datetime.now().isoformat(timespec="seconds")

            # 落盘 + 重算派生数据（save 内部已上锁；这里已持锁，拆开做）
            self._movies = movies
            self._recompute_derived_unlocked()
            self._loaded_at = datetime.now().isoformat(timespec="seconds")
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.data_path.with_suffix(self.data_path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(movies, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.data_path)

        return {
            "code": code_norm,
            "ok": ok,
            "status_code": status_code,
            "error": result.get("error"),
            # rollback 路径（失败且是新建条目）：existing 已从 movies 移除，
            # 不要暴露它的初始字段给前端；直接给"未持久化"的占位值
            "bucket": "unknown" if not ok else existing.get("_bucket"),
            "release_date": "" if not ok else (existing.get("release_date") or ""),
            "title": "" if not ok else (existing.get("title") or ""),
            "created": created,
            "saved": saved if ok else None,
            # 完整的更新后条目（供前端 in-place 更新卡片，不需要重新 load 全表）：
            # - 成功：返回带最新 release_date/bucket/_status 的完整 entry
            # - 失败 + 已存在：返回带 failed 状态的完整 entry（让前端只刷一张卡）
            # - 失败 + 新建被回滚：返回 None（前端需要走全表 load，新条目未持久化）
            "movie": dict(existing) if ok or not created else None,
        }

    # ---- 预热辅助（P1）----
    def iter_codes(self) -> List[str]:
        """返回当前 wanted 列表里所有 code 的快照（用于 sample cache 预热）。

        按 release_date 倒序排好，前 N 个就是用户最常看的。
        """
        with self._lock:
            return [m.get("code") or "" for m in self._sorted_movies]