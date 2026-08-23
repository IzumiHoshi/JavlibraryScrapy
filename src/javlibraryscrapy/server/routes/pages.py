"""页面路由：GET /, /wanted, /library —— 统一返回 ``static/index.html``。

前端静态资源（CSS / JS 模块）由 ``app.py`` 的 ``StaticFiles`` 挂载在
``/static/`` 提供；本模块只负责把 SPA 入口 HTML 吐回去。

历史说明：早期实现读 ``templates/gallery.html``（117 KB 单文件），含
mtime 内存缓存 + 双检锁。重构后入口 HTML 只剩 ~7 KB，重复 read 也不再
是热点，改为简单 ``FileResponse`` —— Starlette 自动处理 ``If-Modified-Since``
304，省去手写 mtime cache。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse

from javlibraryscrapy._paths import PACKAGE_ROOT

INDEX_PATH = PACKAGE_ROOT / "static" / "index.html"


def register(app: FastAPI) -> None:
    @app.get("/", response_class=FileResponse)
    @app.get("/index.html", response_class=FileResponse)
    @app.get("/wanted", response_class=FileResponse)
    @app.get("/library", response_class=FileResponse)
    async def _page() -> FileResponse:
        return FileResponse(
            INDEX_PATH,
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
