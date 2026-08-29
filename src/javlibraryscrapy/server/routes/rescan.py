"""全库扫描 API：

POST /api/library/rescan           —— 触发全库扫描（手动重新建立 library_index）

单部刷新功能（``/api/library/{carid}/rescan`` + ``/api/library/rescan-status``）
已被「补齐缺失」接口取代（``library/backfill.py`` + 路由 ``library.py``）：
旧的单部刷新会删除并重下封面，破坏已有元数据；新的补齐接口保留一切
已有文件，仅补缺失的 NFO / 封面 / 样图。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request


def register(app: FastAPI) -> None:
    @app.post("/api/library/rescan")
    async def trigger_rescan(request: Request) -> Dict[str, Any]:
        """触发全库扫描（重建 library_index.json）。"""
        state = request.app.state.gallery
        if state.library_root is None:
            raise HTTPException(status_code=503, detail="未配置 LIBRARY_ROOT")
        if state.start_rescan():
            return {"ok": True}
        raise HTTPException(status_code=409, detail="扫描已在进行中")
