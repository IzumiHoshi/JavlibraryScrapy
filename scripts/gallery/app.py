"""FastAPI 工厂。

把原 ``GalleryServer + GalleryHandler`` 拆成：
- ``create_app``：构造 ``FastAPI`` 并注册路由 + lifespan
- ``State`` 上下文：通过 ``request.app.state.gallery`` 访问
"""

from __future__ import annotations

import logging
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from .config import Settings, load_settings
from .routes import register_routes
from .services.library import GalleryState
from .services.wanted import WantedService

logger = logging.getLogger("gallery.app")


def local_ip_address() -> str:
    """尽力获取当前机器可供局域网访问的 IPv4 地址。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def create_gallery_state(
    settings: Settings,
    data_path: Path,
    output_dir: Path,
    image_proxy_mode: str,
    no_rescan_on_startup: bool = False,
) -> GalleryState:
    """构造 ``GalleryState``（供 main 和测试使用）。"""
    state = GalleryState(
        data_path=data_path,
        output_dir=output_dir,
        image_proxy_mode=image_proxy_mode,
        proxy=settings.proxy,
        proxy_enabled=settings.proxy_enabled,
        user_agent=settings.user_agent,
        verify_ssl=settings.verify_ssl,
        download_timeout=settings.download_timeout,
        javbus_url=settings.javbus_url,
        library_root=settings.library_root,
        library_index_path=settings.library_index,
    )
    # 启动时若 root 不一致则强制重建（沿用原服务 main 行为）
    if (
        not no_rescan_on_startup
        and settings.library_root is not None
        and len(state.library_index) == 0
        and settings.library_root.exists()
    ):
        logger.info("启动时触发首次后台扫描…")
        state.start_rescan()
    return state


@asynccontextmanager
async def _lifespan(app: FastAPI, state: GalleryState):
    """FastAPI lifespan：启动/停止钩子。

    原 stdlib 服务没有显式 shutdown hook；这里预留给未来（例如清理 asyncio task）。
    """
    yield


def create_app(
    settings: Settings,
    data_path: Path,
    output_dir: Path,
    image_proxy_mode: str = "auto",
    no_rescan_on_startup: bool = False,
) -> FastAPI:
    """构造 FastAPI 应用。"""
    state = create_gallery_state(
        settings,
        data_path=data_path,
        output_dir=output_dir,
        image_proxy_mode=image_proxy_mode,
        no_rescan_on_startup=no_rescan_on_startup,
    )

    # JAVLibrary 镜像（c99i.com）当前不需要代理；磁力抓取（JavBus）由 GalleryState 用 settings.proxy。
    # 这里的两个 proxy 字段分别控制 wanted pipeline 两阶段的代理使用。
    wanted = WantedService(
        data_path=settings.library_index.parent / "javlibrary_movies.json",
        javlibrary_proxy=None,            # c99i.com 直连
        javbus_proxy=settings.proxy,      # JavBus 需要代理绕过 Cloudflare
    )

    app = FastAPI(
        title="JAV Gallery",
        version="2.0",
        description="影片画廊本地服务器（FastAPI 重构版）",
        lifespan=lambda a: _lifespan(a, state),
    )
    app.state.gallery = state
    app.state.settings = settings
    app.state.wanted = wanted

    register_routes(app)

    return app