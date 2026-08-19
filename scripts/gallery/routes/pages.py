"""页面路由：GET /, /wanted, /library —— 统一返回同一份 HTML 模板。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "gallery.html"


def register(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    @app.get("/wanted", response_class=HTMLResponse)
    @app.get("/library", response_class=HTMLResponse)
    async def _page(request: Request) -> HTMLResponse:
        if not TEMPLATE_PATH.exists():
            return HTMLResponse(
                f"缺少页面模板：{TEMPLATE_PATH}",
                status_code=500,
            )
        return HTMLResponse(
            TEMPLATE_PATH.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )