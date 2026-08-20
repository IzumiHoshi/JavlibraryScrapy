"""Wanted API：手动刷新 + 按月分页 + 进度轮询 + 本地图片查询。

端点：
    POST /api/wanted/refresh                 —— 启动后台刷新（max_pages 可选）
    GET  /api/wanted/refresh-status          —— 当前任务进度（前端 1.5s 轮询）
    GET  /api/wanted/months                  —— 月份桶摘要（导航条用）
    GET  /api/wanted?month=YYYY-MM&page=N&size=K —— 按月分页列表
    GET  /api/wanted/{carid}/gallery-images  —— 该车在 MOSTWANTED_LIBRARY_ROOT 下
                                                的 cover.jpg + sample_*.jpg URL
    GET  /api/wanted/{carid}/image?type=cover|sample&idx=N —— 单张图片字节流

封面代理：
    ``/api/movies`` 在 ``image_proxy=on`` 时把 cover 改写成 ``/api/cover?url=...``
    让前端走服务端代理（DMM 等直连拿不到时用）。wanted 也做同样改写，保证
    海报一定可加载；不想走代理时设置 ``--image-proxy off`` 或
    GalleryState.image_proxy=False（wanted 默认跟随这个标志）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..services.library import CARID_RE
from ..services.proxy import proxied_url
from ..services.wanted import WantedService

logger = logging.getLogger("gallery.wanted_routes")


class RefreshBody(BaseModel):
    """POST /api/wanted/refresh 的请求体。body 可空 / 字段可缺。

    - 空 body / 非 dict：所有字段走默认（max_pages=None = 整站抓）
    - ``max_pages <= 0`` 在 handler 里视同未传（保持旧行为「正数才生效」）
    - 多余字段静默忽略
    """

    model_config = ConfigDict(extra="ignore")

    max_pages: Optional[int] = Field(
        default=None,
        description="限制抓取的 Most Wanted 页数；不传 = 抓所有页。<=0 视同未传。",
    )


def _find_movie_folder(mw_root: Path, carid: str) -> Optional[Path]:
    """在 ``mw_root`` 下找到第一个 ``<CARID> <title>/`` 文件夹（大小写不敏感）。

    不依赖 wanted service 内存状态，避免启动期 / 重新加载间隙读到旧 title。
    """
    if not mw_root.exists() or not mw_root.is_dir():
        return None
    prefix = carid.upper() + " "
    try:
        for entry in mw_root.iterdir():
            if entry.is_dir() and entry.name.upper().startswith(prefix):
                return entry
    except OSError as e:
        logger.warning(f"无法枚举 {mw_root}: {e}")
    return None


def register(app: FastAPI) -> None:
    # 注册顺序：精确路径（/refresh, /refresh-status, /months, /）必须在
    # {carid} path-param 路由之前注册，否则会被 path-param 吞掉。

    @app.post("/api/wanted/refresh")
    async def refresh(request: Request) -> Dict[str, Any]:
        wanted: WantedService = request.app.state.wanted
        # body 是可选的：空 body / 非 JSON / 非 dict 都按 max_pages=None 处理
        body_dict: Dict[str, Any] = {}
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                body_dict = raw
        except Exception:
            pass
        payload = RefreshBody.model_validate(body_dict)
        # 保持旧行为：<=0 视同未传（前端偶尔会传 0 / 负数兜底）
        max_pages = payload.max_pages if payload.max_pages and payload.max_pages > 0 else None
        return wanted.start_refresh(max_pages=max_pages)

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
        # 封面代理：跟随 GalleryState 的 image_proxy 标志（与 /api/movies 共用同一 helper）
        result["items"] = [
            {**item, "cover": proxied_url(item.get("cover_url") or item.get("cover"), gallery)}
            for item in result.get("items", [])
        ]
        return result

    @app.get("/api/wanted/{carid}/gallery-images")
    async def gallery_images(carid: str, request: Request) -> Dict[str, Any]:
        """列出该车在本地的 cover + samples URL。文件夹不存在返回空集。"""
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

        sample_paths = sorted(folder.glob("sample_*.jpg"))
        samples: List[str] = [
            f"/api/wanted/{carid_norm}/image?type=sample&idx={i + 1}"
            for i, p in enumerate(sample_paths)
            if p.exists()
        ]

        return {
            "cover": cover_url,
            "samples": samples,
            "folder_exists": True,
            "folder_name": folder.name,
        }

    @app.get("/api/wanted/{carid}/image")
    async def serve_image(
        carid: str,
        request: Request,
        type: str = Query(..., pattern="^(cover|sample)$"),
        idx: int = Query(1, ge=1, le=999),
    ):
        """返回 cover.jpg 或 sample_NNN.jpg 的字节流。"""
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

        return FileResponse(target, media_type="image/jpeg")