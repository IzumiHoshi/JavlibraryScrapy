"""POST /api/open-folder —— 用资源管理器打开本地目录（越界保护）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request


def register(app: FastAPI) -> None:
    @app.post("/api/open-folder")
    async def open_folder(request: Request) -> dict:
        state = request.app.state.gallery
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="请求体不是合法 JSON")

        folder = (payload or {}).get("folder", "")
        if not isinstance(folder, str) or not folder:
            raise HTTPException(status_code=400, detail="缺少 folder")

        folder_path = Path(folder)
        if not state.is_within_library(folder_path):
            raise HTTPException(status_code=403, detail="路径越界")
        if not folder_path.exists() or not folder_path.is_dir():
            raise HTTPException(status_code=404, detail="文件夹不存在")

        try:
            from ..services.covers import open_in_explorer
            open_in_explorer(folder_path)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"打开失败：{e}")
        return {"ok": True}