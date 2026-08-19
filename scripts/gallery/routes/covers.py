"""GET /api/cover —— 服务端代理拉取封面（带缓存）。"""

from __future__ import annotations

import urllib.parse
from typing import Optional

from fastapi import FastAPI, Request, Response


def register(app: FastAPI) -> None:
    @app.get("/api/cover")
    async def cover(request: Request, url: str = "") -> Response:
        state = request.app.state.gallery
        from ..services.covers import fetch_cover

        result = fetch_cover(
            url=url,
            cache_dir=state.cover_cache_dir,
            user_agent=state.user_agent,
            timeout=state.download_timeout,
            verify_ssl=state.verify_ssl,
            cover_proxy=state.cover_proxy,
        )
        if result is None:
            return Response(
                content='{"error":"封面获取失败"}',
                media_type="application/json; charset=utf-8",
                status_code=404,
            )
        body, content_type = result
        return Response(
            content=body,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/api/local-cover")
    async def local_cover(
        request: Request,
        folder: str = "",
        name: str = "",
    ) -> Response:
        state = request.app.state.gallery
        from pathlib import Path

        from ..services.covers import find_local_cover, guess_cover_content_type

        if not folder:
            return Response(
                content='{"error":"缺少 folder"}',
                media_type="application/json; charset=utf-8",
                status_code=400,
            )

        folder_path = Path(folder)
        if not state.is_within_library(folder_path):
            return Response(
                content='{"error":"路径越界"}',
                media_type="application/json; charset=utf-8",
                status_code=403,
            )

        from library_scanner import COVER_NAMES, FANART_NAMES

        cover_path = find_local_cover(folder_path, name=name)
        if name and cover_path is None:
            return Response(
                content='{"error":"非允许的文件名"}',
                media_type="application/json; charset=utf-8",
                status_code=403,
            )
        if cover_path is None:
            cover_path = find_local_cover(folder_path, name="")
        if cover_path is None:
            return Response(
                content='{"error":"封面不存在"}',
                media_type="application/json; charset=utf-8",
                status_code=404,
            )

        try:
            body = cover_path.read_bytes()
        except OSError as e:
            return Response(
                content=f'{{"error":"读取失败：{e}"}}',
                media_type="application/json; charset=utf-8",
                status_code=500,
            )

        return Response(
            content=body,
            media_type=guess_cover_content_type(cover_path.suffix),
            headers={"Cache-Control": "public, max-age=3600"},
        )