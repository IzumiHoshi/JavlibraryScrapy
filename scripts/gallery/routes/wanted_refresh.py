"""Wanted API：手动刷新 + 按月分页 + 进度轮询。

端点：
    POST /api/wanted/refresh        —— 启动后台刷新（max_pages 可选）
    GET  /api/wanted/refresh-status —— 当前任务进度（前端 1.5s 轮询）
    GET  /api/wanted/months         —— 月份桶摘要（导航条用）
    GET  /api/wanted?month=YYYY-MM&page=N&size=K —— 按月分页列表

封面代理：
    ``/api/movies`` 在 ``image_proxy=on`` 时把 cover 改写成 ``/api/cover?url=...``
    让前端走服务端代理（DMM 等直连拿不到时用）。wanted 也做同样改写，保证
    海报一定可加载；不想走代理时设置 ``--image-proxy off`` 或
    GalleryState.image_proxy=False（wanted 默认跟随这个标志）。
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request

from ..services.wanted import WantedService

logger = logging.getLogger("gallery.wanted_routes")


def _maybe_proxy_cover(state: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    """如果服务启用了 image_proxy，把 cover_url 重写成 /api/cover?url=...

    行为对齐 /api/movies 路由：state.image_proxy 为 True 时改写，否则原样返回。
    不存在 cover_url / cover 字段时不动。
    """
    cover = item.get("cover") or item.get("cover_url")
    if not cover:
        return item
    # state 可能为 None（极小窗口）；None 时不动
    use_proxy = bool(getattr(state, "image_proxy", False)) if state is not None else False
    if use_proxy and not cover.startswith("/api/cover?"):
        item["cover"] = "/api/cover?url=" + urllib.parse.quote(cover, safe="")
    return item


def register(app: FastAPI) -> None:
    @app.post("/api/wanted/refresh")
    async def refresh(request: Request) -> Dict[str, Any]:
        wanted: WantedService = request.app.state.wanted
        # max_pages 是可选 body 参数（前端通常不传 = 整站抓）
        max_pages: Optional[int] = None
        try:
            body = await request.json()
            if isinstance(body, dict):
                mp = body.get("max_pages")
                if mp is not None:
                    mp_int = int(mp)
                    if mp_int > 0:
                        max_pages = mp_int
        except Exception:
            # 空 body / 非 JSON / 无 max_pages 字段 → 用 None
            pass
        result = wanted.start_refresh(max_pages=max_pages)
        return result

    @app.get("/api/wanted/refresh-status")
    async def refresh_status(request: Request) -> Dict[str, Any]:
        wanted: WantedService = request.app.state.wanted
        snap = wanted.get_refresh_status()
        if snap is None:
            return {"status": "idle"}
        return snap

    @app.get("/api/wanted/months")
    async def months(request: Request) -> Dict[str, Any]:
        wanted: WantedService = request.app.state.wanted
        result = wanted.list(month="", page=1, size=1)
        return {"months": result["months"], "missing_in_remote_count": result["missing_in_remote_count"]}

    @app.get("/api/wanted")
    async def list_wanted(
        request: Request,
        month: str = Query(default="", description="YYYY-MM 或 'unknown'"),
        page: int = Query(default=1, ge=1),
        size: int = Query(default=60, ge=1, le=200),
        include_missing: bool = Query(default=True),
    ) -> Dict[str, Any]:
        wanted: WantedService = request.app.state.wanted
        gallery = request.app.state.gallery
        result = wanted.list(
            month=month,
            page=page,
            size=size,
            include_missing=include_missing,
        )
        # 封面代理：跟随 GalleryState 的 image_proxy 标志
        result["items"] = [
            _maybe_proxy_cover(gallery, dict(item))
            for item in result.get("items", [])
        ]
        return result