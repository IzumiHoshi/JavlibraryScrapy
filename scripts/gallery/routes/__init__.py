"""FastAPI 路由注册。

把 ``gallery_server.py`` 中的 12 个端点拆到几个模块文件，每个模块只负责一组端点。
URL/方法/响应形状与原服务 1:1 对齐 —— 前端 HTML 不需要任何改动。
"""

from __future__ import annotations

from fastapi import FastAPI

from . import (
    covers as _covers,
    folder as _folder,
    library as _library,
    movies as _movies,
    pages as _pages,
    rescan as _rescan,
    scrape as _scrape,
    wanted_refresh as _wanted_refresh,
)


def register_routes(app: FastAPI) -> None:
    """把全部路由挂到 app 上。

    注册顺序很关键：``/api/library/{carid}`` 这种 path-param 路由会在
    ``/api/library/rescan``、``/api/library/rescan-status``、``/api/library/status``、
    ``/api/library/warnings`` 之前匹配所有以 ``/api/library/`` 开头的请求。
    因此 rescan.py（精确路径）必须先于 library.py（path-param）注册。
    """
    _pages.register(app)
    _movies.register(app)
    _scrape.register(app)
    _covers.register(app)
    _rescan.register(app)        # 先注册精确路径
    _library.register(app)       # 再注册 {carid} path-param
    _wanted_refresh.register(app)
    _folder.register(app)