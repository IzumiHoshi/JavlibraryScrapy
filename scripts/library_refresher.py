"""
刷新单个本地库影片：重新爬取 JAVBus，写 NFO + 封面到现有目录。

用法：
    from library_refresher import refresh_library_movie
    result = asyncio.run(refresh_library_movie(
        folder=Path("Z:\\\\JAV\\\\ABF-340 title"),
        carid="ABF-340",
        javbus_url="https://www.javbus.com/",
        proxy="http://127.0.0.1:10808",
    ))
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from scrapling.fetchers import AsyncDynamicSession

from javbus_scrapling import JavbusSpider
from library_scanner import COVER_NAMES, FANART_NAMES, VIDEO_EXTENSIONS
from utils.filesave import rename, write_xml
from utils.fanart import split_poster_from_fanart

logger = logging.getLogger("library_refresher")

LogCallback = Optional[Callable[[int, str], None]]


async def refresh_library_movie(
    folder: Path,
    carid: str,
    javbus_url: str,
    proxy: Optional[str] = None,
    log_callback: LogCallback = None,
) -> Dict[str, Any]:
    """
    重新爬取单个本地库影片，覆盖 NFO + 封面。

    策略：
    - 临时把 ``JavbusSpider.root_dir`` 指向影片目录，让 ``download_cover`` 直接写入
    - 解析完成后用现有的 ``filename_prefix = "<carid> <title>"`` 命名 NFO（与原文件一致）
    - 封面下载后重命名为 ``fanart.png``，再裁剪出 ``poster.png``
    - 同时清理旧的 ``{carid}.png`` 与同名 .jpg 文件，避免残留

    Returns:
        {"ok": bool, "title": str, "nfo_path": str|None,
         "fanart_path": str|None, "poster_path": str|None, "error": str|None}
    """
    folder = Path(folder)
    if not folder.is_dir():
        return _fail(f"目录不存在：{folder}", log_callback)

    def emit(level: int, msg: str) -> None:
        logger.log(level, msg)
        if log_callback:
            try:
                log_callback(level, msg)
            except Exception:  # noqa: BLE001
                pass

    # 找第一个视频文件（仅用于 process_movie 的兼容，这里只校验存在）
    first_video: Optional[Path] = None
    for ext in VIDEO_EXTENSIONS:
        candidates = list(folder.glob(f"*{ext}")) + list(folder.glob(f"*{ext.upper()}"))
        if candidates:
            first_video = candidates[0]
            break
    if first_video is None:
        return _fail(f"目录下未找到视频文件：{folder}", log_callback)

    spider = JavbusSpider(root_dir=folder)  # 关键：让 download_cover 直接写入 folder
    spider.proxy_enabled = proxy is not None
    spider.proxy = proxy

    # 清理陈旧的临时封面（之前失败留下的 {carid}.png）
    stale = folder / f"{carid}.png"
    if stale.exists():
        try:
            stale.unlink()
        except OSError as e:
            emit(logging.WARNING, f"清理陈旧文件失败：{stale} ({e})")

    # 清理旧的 poster/folder/cover/fanart（多种扩展名 + .jpg 等），避免新旧并存
    for pattern in (COVER_NAMES | FANART_NAMES):
        for p in folder.glob(pattern):
            try:
                p.unlink()
            except OSError:
                pass

    url = f"{javbus_url}{carid}"
    emit(logging.INFO, f"开始刷新 {carid}：{url}")

    try:
        async with AsyncDynamicSession(
            load_dom=spider.load_dom,
            network_idle=spider.network_idle,
            disable_resources=spider.disable_resources,
            proxy=spider.proxy,
            headless=spider.headless,
            timeout=spider.timeout,
        ) as session:
            page = await session.fetch(url)
            emit(logging.INFO, f"已获取页面：{carid}")
            info = await spider.parse(page)

        if not info.get("title") or not info.get("carid"):
            return _fail("JAVBus 页面缺少标题或车牌", log_callback)

        filename_prefix = f"{info['carid']} {info['title'].strip()}"
        emit(logging.INFO, f"标题：{info['title']}")

        # 写 NFO（覆盖）
        nfo_filename = folder / f"{filename_prefix}.nfo"
        write_xml(nfo_filename, info)
        emit(logging.INFO, f"已写入 NFO：{nfo_filename.name}")

        # 处理封面：info["cover"] 已经是 folder/{carid}.png（download_cover 写的）
        cover = info.get("cover", "")
        fanart_path = folder / "fanart.png"
        poster_path = folder / "poster.png"

        if cover and Path(cover).exists():
            rename(Path(cover), fanart_path)
            try:
                split_poster_from_fanart(fanart_path, poster_path)
                emit(logging.INFO, f"已写入封面：{fanart_path.name}, {poster_path.name}")
            except Exception as e:  # noqa: BLE001
                emit(logging.WARNING, f"封面裁剪失败（仅 fanart 已写入）：{e}")
        else:
            emit(logging.WARNING, "未下载到封面（CDN 403 / 解析失败？）")

        emit(logging.INFO, f"刷新完成：{folder.name}")
        return {
            "ok": True,
            "title": info["title"],
            "nfo_path": str(nfo_filename),
            "fanart_path": str(fanart_path) if fanart_path.exists() else None,
            "poster_path": str(poster_path) if poster_path.exists() else None,
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        return _fail(str(e), log_callback)


def _fail(msg: str, log_callback: LogCallback = None) -> Dict[str, Any]:
    if log_callback:
        try:
            log_callback(logging.ERROR, msg)
        except Exception:  # noqa: BLE001
            pass
    logger.error(msg)
    return {
        "ok": False,
        "title": "",
        "nfo_path": None,
        "fanart_path": None,
        "poster_path": None,
        "error": msg,
    }


def refresh_library_movie_sync(*args, **kwargs) -> Dict[str, Any]:
    """同步便捷封装：``asyncio.run(refresh_library_movie(...))``。"""
    return asyncio.run(refresh_library_movie(*args, **kwargs))