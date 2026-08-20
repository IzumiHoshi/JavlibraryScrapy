"""封面与本地资源：服务端代理拉图、本地库封面读取、用资源管理器打开目录。"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple

import requests

from javlibraryscrapy.library.scanner import COVER_NAMES, FANART_NAMES

logger = logging.getLogger("gallery.covers")


def fetch_cover(
    url: str,
    cache_dir: Path,
    user_agent: str,
    timeout: int,
    verify_ssl: bool,
    cover_proxy: Optional[str],
    referer: str = "https://www.javlibrary.com/",
) -> Optional[Tuple[bytes, str]]:
    """服务端拉取封面（带代理与磁盘缓存），返回 (内容, content-type)。"""
    if not url.startswith(("http://", "https://")):
        return None

    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        suffix = ".jpg"
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }[suffix]

    cache_file = cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + suffix)
    if cache_file.exists():
        return cache_file.read_bytes(), content_type

    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": user_agent,
                "Referer": referer,
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            },
            timeout=timeout,
            proxies=(
                {"http": cover_proxy, "https": cover_proxy} if cover_proxy else None
            ),
            verify=verify_ssl,
        )
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"封面下载失败 {url}: {e}")
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(resp.content)
    return resp.content, resp.headers.get("Content-Type", content_type)


def find_local_cover(folder: Path, name: str = "") -> Optional[Path]:
    """从本地库目录下挑选封面。未指定 name 时按 poster.* > folder.* > cover.* 顺序。

    ``name`` 指定时只接受白名单中的文件名（COVER_NAMES ∪ FANART_NAMES）。
    """
    if name:
        if name.lower() not in (COVER_NAMES | FANART_NAMES):
            return None
        p = folder / name
        return p if p.is_file() else None

    for n in COVER_NAMES:
        p = folder / n
        if p.is_file():
            return p
    return None


def open_in_explorer(path: Path) -> None:
    """用系统资源管理器打开本地文件夹。"""
    path_str = str(path)
    if sys.platform == "win32":
        os.startfile(path_str)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", path_str], check=True)
    else:
        subprocess.run(["xdg-open", path_str], check=True)


def guess_cover_content_type(suffix: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(suffix.lower(), "application/octet-stream")