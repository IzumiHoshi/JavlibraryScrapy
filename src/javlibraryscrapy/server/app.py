"""FastAPI 工厂。

把原 ``GalleryServer + GalleryHandler`` 拆成：
- ``create_app``：构造 ``FastAPI`` 并注册路由 + lifespan
- ``State`` 上下文：通过 ``request.app.state.gallery`` 访问
"""

from __future__ import annotations

import logging
import re
import socket
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from javlibraryscrapy._paths import PACKAGE_ROOT
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
    magnets_index: Optional[Path] = None,
    no_rescan_on_startup: bool = False,
) -> GalleryState:
    """构造 ``GalleryState``（供 main 和测试使用）。

    ``magnets_index`` 可由 caller 显式覆盖 settings.magnets_index，便于测试
    写到 tmp 目录。
    """
    state = GalleryState(
        data_path=data_path,
        output_dir=output_dir,
        image_proxy_mode=image_proxy_mode,
        proxy=settings.proxy,
        proxy_javbus_enabled=settings.proxy_javbus_enabled,
        user_agent=settings.user_agent,
        verify_ssl=settings.verify_ssl,
        download_timeout=settings.download_timeout,
        javbus_url=settings.javbus_url,
        library_root=settings.library_root,
        library_index_path=settings.library_index,
        magnets_index=magnets_index if magnets_index is not None else settings.magnets_index,
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


class VersionedStaticFiles(StaticFiles):
    """支持版本化文件名的 StaticFiles。

    URL 中的 ``wanted.<8字符hex>.js`` 会按 hex 后缀切掉，还原到磁盘真实文件
    ``wanted.js``。浏览器看到 URL 变了 → 必定发新请求（不是 304），所以无需
    Cache-Control 头也保证文件更新即时生效。

    设计要点：
    - 8 字符 hex = 文件 mtime 的低 32 位，覆盖 2106 年之前的需求
    - hash 后缀不参与路由匹配，只作 cache-busting；命中后即被剥离
    - 非版本化 URL（原 ``app.css``）也照常工作，向后兼容
    """

    VERSION_SUFFIX_RE = re.compile(
        r"^(?P<stem>.+?)\.([a-f0-9]{8})(?P<ext>\.[^.]+)$"
    )

    async def get_response(self, path: str, scope: dict) -> Response:  # type: ignore[override]
        # path 是相对 static_dir 的，如 ``js/wanted.abc123.js``。
        # Windows 下 Starlette 会把 ``/`` 转成 ``\\``，rsplit 时要兼容两种。
        sep = "\\" if "\\" in path else "/"
        parts = path.rsplit(sep, 1)
        if len(parts) == 2:
            dir_part, filename = parts
            m = self.VERSION_SUFFIX_RE.match(filename)
            if m:
                real_filename = f"{m.group('stem')}{m.group('ext')}"
                real_path = f"{dir_part}{sep}{real_filename}"
                return await super().get_response(real_path, scope)
        return await super().get_response(path, scope)


def versioned_static_url(static_dir: Path, rel_path: str) -> str:
    """把 ``js/wanted.js`` 这种相对 static_dir 的路径，转成带 hash 的 URL。

    例：``js/wanted.js`` → ``/static/js/wanted.5a1b2c3d.js``

    文件不存在时（开发态偶尔发生）原样返回，避免 HTML 渲染挂掉。
    """
    file_path = static_dir / rel_path
    if not file_path.exists():
        return f"/static/{rel_path}"
    mtime = int(file_path.stat().st_mtime) & 0xFFFFFFFF
    hash_hex = format(mtime, "08x")
    p = Path(rel_path)
    parent = p.parent.as_posix()
    new_name = f"{p.stem}.{hash_hex}{p.suffix}"
    if parent and parent != ".":
        return f"/static/{parent}/{new_name}"
    return f"/static/{new_name}"


def create_app(
    settings: Settings,
    data_path: Path,
    output_dir: Path,
    image_proxy_mode: str = "auto",
    magnets_index: Optional[Path] = None,
    no_rescan_on_startup: bool = False,
) -> FastAPI:
    """构造 FastAPI 应用。

    ``magnets_index`` 可显式覆盖 settings.magnets_index；不传则走 settings。
    """
    state = create_gallery_state(
        settings,
        data_path=data_path,
        output_dir=output_dir,
        image_proxy_mode=image_proxy_mode,
        magnets_index=magnets_index,
        no_rescan_on_startup=no_rescan_on_startup,
    )

    # JAVLibrary 镜像（c99i.com）当前不需要代理；磁力抓取（JavBus）由 GalleryState 用 settings.proxy。
    # 这里的两个 proxy 字段分别控制 wanted pipeline 两阶段的代理使用。
    # data_path 解析优先级：MOSTWANTED_INDEX > MOSTWANTED_LIBRARY_ROOT/javlibrary_movies.json
    # > library_index.parent/javlibrary_movies.json（保持旧行为）
    if settings.mostwanted_index:
        wanted_data_path = settings.mostwanted_index
    elif settings.mostwanted_library_root:
        wanted_data_path = settings.mostwanted_library_root / "javlibrary_movies.json"
    else:
        wanted_data_path = settings.library_index.parent / "javlibrary_movies.json"
    # 代理分开配：javlibrary 镜像抓取走 proxy_javlibrary_enabled 控制；
    # javbus 详情抓取走 proxy_javbus_enabled 控制（共用 settings.proxy 地址）。
    javlibrary_proxy = (
        settings.proxy if settings.proxy_javlibrary_enabled else None
    )
    javbus_proxy = (
        settings.proxy if settings.proxy_javbus_enabled else None
    )
    wanted = WantedService(
        data_path=wanted_data_path,
        javlibrary_url=settings.javlibrary_url,
        javlibrary_proxy=javlibrary_proxy,
        javbus_proxy=javbus_proxy,
    )

    # sample 数量缓存：启动期用 settings.mostwanted_library_root 一次性配置。
    # 之后刷新任务会用 cache.put() 回填，避免下次扫描 NFS。
    from javlibraryscrapy.server.services.sample_cache import get_sample_cache
    sample_cache = get_sample_cache(mw_root=settings.mostwanted_library_root)

    # 极空间 NAS 配置 holder（从 Settings.zspace_* 读取；运行时只读）。
    from javlibraryscrapy.server.services.zspace_config import ZSpaceConfigStore
    zspace_config_store = ZSpaceConfigStore(settings=settings)

    # 本地库「补齐缺失文件」后台服务（POST /api/library/backfill 触发）
    from javlibraryscrapy.server.services.library_backfill import LibraryBackfillService
    library_backfill = LibraryBackfillService(
        gallery_state=state,
        wanted_service=wanted,
    )

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
    app.state.library_backfill = library_backfill

    # 前端静态资源：CSS / JS / 图片（重构后 gallery.html → static/index.html + 多模块）
    # mount 在 routes 注册之后：Starlette 的 mount 作为路由表的 fallback，
    # 所以 /api/* 的精确路由仍优先匹配，只有未匹配请求才会落到 StaticFiles。
    # 用 VersionedStaticFiles 支持版本化文件名（cache-busting）：
    #   /static/js/wanted.<hash>.js → 命中后还原到磁盘的 wanted.js
    # 浏览器看到 URL 变了 → 必定发新请求 → 修改 CSS/JS 后用户刷新立刻生效，
    # 不依赖 Cache-Control 头（手机浏览器也不会无限期吃 disk cache）。
    # 版本化的 URL 由 pages.py 在吐 index.html 时动态注入。
    static_dir = PACKAGE_ROOT / "static"
    if static_dir.exists():
        app.mount("/static", VersionedStaticFiles(directory=static_dir), name="static")

    register_routes(app)

    return app