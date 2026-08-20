"""单部刷新 API：

POST /api/library/rescan           —— 触发全库扫描
POST /api/library/{carid}/rescan   —— 把单部入队刷新
GET  /api/library/rescan-status    —— 队列状态（前端轮询用）

注册顺序：``/api/library/rescan`` 与 ``/api/library/rescan-status`` 必须先注册，
否则 FastAPI 会把 "rescan"/"rescan-status" 当成 ``carid`` 参数（不合法 → 400）。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request

from ..services.library import CARID_RE


def register(app: FastAPI) -> None:
    @app.post("/api/library/rescan")
    async def trigger_rescan(request: Request) -> Dict[str, Any]:
        state = request.app.state.gallery
        if state.library_root is None:
            raise HTTPException(status_code=503, detail="未配置 LIBRARY_ROOT")
        if state.start_rescan():
            return {"ok": True}
        raise HTTPException(status_code=409, detail="扫描已在进行中")

    @app.get("/api/library/rescan-status")
    async def rescan_status(request: Request) -> Dict[str, Any]:
        state = request.app.state.gallery
        return state.get_rescan_status()

    @app.post("/api/library/{carid}/rescan")
    async def enqueue_rescan(carid: str, request: Request) -> Dict[str, Any]:
        state = request.app.state.gallery
        if not carid or not CARID_RE.fullmatch(carid.strip().upper()):
            raise HTTPException(status_code=400, detail="非法的车牌")
        if state.library_root is None:
            raise HTTPException(status_code=503, detail="未配置 LIBRARY_ROOT")
        try:
            job = state.enqueue_rescan_movie(carid)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))

        snap = state.get_rescan_status()
        for q in snap.get("queued", []):
            if q["carid"] == carid.upper():
                return {
                    "ok": True,
                    "carid": carid,
                    "already": True,
                    "position": q["position"],
                }
        if snap.get("current") and snap["current"]["carid"] == carid.upper():
            return {
                "ok": True,
                "carid": carid,
                "already": True,
                "running": True,
            }
        return {
            "ok": True,
            "carid": carid,
            "status": job.status,
            "position": snap["total"],
        }