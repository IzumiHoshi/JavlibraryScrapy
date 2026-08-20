"""Wanted 影片图片 API。

把 ``MOSTWANTED_LIBRARY_ROOT/<CARID> <title>/`` 下的 ``cover.jpg`` 和
``sample_NNN.jpg`` 暴露给前端灯箱用。

端点：
    GET /api/wanted/{carid}/gallery-images
        → ``{"cover": url, "samples": [urls], "folder_exists": bool, "folder_name": str}``

    GET /api/wanted/{carid}/image?type=cover
    GET /api/wanted/{carid}/image?type=sample&idx=N
        → image/jpeg 字节流

URL 不暴露文件系统路径，server 自己负责路径解析（基于 ``mostwanted_library_root``
和车牌号 → 文件夹名 ``<CARID> <title>``）。这样防止路径穿越，调用方拿到的只是
本服务的固定 URL 模板。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse

logger = logging.getLogger("gallery.wanted_images")

# 与 gallery 其他端点一致的车牌格式
_CARID_RE = re.compile(r"[A-Z0-9_-]{2,32}")


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
    @app.get("/api/wanted/{carid}/gallery-images")
    async def gallery_images(carid: str, request: Request) -> Dict[str, Any]:
        """列出该车在本地的 cover + samples URL。文件夹不存在返回空集。"""
        carid_norm = carid.strip().upper()
        if not _CARID_RE.fullmatch(carid_norm):
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
        if not _CARID_RE.fullmatch(carid_norm):
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