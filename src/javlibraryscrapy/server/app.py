"""FastAPI 工厂。

把原 ``GalleryServer + GalleryHandler`` 拆成：
- ``create_app``：构造 ``FastAPI`` 并注册路由 + lifespan
- ``State`` 上下文：通过 ``request.app.state.gallery`` 访问
"""

from __future__ import annotations

import logging
import socket
import threading
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

    - 启动：创建 ``lib_sample_executor``（8 workers）放到 ``app.state``，
      供 library routes 的 ``_batch_count_samples`` 并发 glob NFS 用。
      不放在模块级是为了测试 / 多 app 实例场景下能干净 shutdown。
    - 停止：``shutdown(wait=True)`` 等所有 in-flight glob 跑完再退；
      关掉 zspace 的 httpx 客户端（如果被用过的话）让连接池释放。
    """
    from concurrent.futures import ThreadPoolExecutor
    app.state.lib_sample_executor = ThreadPoolExecutor(
        max_workers=8, thread_name_prefix="lib-sample-cache"
    )
    try:
        yield
    finally:
        # wait=True：让正在跑 NFS glob 的线程跑完，避免半截结果 + 资源泄漏
        app.state.lib_sample_executor.shutdown(wait=True)
        # zspace httpx 客户端（懒加载，可能从未创建）：有就 aclose，没就跳过
        zspace = getattr(app.state, "zspace", None)
        if zspace is not None:
            try:
                await zspace.aclose()
            except Exception:  # noqa: BLE001
                pass


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
    # data_path：MOSTWANTED_LIBRARY_ROOT 设了 → JSON 放在那里；否则退回 library_index.parent（保持旧行为）
    if settings.mostwanted_library_root:
        wanted_data_path = settings.mostwanted_library_root / "javlibrary_movies.json"
    else:
        wanted_data_path = settings.library_index.parent / "javlibrary_movies.json"
    wanted = WantedService(
        data_path=wanted_data_path,
        javlibrary_proxy=None,            # c99i.com 直连
        javbus_proxy=settings.proxy,      # JavBus 需要代理绕过 Cloudflare
    )

    # sample 数量缓存：启动期用 settings.mostwanted_library_root 一次性配置。
    # 之后刷新任务会用 cache.put() 回填，避免下次扫描 NFS。
    from javlibraryscrapy.server.services.sample_cache import get_sample_cache
    sample_cache = get_sample_cache(mw_root=settings.mostwanted_library_root)

    # 极空间 NAS 配置存储（JSON 文件，output/zspace_config.json）。
    # 首次启动从 .env 兜底 + 落盘；之后以 JSON 为准，可通过网页 UI 修改。
    from javlibraryscrapy.server.services.zspace_config import ZSpaceConfigStore
    zspace_config_store = ZSpaceConfigStore(output_dir=output_dir, settings=settings)

    # P1：后台预热 sample cache —— 把 wanted 列表里的前 N 个 code 的 sample 数扫掉，
    # 让首次 /api/wanted 不必触发 NFS cold start（NFS 单目录 glob 几百 ms~几 s）。
    # 用 daemon 线程，不阻塞启动；失败也不影响服务可用。
    def _prewarm() -> None:
        try:
            codes = wanted.iter_codes()
            sample_cache.prewarm(codes[:120])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"sample cache prewarm 失败：{e}")

    threading.Thread(target=_prewarm, daemon=True, name="sample-cache-prewarm").start()

    app = FastAPI(
        title="JAV Gallery",
        version="2.0",
        description="影片画廊本地服务器（FastAPI 重构版）",
        lifespan=lambda a: _lifespan(a, state),
    )
    app.state.gallery = state
    app.state.settings = settings
    app.state.wanted = wanted
    app.state.sample_cache = sample_cache
    app.state.zspace_config_store = zspace_config_store

    register_routes(app)

    return app