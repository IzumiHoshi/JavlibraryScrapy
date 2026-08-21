"""页面路由：GET /, /wanted, /library —— 统一返回同一份 HTML 模板。

P3 优化 + 开发友好：模板文件 (79KB) 启动期读一次缓存在内存，避免每次请求都
disk I/O；同时检测文件 mtime —— 改了 ``gallery.html`` 刷新页面即生效，无须重启。

之前的实现是「进程内只读一次」，开发期改了模板必须重启服务才能看到效果，跟
``docs/refresh-flows.md`` / ``CLAUDE.md`` 里的承诺不一致。本模块修了这个 bug。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

# 模板在 javlibraryscrapy/templates/gallery.html，统一在 _paths.py 算好
from javlibraryscrapy._paths import PACKAGE_ROOT
TEMPLATE_PATH = PACKAGE_ROOT / "templates" / "gallery.html"

logger = logging.getLogger("gallery.pages")

# ---- 模板内存缓存（P3）----
# 启动期读一次缓存到内存；每次请求 stat 文件 mtime，变了才重读。
# 79KB 的 read_text 比 stat 慢 1000+ 倍，stat 几乎免费（Windows file system cache）。
_template_cache: Optional[str] = None
_template_mtime_ns: Optional[int] = None
_template_cache_lock = threading.Lock()


def _get_template() -> Optional[str]:
    """惰性加载模板到内存；磁盘 mtime 变化时自动重读。

    行为：
    - 启动首次调用：读盘 + 缓存
    - 文件未变：直接返回缓存（每请求 stat 一次，但 stat 是 μs 级，开销忽略）
    - 文件变了：重读 + 替换缓存（无锁阻塞极短，只在持有锁时做 read_text）
    """
    global _template_cache, _template_mtime_ns
    if not TEMPLATE_PATH.exists():
        logger.error(f"模板不存在：{TEMPLATE_PATH}")
        return None
    try:
        mtime_ns = TEMPLATE_PATH.stat().st_mtime_ns
    except OSError as e:
        # stat 失败（文件被独占锁等）：回退到旧缓存，宁可陈旧也别 500
        logger.warning(f"stat 模板失败：{e}，回退到旧缓存")
        return _template_cache
    # 快速路径：缓存命中（无锁）
    if _template_cache is not None and _template_mtime_ns == mtime_ns:
        return _template_cache
    # 慢路径：文件变了或首次加载
    with _template_cache_lock:
        # 双检：拿到锁后再确认一次，避免并发请求重复 read
        if _template_cache is not None and _template_mtime_ns == mtime_ns:
            return _template_cache
        try:
            body = TEMPLATE_PATH.read_text(encoding="utf-8")
            _template_cache = body
            _template_mtime_ns = mtime_ns
            logger.info(f"模板已加载 {len(body)} bytes（{TEMPLATE_PATH}）")
            return body
        except OSError as e:
            logger.error(f"读取模板失败：{e}")
            return _template_cache  # 回退到旧缓存（如果有）


def register(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    @app.get("/wanted", response_class=HTMLResponse)
    @app.get("/library", response_class=HTMLResponse)
    async def _page(request: Request) -> HTMLResponse:
        body = _get_template()
        if body is None:
            return HTMLResponse(
                f"缺少页面模板：{TEMPLATE_PATH}",
                status_code=500,
            )
        return HTMLResponse(
            body,
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )