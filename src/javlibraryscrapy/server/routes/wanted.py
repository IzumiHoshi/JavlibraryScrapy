"""Wanted API：手动刷新 + 按月分页 + 进度轮询 + 本地图片查询。

端点：
    POST /api/wanted/refresh                 —— 启动后台刷新（max_pages 可选）
    GET  /api/wanted/refresh-status          —— 当前任务进度（前端 1.5s 轮询）
    GET  /api/wanted/months                  —— 月份桶摘要（导航条用）
    GET  /api/wanted?month=YYYY-MM&page=N&size=K —— 按月分页列表
    GET  /api/wanted/{carid}/gallery-images  —— 该车在 MOSTWANTED_LIBRARY_ROOT 下
                                                的 cover.jpg + sample_*.jpg URL
    GET  /api/wanted/{carid}/image?type=cover|sample&idx=N —— 单张图片字节流
    POST /api/wanted/reload                  —— 从磁盘重读 wanted JSON（外部脚本改文件后用）

封面代理：
    ``/api/movies`` 在 ``image_proxy=on`` 时把 cover 改写成 ``/api/cover?url=...``
    让前端走服务端代理（DMM 等直连拿不到时用）。wanted 也做同样改写，保证
    海报一定可加载；不想走代理时设置 ``--image-proxy off`` 或
    GalleryState.image_proxy=False（wanted 默认跟随这个标志）。
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..services.library import CARID_RE, normalize_carid
from ..services.proxy import proxied_url
from ..services.sample_cache import get_sample_cache
from ..services.wanted import WantedService

logger = logging.getLogger("gallery.wanted_routes")


_SAMPLE_IDX_RE = re.compile(r"sample_(\d+)\.jpg")


# 硬限制：Most Wanted 刷新最多覆盖前 2 页（约 40 部）。
# 原因：
#   1. 全站 ≈ 500 部一轮刷新耗时 30 分钟以上，性价比低；
#   2. 头部以外的车牌在 JavBus 上大量 404，没有 release_date 进不了任何月份桶；
#   3. 用户明确要求"只跑前两页"。
# 任何来源（前端表单 / API 直接调用 / CLI 脚本）都会被截断到这个上限。
DEFAULT_MAX_PAGES = 2
MAX_PAGES_HARD_CAP = 2


class RefreshBody(BaseModel):
    """POST /api/wanted/refresh 的请求体。body 可空 / 字段可缺。

    - 空 body / 非 dict：所有字段走默认（max_pages=2，只抓前 2 页）
    - ``max_pages <= 0`` 在 handler 里视同未传（保留旧行为「正数才生效」）
    - 多余字段静默忽略
    """

    model_config = ConfigDict(extra="ignore")

    max_pages: Optional[int] = Field(
        default=2,
        description="限制抓取的 Most Wanted 页数；不传/<=0 = 默认只抓前 2 页。Most Wanted 头部热度最高，2 页 ≈ 40 部足以覆盖日常需要。",
    )


def _find_movie_folder(mw_root: Path, carid: str) -> Optional[Path]:
    """在 ``mw_root`` 下找到第一个 ``<CARID> <title>/`` 文件夹（大小写不敏感）。

    不依赖 wanted service 内存状态，避免启动期 / 重新加载间隙读到旧 title。

    性能：
        - 内存缓存 ``{carid: (path, expires_at)}``，TTL = 60 秒
        - 命中即返回（避免每次灯箱打开都 iterdir NFS / SMB 几百毫秒）
        - 缓存"未找到"也记，避免失败的车牌被反复扫
    """
    if not mw_root.exists() or not mw_root.is_dir():
        return None

    carid_u = carid.upper()
    now = time.monotonic()
    cached = _FOLDER_CACHE.get((mw_root, carid_u))
    if cached is not None and cached[1] > now:
        return cached[0]  # 可能为 None（负缓存），由调用方处理

    prefix = carid_u + " "
    found: Optional[Path] = None
    try:
        for entry in mw_root.iterdir():
            if entry.is_dir() and entry.name.upper().startswith(prefix):
                found = entry
                break  # 第一个匹配即可，无需全部扫
    except OSError as e:
        logger.warning(f"无法枚举 {mw_root}: {e}")

    _FOLDER_CACHE[(mw_root, carid_u)] = (found, now + _FOLDER_CACHE_TTL)
    # 防内存膨胀：超过上限时清掉一半最早的条目
    if len(_FOLDER_CACHE) > _FOLDER_CACHE_MAX:
        try:
            items = sorted(_FOLDER_CACHE.items(), key=lambda kv: kv[1][1])
            for k, _ in items[: len(items) // 2]:
                _FOLDER_CACHE.pop(k, None)
        except Exception:
            _FOLDER_CACHE.clear()
    return found


# (mw_root, carid) → (folder_path or None, expires_at_monotonic)
_FOLDER_CACHE: Dict[Any, Tuple[Optional[Path], float]] = {}
_FOLDER_CACHE_TTL = 60.0
_FOLDER_CACHE_MAX = 2048


def register(app: FastAPI) -> None:
    # 注册顺序：精确路径（/refresh, /refresh-status, /months, /）必须在
    # {carid} path-param 路由之前注册，否则会被 path-param 吞掉。

    @app.post("/api/wanted/refresh")
    async def refresh(request: Request) -> Dict[str, Any]:
        wanted: WantedService = request.app.state.wanted
        # body 是可选的：空 body / 非 JSON / 非 dict 都按 max_pages=DEFAULT 处理
        body_dict: Dict[str, Any] = {}
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                body_dict = raw
        except Exception:
            pass
        payload = RefreshBody.model_validate(body_dict)
        # 硬限制：max_pages 最多 MAX_PAGES_HARD_CAP 页。
        # <=0 / 缺省 → DEFAULT_MAX_PAGES；>CAP → 截断到 CAP；<=CAP → 保留原值。
        if not payload.max_pages or payload.max_pages <= 0:
            requested = DEFAULT_MAX_PAGES
        else:
            requested = payload.max_pages
        max_pages = min(requested, MAX_PAGES_HARD_CAP)
        # 把 cache 透传给后台线程，落盘后回填（避免下次 /api/wanted 扫 NFS）
        cache = getattr(request.app.state, "sample_cache", None) or get_sample_cache()
        return wanted.start_refresh(max_pages=max_pages, sample_cache=cache)

    @app.get("/api/wanted/refresh-status")
    def refresh_status(request: Request) -> Dict[str, Any]:
        wanted: WantedService = request.app.state.wanted
        snap = wanted.get_refresh_status()
        if snap is None:
            return {"status": "idle"}
        return snap

    @app.post("/api/wanted/reload")
    def reload(request: Request) -> Dict[str, Any]:
        """从磁盘重读 wanted JSON。

        用途：外部脚本（如 ``scripts/backfill_magnets.py``）改完
        ``javlibrary_movies.json`` 后调用，让 gallery 服务立即看到新数据，
        不用重启进程。refresh_wanted 任务跑完也会自动 reload。

        Returns:
            ``{"ok": True, "loaded_at": ISO 时间, "total": N,
            "with_magnet": M}``
        """
        wanted: WantedService = request.app.state.wanted
        try:
            wanted.reload()
        except Exception as e:  # noqa: BLE001
            logger.error(f"wanted reload 失败：{e}")
            raise HTTPException(status_code=500, detail=f"reload 失败：{e}")
        # reload() 后 _loaded_at / _movies 已更新
        total = len(wanted._movies)
        with_magnet = sum(1 for m in wanted._movies if m.get("magnet"))
        return {
            "ok": True,
            "loaded_at": wanted._loaded_at,
            "total": total,
            "with_magnet": with_magnet,
        }

    @app.post("/api/wanted/{carid}/javbus")
    async def fetch_one_javbus(carid: str, request: Request) -> Dict[str, Any]:
        """手动单车 JavBus 重抓。

        用途：之前 JavBus 404 / 失败的某个车牌，用户手动触发重抓；
        也会自动写回 JSON（成功 → ready + 月份桶；失败 → failed + unknown）。

        body 可选 ``{"title"?: str, "cover_url"?: str}``：
        - 如果车牌不在 JSON 中，会先用 body 里的 title/cover_url 建一条最小记录
          再抓 JavBus（成功时 JavBus 的 title/cover 会覆盖传入值）；
        - 如果车牌已在 JSON，body 字段被忽略（已存在的 title/cover 不被覆盖）。

        返回 ``WantedService.fetch_one_javbus`` 的 dict：
        ``{"code", "ok", "status_code", "error", "bucket", "release_date",
        "title", "created", "saved"}``。失败时 HTTP 200（语义层面失败），
        让前端能拿到 error 字段；非法车牌 → 400。
        """
        carid_norm = carid.strip().upper()
        if not CARID_RE.fullmatch(carid_norm):
            raise HTTPException(status_code=400, detail="非法的车牌")

        # 兼容用户输入 "ipzz907" 这种漏写 "-" 的写法：自动插入分隔符。
        # 不做这一步的话 JavBus 会返回 404，表现为"抓取失败 http=404"，
        # 但实际上车牌是合法的（JAVLibrary JSON 里也是 IPZZ-907）。
        normalized = normalize_carid(carid_norm)
        if not normalized:
            raise HTTPException(
                status_code=400,
                detail=f"非法的车牌：{carid!r}（应为 字母-数字 格式，如 IPZZ-907）",
            )
        if normalized != carid_norm:
            logger.info(f"车牌 {carid_norm} 自动规范化 → {normalized}")
            carid_norm = normalized

        body_dict: Dict[str, Any] = {}
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                body_dict = raw
        except Exception:
            pass

        wanted: WantedService = request.app.state.wanted
        settings = request.app.state.settings
        mw_root = getattr(settings, "mostwanted_library_root", None)
        cache = getattr(request.app.state, "sample_cache", None) or get_sample_cache()

        # 单点抓取在 asyncio 线程池里跑，避免阻塞 uvicorn 事件循环。
        # 否则用户点 A 车刷新后立刻点 B 车封面，B 的 lightbox 请求会被卡住（直到 A 抓完）。
        # 原 fetch_one_javbus 内部用 ThreadPoolExecutor 跑 asyncio.run（避开 event-loop 冲突），
        # 但那个 ThreadPoolExecutor 是子线程池，调用方（事件循环线程）依然被 future.result 阻塞。
        # 这里用 asyncio.to_thread 把整个同步调用搬到 asyncio 默认线程池，
        # 事件循环本身只 await 不阻塞，其它请求（lightbox、图片代理）可以并行处理。
        # fetch_one_javbus 是 keyword-only 参数，所以必须用 functools.partial 包装。
        result = await asyncio.to_thread(
            functools.partial(
                wanted.fetch_one_javbus,
                carid_norm,
                title=(body_dict.get("title") or "").strip() or None,
                cover_url=(body_dict.get("cover_url") or "").strip() or None,
                mw_root=Path(mw_root) if mw_root else None,
                sample_cache=cache,
            )
        )
        logger.info(
            f"单车 JavBus 重抓 {carid_norm}: ok={result.get('ok')} "
            f"status={result.get('status_code')} error={result.get('error')} "
            f"bucket={result.get('bucket')}"
        )
        return result

    @app.post("/api/wanted/{carid}/organize")
    async def organize_one(
        carid: str,
        request: Request,
        dry_run: bool = Query(
            default=False,
            description="只预览不执行：列出所有计划动作但不实际写盘 / 移动 / 删除",
        ),
    ) -> Dict[str, Any]:
        """把单部已下载的 wanted 影片从 wanted 目录整理到本地库。

        前置条件：
        - 已配置 ``LIBRARY_ROOT``（本地库根目录）
        - 已配置 ``MOSTWANTED_LIBRARY_ROOT``（wanted 库根目录）
        - 已配置 ``LOCAL_DOWNLOAD_PATH``（本地可访问的 NAS 下载目录，
          例如 Windows 映射盘符或 UNC 路径；留空则回退 ``ZSPACE_DOWNLOAD_PATH``）

        整理动作：
        1. 在 ``<MOSTWANTED_LIBRARY_ROOT>/<CARID> <title>/`` 找源文件夹
        2. 读 NFO 拿 title / release_date，算月份桶
        3. 把整个源文件夹（nfo / poster / fanart / samples）复制到
           ``<LIBRARY_ROOT>/<YYYY-MM>/<CARID> <title>/``
        4. 在 ``<ZSPACE_DOWNLOAD_PATH>`` 下找含车牌的下载（取最大文件）
        5. 移动 + 重命名为 ``<CARID> <title>.<ext>``
        6. 如果下载是文件夹，删源文件夹
        7. 触发 library scanner 更新索引

        ``?dry_run=true`` 时只列计划动作，不实际写盘/移动/删除，
        返回 dict 额外带 ``plan: [str, ...]`` 列出每一步要做什么。

        目标目录已存在 → ``ok=False`` + ``skipped="already_organized"``，
        不覆盖用户数据。

        返回 :func:`organize_movie` 的 dict，可直接 toast 展示。
        """
        from javlibraryscrapy.library.organizer import organize_movie

        carid_norm = carid.strip().upper()
        if not CARID_RE.fullmatch(carid_norm):
            raise HTTPException(status_code=400, detail="非法的车牌")
        normalized = normalize_carid(carid_norm)
        if not normalized:
            raise HTTPException(
                status_code=400,
                detail=f"非法的车牌：{carid!r}（应为 字母-数字 格式，如 IPZZ-907）",
            )
        if normalized != carid_norm:
            logger.info(f"车牌 {carid_norm} 自动规范化 → {normalized}")
            carid_norm = normalized

        settings = request.app.state.settings
        mw_root = getattr(settings, "mostwanted_library_root", None)
        lib_root = getattr(settings, "library_root", None)
        # NAS 下载目录：优先用 local_download_path（Windows/局域网视角），
        # 回退到 zspace_download_path（NAS 视角，如果本机也能访问的话）。
        local_download_path = getattr(settings, "local_download_path", None)
        zspace_download_path = getattr(settings, "zspace_download_path", None)
        nas_download_path = local_download_path or zspace_download_path
        # JavBus 抓取（NFO 兜底用）—— 跟单部刷新 /api/wanted/{code}/javbus 用同一份配置
        javbus_url = getattr(settings, "javbus_url", None) or "https://www.javbus.com"
        # proxy_javbus_enabled 开着才传 proxy（与 javbus 端点 / 单部 refresh 保持一致）
        javbus_proxy = (
            getattr(settings, "proxy", None)
            if getattr(settings, "proxy_javbus_enabled", False)
            else None
        )

        missing = []
        if not mw_root:
            missing.append("MOSTWANTED_LIBRARY_ROOT")
        if not lib_root:
            missing.append("LIBRARY_ROOT")
        if not nas_download_path:
            missing.append("LOCAL_DOWNLOAD_PATH 或 ZSPACE_DOWNLOAD_PATH")
        if missing:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"整理功能未配置（缺失：{', '.join(missing)}，"
                    "在 .env 里设置并重启服务）"
                ),
            )

        # library scanner 回调：整理成功后增量刷新索引，
        # 让前端的「本地已有」徽章立刻亮起。
        # dry_run=True 时不调（不能给用户假预览预览），给个 no-op 即可。
        gallery = getattr(request.app.state, "gallery", None)

        def _rescan():
            if not dry_run and gallery is not None and hasattr(gallery, "start_rescan"):
                gallery.start_rescan()

        # 与 javbus 端点一致：放到 asyncio 线程池跑，避免阻塞事件循环
        # （复制 / 移动可能涉及 NFS / SMB / NAS 大文件 IO，单次几秒到十几秒）
        result = await asyncio.to_thread(
            functools.partial(
                organize_movie,
                carid_norm,
                Path(mw_root),
                Path(lib_root),
                Path(nas_download_path),
                on_library_change=_rescan,
                dry_run=dry_run,
                javbus_url=javbus_url,
                javbus_proxy=javbus_proxy,
            )
        )
        logger.info(
            f"整理 {carid_norm}: ok={result.get('ok')} "
            f"videos_moved={result.get('videos_moved')} "
            f"nas_source_removed={result.get('nas_source_removed')} "
            f"skipped={result.get('skipped')} error={result.get('error')}"
        )
        return result

    @app.get("/api/wanted/months")
    async def months(request: Request) -> Dict[str, Any]:
        wanted: WantedService = request.app.state.wanted
        return wanted.list_months(include_missing=True)

    @app.get("/api/wanted")
    async def list_wanted(
        request: Request,
        month: str = Query(default="", description="YYYY-MM 或 'unknown'"),
        page: int = Query(default=1, ge=1),
        size: int = Query(default=60, ge=1, le=200),
        include_missing: bool = Query(default=True),
        q: str = Query(default="", description="搜索关键字（车牌/标题/演员，大小写不敏感）"),
    ) -> Dict[str, Any]:
        wanted: WantedService = request.app.state.wanted
        gallery = request.app.state.gallery
        # 启动期 create_app 已经按 settings 配好 cache；这里直接用。
        cache = getattr(request.app.state, "sample_cache", None) or get_sample_cache()
        result = wanted.list(
            month=month,
            page=page,
            size=size,
            include_missing=include_missing,
            q=q,
        )

        # local_samples：NFS glob 单次几百毫秒～几秒，60 条串行扫 = 几十秒。
        # 走 SampleCountCache：命中即返，未命中并发 glob（thread pool）。
        codes = [item.get("code") or "" for item in result.get("items", [])]
        counts = cache.counts_for(codes)

        # 封面代理：跟随 GalleryState 的 image_proxy 标志（与 /api/movies 共用同一 helper）
        # local_exists：查 gallery.library_index（in-memory，O(1)）。整理完的车 →
        # local_exists=true → 前端徽章变「📁 已整理」（紫色），点击按钮消失。
        # 双向匹配（LibraryIndex.find_match 是 a.startswith(b) or b.startswith(a)），
        # 兼容车牌 + 不同子编码前缀。
        lib_index = getattr(gallery, "library_index", None)
        result["items"] = [
            {
                **item,
                "cover": proxied_url(item.get("cover_url") or item.get("cover"), gallery),
                "local_samples": counts.get((item.get("code") or "").upper(), 0),
                "local_exists": (
                    lib_index.find_match(code) is not None
                    if lib_index is not None and hasattr(lib_index, "find_match")
                    else None
                ),
            }
            for item, code in zip(
                result.get("items", []),
                [item.get("code") or "" for item in result.get("items", [])],
            )
        ]
        return result

    @app.get("/api/wanted/{carid}/gallery-images")
    def gallery_images(carid: str, request: Request) -> Dict[str, Any]:
        """列出该车在本地的 cover + samples URL。文件夹不存在返回空集。

        用 ``def`` 而非 ``async def``：路由内做 NFS sync I/O（folder glob / exists），
        FastAPI 会自动放到 thread pool 跑，避免在 scrape 进行时阻塞 event loop。
        """
        carid_norm = carid.strip().upper()
        if not CARID_RE.fullmatch(carid_norm):
            raise HTTPException(status_code=400, detail="非法的车牌")
        settings = request.app.state.settings
        mw_root: Optional[Path] = settings.mostwanted_library_root
        if not mw_root:
            return {"cover": None, "samples": [], "folder_exists": False}

        folder = _find_movie_folder(Path(mw_root), carid_norm)
        if not folder:
            return {"cover": None, "samples": [], "folder_exists": False}

        cover_path = folder / "cover.jpg"
        cover_url: Optional[str] = (
            f"/api/wanted/{carid_norm}/image?type=cover" if cover_path.exists() else None
        )

        sample_paths = sorted(
            folder.glob("sample_*.jpg"),
            # 按数字 idx 排序而非文件名（sample_10.jpg 不能排在 sample_2.jpg 前面）
            key=lambda p: int(_SAMPLE_IDX_RE.match(p.name).group(1))
            if _SAMPLE_IDX_RE.match(p.name)
            else 0,
        )
        samples: List[str] = []
        for p in sample_paths:
            # 直接从文件名取 idx，保证 URL idx 与磁盘文件名一致
            # （不依赖连续编号——用户手动删了某张也不会 404）
            m = _SAMPLE_IDX_RE.match(p.name)
            if not m:
                continue
            idx = int(m.group(1))
            samples.append(
                f"/api/wanted/{carid_norm}/image?type=sample&idx={idx}"
            )

        return {
            "cover": cover_url,
            "samples": samples,
            "folder_exists": True,
            "folder_name": folder.name,
        }

    @app.get("/api/wanted/{carid}/image")
    def serve_image(
        carid: str,
        request: Request,
        type: str = Query(..., pattern="^(cover|sample)$"),
        idx: int = Query(1, ge=1, le=999),
    ):
        """返回 cover.jpg 或 sample_NNN.jpg 的字节流。

        用 ``def`` 而非 ``async def``：FileResponse 是 sync I/O，scrape 进行时
        在 thread pool 里跑可以避免阻塞 event loop。
        """
        carid_norm = carid.strip().upper()
        if not CARID_RE.fullmatch(carid_norm):
            raise HTTPException(status_code=400, detail="非法的车牌")
        settings = request.app.state.settings
        mw_root: Optional[Path] = settings.mostwanted_library_root
        if not mw_root:
            raise HTTPException(status_code=503, detail="未配置 MOSTWANTED_LIBRARY_ROOT")

        folder = _find_movie_folder(Path(mw_root), carid_norm)
        if not folder:
            raise HTTPException(status_code=404, detail=f"未找到 {carid_norm} 的本地文件夹")

        if type == "cover":
            target = folder / "cover.jpg"
        else:  # sample
            target = folder / f"sample_{idx:03d}.jpg"

        if not target.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在：{target.name}")

        # cover.jpg / sample_NNN.jpg 文件名稳定 + 内容不变 → 浏览器可永久缓存
        # （无 ETag/Last-Modified 也行，文件名已经隐含版本信息）
        return FileResponse(
            target,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )