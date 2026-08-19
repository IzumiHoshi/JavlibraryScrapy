"""本地库 API：

GET  /api/library              —— 列表（分页/搜索/排序）
GET  /api/library/status       —— 扫描状态
GET  /api/library/warnings     —— 重复车牌 / 无 NFO 汇总
GET  /api/library/{carid}      —— 单部详情

注册顺序：具体路径必须在 ``/api/library/{carid}`` 之前注册，否则 FastAPI
会把 "status"/"warnings" 当成 ``carid`` 参数（与原服务 stdlib 路由的行为一致）。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request

from ..services.library import CARID_RE


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
        page: int = 1,
        size: int = 100,
        sort: str = "carid",
    ) -> Dict[str, Any]:
        state = request.app.state.gallery
        if state.library_root is None:
            raise HTTPException(status_code=503, detail="未配置 LIBRARY_ROOT")

        page = max(1, page)
        size = min(200, max(1, size))
        if sort not in ("carid", "mtime"):
            sort = "carid"

        idx = state.library_index
        items = idx.all_sorted()
        if sort == "mtime":
            items = sorted(items, key=lambda e: e.modified, reverse=True)

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

        total = len(items)
        start = (page - 1) * size
        page_items = items[start : start + size]

        return {
            "configured": True,
            "root": str(state.library_root),
            "scanned_at": state.library_scanned_at,
            "total": total,
            "page": page,
            "size": size,
            "q": q,
            "sort": sort,
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
                }
                for e in page_items
            ],
        }

    @app.get("/api/library/{carid}")
    async def library_detail(carid: str, request: Request) -> Dict[str, Any]:
        state = request.app.state.gallery
        if not CARID_RE.fullmatch(carid.strip().upper()):
            raise HTTPException(status_code=400, detail="非法的车牌")
        entry = state.library_index.get(carid)
        if entry is None:
            raise HTTPException(status_code=404, detail="未找到该车牌")
        return entry.to_dict()