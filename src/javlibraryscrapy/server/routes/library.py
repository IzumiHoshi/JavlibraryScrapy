"""本地库 API：

GET  /api/library                              —— 列表（分页/搜索/排序）
GET  /api/library/status                       —— 扫描状态
GET  /api/library/warnings                     —— 重复车牌 / 无 NFO 汇总
GET  /api/library/{carid}/gallery-images       —— 该车在 LIBRARY_ROOT 下的
                                                    sample_*.jpg URL（与 wanted 对称）
GET  /api/library/{carid}/image?type=cover|sample&idx=N —— 单张图片字节流
GET  /api/library/{carid}                      —— 单部详情

注册顺序：具体路径必须在 ``/api/library/{carid}`` 之前注册，否则 FastAPI
会把 "status"/"warnings" 当成 ``carid`` 参数（与原服务 stdlib 路由的行为一致）。
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ..services.library import CARID_RE


# 复用 wanted 的 sample 编号提取规则（必须保持一致，否则 sample_10.jpg 排在
# sample_2.jpg 前面的 bug 会复现）。从 wanted.py 镜像一份：跨 routes 共享
# 常量会让 wanted.py 变成 "public API"，代价大于复用收益。
_SAMPLE_IDX_RE = re.compile(r"sample_(\d+)\.jpg")

# 图库端点的扩展名优先级（cover/poster/fanart 各自按这个顺序找第一个存在的）
_IMG_EXTS = ("jpg", "png", "jpeg", "webp")
_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _empty_gallery_payload() -> Dict[str, Any]:
    """gallery-images 在 root 未配置 / entry 缺失 / folder 不存在时共用。"""
    return {
        "cover": None,
        "poster": None,
        "fanart": None,
        "samples": [],
        "folder_exists": False,
    }


def _find_image_url(folder: Path, kind: str, carid_norm: str) -> Optional[str]:
    """在 ``folder`` 下找 ``{kind}.{jpg,png,jpeg,webp}`` 第一张存在的图，返回
    image endpoint URL；都不存在返回 None。

    kind ∈ {"cover", "poster", "fanart"}。
    """
    for ext in _IMG_EXTS:
        p = folder / f"{kind}.{ext}"
        if p.exists():
            return f"/api/library/{carid_norm}/image?type={kind}"
    return None


# ---- library 端 sample 数量轻量缓存 ------------------------------------- #
# 与 wanted 的 ``SampleCountCache``（绑定 MOSTWANTED_LIBRARY_ROOT）不同：
# library 端的 entry.folder 已经是绝对路径（UNC 或本地），不需要从某个 root
# iterdir 找，所以这里用更直接的 ``folder_path -> (count, mtime, checked_at)`` 缓存。
#
# 并发安全：RLock 守护；线程池并发 glob（跟 wanted 同样 8 workers）。
# 失效：
#   - 距上次 stat < TTL 秒 → 直接返缓存 count（避免每次请求都 stat NFS）
#   - 距上次 stat >= TTL 秒 → 重新 stat，比对 mtime；变化则 glob 重数
#   - folder 不存在 / stat 失败 → 写 ``(0, 0.0, now)`` 缓存，下次直接返 0
#     （Sourcery review 修正：之前 OSError 时静默返回旧值，会让"用户拔了 NFS"
#     这种真实失效状态永远 stale）
# 所有读路径都走 ``_count_samples_in``（包括批量），让 TTL/mtime 校验生效。
# ``_LIB_SAMPLE_VALIDATE_TTL = 5.0``：跟 wanted 的 ``_VALIDATE_TTL`` 一致，
# 用户手动增删样本后最多 5s 内反映到 UI。
#
# 内存保护：``_LIB_SAMPLE_CACHE_MAX`` 之上清掉一半最旧条目（按 checked_at 升序）。
# 1137 部 + TTL=5s 不会触发上限；防的是长时间运行 + 大量手动 rescan 后
# folder 路径变化导致的死条目堆积（Sourcery 提的"无 eviction"问题）。
#
# Executor 不在模块级创建（Sourcery 提的"import 时创建、永不 shutdown"
# 问题，会在测试和多 app 实例场景下线程残留）。改在 ``app._lifespan``
# 里创建并存到 ``app.state.lib_sample_executor``，lifespan teardown 时
# 调 ``shutdown(wait=True)`` 干净关闭。
_LIB_SAMPLE_CACHE: Dict[str, Tuple[int, float, float]] = {}
_LIB_SAMPLE_LOCK = threading.RLock()
_LIB_SAMPLE_VALIDATE_TTL = 5.0
_LIB_SAMPLE_CACHE_MAX = 2048


def _evict_old_lib_cache() -> None:
    """缓存超过 ``_LIB_SAMPLE_CACHE_MAX`` 时清掉一半最旧（按 checked_at 升序）。"""
    if len(_LIB_SAMPLE_CACHE) <= _LIB_SAMPLE_CACHE_MAX:
        return
    sorted_items = sorted(_LIB_SAMPLE_CACHE.items(), key=lambda kv: kv[1][2])
    drop_count = len(_LIB_SAMPLE_CACHE) // 2
    for k, _ in sorted_items[:drop_count]:
        _LIB_SAMPLE_CACHE.pop(k, None)


def _count_samples_in(folder_str: str) -> int:
    """``folder_str -> 图片总数``（cover + poster + fanart + sample_NNN.jpg），
    TTL 节流 + mtime 校验 + 缓存。

    行为：
      - 缓存未命中 → glob/stat 一次，写 ``(count, mtime, now)``
      - 缓存命中且 ``now - checked_at < TTL`` → 直接返 count（**不 stat**）
      - 缓存命中但已过 TTL → stat folder：
          - 失败（folder 没了 / 共享断） → 写 ``(0, 0.0, now)`` 返 0
          - mtime 与缓存一致 → 刷 ``checked_at`` 返 count
          - mtime 变化 → 重数 + 写缓存

    范围说明：原版只数 ``sample_*.jpg``，但用户的 NFS 库里几乎没有 sample
    文件（poster+fanart 都是直接 copy 进来的），卡片 sample-badge 因此
    永远不显示。改为数 cover/poster/fanart/sample 总数后徽章才有意义。
    cover/poster/fanart 各按 ``_IMG_EXTS`` 优先级取一张（与 ``gallery-images``
    端点行为一致，避免"端点说有这个图但 cache 数 0"的不一致）。
    """
    now = time.monotonic()
    with _LIB_SAMPLE_LOCK:
        cached = _LIB_SAMPLE_CACHE.get(folder_str)
    if cached is not None:
        count, cached_mtime, checked_at = cached
        if now - checked_at < _LIB_SAMPLE_VALIDATE_TTL:
            return count
        # TTL 到期 → 重新 stat 校验 mtime
        try:
            cur_mtime = Path(folder_str).stat().st_mtime
        except OSError:
            # folder 不可访问：显式缓存为 0，下次 TTL 之内直接返 0
            with _LIB_SAMPLE_LOCK:
                _LIB_SAMPLE_CACHE[folder_str] = (0, 0.0, now)
                _evict_old_lib_cache()
            return 0
        if cur_mtime == cached_mtime:
            # mtime 没变 → 刷 checked_at，跳过 glob
            with _LIB_SAMPLE_LOCK:
                _LIB_SAMPLE_CACHE[folder_str] = (count, cached_mtime, now)
                _evict_old_lib_cache()
            return count
        # mtime 变 → 重数（落到下方"未命中"分支）

    # 缓存未命中 / TTL 到期且 mtime 变 → 实际数一次
    try:
        folder = Path(folder_str)
        if not folder.exists() or not folder.is_dir():
            with _LIB_SAMPLE_LOCK:
                _LIB_SAMPLE_CACHE[folder_str] = (0, 0.0, now)
                _evict_old_lib_cache()
            return 0
        # cover/poster/fanart 各按 _IMG_EXTS 优先级取一张，存在的算 1
        n = 0
        for kind in ("cover", "poster", "fanart"):
            for ext in _IMG_EXTS:
                if (folder / f"{kind}.{ext}").exists():
                    n += 1
                    break
        # sample_NNN.jpg 数量
        n += sum(1 for _ in folder.glob("sample_*.jpg"))
        try:
            mtime = folder.stat().st_mtime
        except OSError:
            # 数完后 mtime 拿不到（极端时序）：用 0.0，下次一定重数
            mtime = 0.0
    except OSError:
        with _LIB_SAMPLE_LOCK:
            _LIB_SAMPLE_CACHE[folder_str] = (0, 0.0, now)
            _evict_old_lib_cache()
        return 0

    with _LIB_SAMPLE_LOCK:
        _LIB_SAMPLE_CACHE[folder_str] = (n, mtime, now)
        _evict_old_lib_cache()
    return n


def _batch_count_samples(folders: List[str], request: Request) -> Dict[str, int]:
    """批量：folder → 图片总数（cover/poster/fanart/sample）。
    全部走 ``_count_samples_in``（让 TTL 节流 + mtime 校验生效），
    并发跑（app.state.lib_sample_executor）。

    旧实现"只对未命中并发，命中直接返"会让缓存条目永远不被 revalidate
    —— 一旦 cached 命中就锁死值，folder 内容变化也不更新（Sourcery bug_risk）。
    所有读路径统一走 ``_count_samples_in``，命中且未过 TTL 时它直接返缓存，
    开销 < 1µs；过 TTL 才 stat/glob。
    """
    if not folders:
        return {}
    # executor 在 app.state 上（lifespan 管理生命周期），不在模块级创建。
    # ThreadPoolExecutor.map 保持入参顺序 → dict key 对得上调用方传的 folders。
    executor = request.app.state.lib_sample_executor
    counts = list(executor.map(_count_samples_in, folders))
    return dict(zip(folders, counts))


def register(app: FastAPI) -> None:
    @app.get("/api/library/status")
    async def library_status(request: Request) -> Dict[str, Any]:
        state = request.app.state.gallery
        s = state.scan_state
        return {
            "configured": state.library_root is not None,
            "root": str(state.library_root) if state.library_root else None,
            "movies_count": len(state.library_index),
            "scanned_at": state.library_scanned_at,
            "is_running": s.is_running,
            "is_complete": s.is_complete,
            "scanned": s.scanned,
            "total_estimate": s.total_estimate,
            "current_folder": s.current_folder,
            "error": s.error,
        }

    @app.get("/api/library/warnings")
    async def library_warnings(request: Request) -> Dict[str, Any]:
        state = request.app.state.gallery
        stats = state.library_stats or {}
        return {
            "duplicate_carids": stats.get("duplicate_carids", []),
            "folders_without_nfo": stats.get("folders_without_nfo", []),
            "folders_no_carid": stats.get("folders_no_carid", []),
            "errors": stats.get("errors", []),
        }

    @app.get("/api/library")
    async def library_list(
        request: Request,
        q: str = "",
        month: str = "",
        actor: str = "",
        page: int = 1,
        size: int = 100,
        sort: str = "released",
    ) -> Dict[str, Any]:
        state = request.app.state.gallery
        if state.library_root is None:
            raise HTTPException(status_code=503, detail="未配置 LIBRARY_ROOT")

        page = max(1, page)
        size = min(200, max(1, size))
        if sort not in ("carid", "mtime", "released"):
            sort = "released"

        idx = state.library_index
        items = idx.all_sorted()

        # 先聚合月份桶（与 q/month/actor 过滤无关，让 chips 始终稳定可点）
        month_counts: Dict[str, int] = {}
        for e in items:
            rd = (e.release_date or "").strip()
            key = rd[:7] if len(rd) >= 7 and rd[4] == "-" else "unknown"
            month_counts[key] = month_counts.get(key, 0) + 1
        known_months = sorted((k for k in month_counts if k != "unknown"), reverse=True)
        months_payload: List[Dict[str, Any]] = [
            {"month": m, "count": month_counts[m]} for m in known_months
        ]
        if "unknown" in month_counts:
            months_payload.append({"month": "unknown", "count": month_counts["unknown"]})

        if sort == "mtime":
            items = sorted(items, key=lambda e: e.modified, reverse=True)
        elif sort == "released":
            # release_date 是 "YYYY-MM-DD" 字符串，字典序 == 时间序；
            # 缺 release_date 的影片排在最末。
            items = sorted(items, key=lambda e: (not (e.release_date or "").strip(), e.release_date), reverse=True)

        if q:
            q_upper = q.upper()
            q_lower = q.lower()
            items = [
                e
                for e in items
                if q_upper in e.carid
                or q_lower in (e.title or "").lower()
                or any(q_lower in a.lower() for a in e.actors)
            ]

        if month:
            if month == "unknown":
                items = [e for e in items if not (e.release_date or "").strip()]
            else:
                items = [e for e in items if (e.release_date or "").startswith(month)]

        if actor:
            a_lower = actor.strip().lower()
            items = [
                e
                for e in items
                if any(a_lower == (a or "").lower() for a in (e.actors or []))
            ]

        total = len(items)
        start = (page - 1) * size
        page_items = items[start : start + size]

        # 图片总数（cover/poster/fanart/sample_NNN.jpg）：走本模块的轻量缓存
        # （folder_path → count）。library 的 entry.folder 是绝对路径（UNC /
        # 本地）不需要从某个 root iterdir 找，所以不共用 wanted 的 SampleCountCache
        # （那个绑定 MOSTWANTED_ROOT）。前端用此字段渲染 .sample-badge。
        image_counts = _batch_count_samples([e.folder for e in page_items], request)

        return {
            "configured": True,
            "root": str(state.library_root),
            "scanned_at": state.library_scanned_at,
            "total": total,
            "page": page,
            "size": size,
            "q": q,
            "month": month,
            "actor": actor,
            "sort": sort,
            "months": months_payload,
            "movies": [
                {
                    "carid": e.carid,
                    "folder": e.folder,
                    "title": e.title,
                    "actors": e.actors,
                    "release_date": e.release_date,
                    "has_nfo": e.has_nfo,
                    "has_poster": e.has_poster,
                    "has_fanart": e.has_fanart,
                    "has_video": e.has_video,
                    "video_count": e.video_count,
                    "total_size_bytes": e.total_size_bytes,
                    "modified": e.modified,
                    "image_count": image_counts.get(e.folder, 0),
                }
                for e in page_items
            ],
        }

    # ---- gallery-images / image 字节流（与 wanted 对称） ----
    # wanted 那边用 ``_find_movie_folder`` iterdir NFS 找；library 这边有
    # 内存索引 ``state.library_index.get(carid).folder``，直接查表即可，
    # 既不扫盘也不缓存。folder 不存在（用户拔了 NFS）返回空集，不 500。
    # 必须在 ``/api/library/{carid}`` 之前注册 —— FastAPI 按声明顺序匹配，
    # 否则 ``{carid}`` 会吞掉 ``/gallery-images`` 后缀。

    @app.get("/api/library/{carid}/gallery-images")
    def gallery_images(carid: str, request: Request) -> Dict[str, Any]:
        """列出该车在本地库下的所有图 URL：cover / poster / fanart / sample_*.jpg。

        返回 cover/poster/fanart 三个单图 URL（null = 文件不存在）+ samples 数组。
        前端 openGalleryLb 把它们拼成一张图片列表（按 cover → poster → fanart →
        samples 顺序）。wanted 端只用到 cover + samples 两字段，本接口兼容
        （多出来的 poster/fanart 是 null 或省略）。

        ``def`` 而非 ``async def``：glob 是 sync I/O，让 FastAPI 放到 thread pool。
        """
        carid_norm = carid.strip().upper()
        if not CARID_RE.fullmatch(carid_norm):
            raise HTTPException(status_code=400, detail="非法的车牌")
        state = request.app.state.gallery
        if state.library_root is None:
            return _empty_gallery_payload()
        entry = state.library_index.get(carid_norm)
        if entry is None:
            return _empty_gallery_payload()

        folder = Path(entry.folder)
        if not folder.exists():
            return _empty_gallery_payload()

        # 找到 cover / poster / fanart 各一张（按优先级 .jpg > .png > .jpeg）。
        cover_url = _find_image_url(folder, "cover", carid_norm)
        poster_url = _find_image_url(folder, "poster", carid_norm)
        fanart_url = _find_image_url(folder, "fanart", carid_norm)

        sample_paths = sorted(
            folder.glob("sample_*.jpg"),
            # 按数字 idx 排序而非文件名（sample_10.jpg 不能排在 sample_2.jpg 前面）
            key=lambda p: int(_SAMPLE_IDX_RE.match(p.name).group(1))
            if _SAMPLE_IDX_RE.match(p.name)
            else 0,
        )
        samples: List[str] = []
        for p in sample_paths:
            m = _SAMPLE_IDX_RE.match(p.name)
            if not m:
                continue
            idx = int(m.group(1))
            samples.append(
                f"/api/library/{carid_norm}/image?type=sample&idx={idx}"
            )

        return {
            "cover": cover_url,
            "poster": poster_url,
            "fanart": fanart_url,
            "samples": samples,
            "folder_exists": True,
            "folder_name": folder.name,
        }

    @app.get("/api/library/{carid}/image")
    def serve_image(
        carid: str,
        request: Request,
        type: str = Query(..., pattern="^(cover|poster|fanart|sample)$"),
        idx: int = Query(1, ge=1, le=999),
    ):
        """返回 cover/poster/fanart/sample 字节流。

        ``cover`` ``poster`` ``fanart`` 各自按 ``{name}.jpg`` ``.png`` ``.jpeg``
        优先级找第一张存在的；不存在 → 404。``sample`` 走 ``sample_NNN.jpg``。
        """
        carid_norm = carid.strip().upper()
        if not CARID_RE.fullmatch(carid_norm):
            raise HTTPException(status_code=400, detail="非法的车牌")
        state = request.app.state.gallery
        if state.library_root is None:
            raise HTTPException(status_code=503, detail="未配置 LIBRARY_ROOT")
        entry = state.library_index.get(carid_norm)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"未找到 {carid_norm} 的本地条目")
        folder = Path(entry.folder)
        if not folder.exists():
            raise HTTPException(status_code=404, detail=f"文件夹不存在：{folder}")

        if type == "sample":
            target = folder / f"sample_{idx:03d}.jpg"
            if not target.exists():
                raise HTTPException(
                    status_code=404, detail=f"文件不存在：sample_{idx:03d}.jpg"
                )
        else:
            # cover / poster / fanart：按 .jpg → .png → .jpeg 顺序找第一个存在
            for ext in _IMG_EXTS:
                target = folder / f"{type}.{ext}"
                if target.exists():
                    break
            else:
                raise HTTPException(
                    status_code=404, detail=f"文件不存在：{type}.*"
                )

        # media_type 按实际扩展名决定（cover 也可能是 png）
        suffix = target.suffix.lower()
        media_type = _MEDIA_TYPES.get(suffix, "application/octet-stream")

        return FileResponse(
            target,
            media_type=media_type,
            # 1 天：cover.jpg 经常会被重新抓图替换；immutable 太长会让用户
            # 看不到新图（CDN/浏览器强缓存命中后不会重新请求）。
            headers={"Cache-Control": "public, max-age=86400"},
        )

    # 必须在 ``/api/library/{carid}/gallery-images`` 和 ``/image`` 之后注册。
    # path-param 路由会吞掉子路径后缀（FastAPI 按声明顺序 first-match wins）。
    @app.get("/api/library/{carid}")
    async def library_detail(carid: str, request: Request) -> Dict[str, Any]:
        state = request.app.state.gallery
        if not CARID_RE.fullmatch(carid.strip().upper()):
            raise HTTPException(status_code=400, detail="非法的车牌")
        entry = state.library_index.get(carid)
        if entry is None:
            raise HTTPException(status_code=404, detail="未找到该车牌")
        return entry.to_dict()