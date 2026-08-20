"""URL 重写辅助。

集中 cover 代理 URL 的改写逻辑，避免在多个路由里复制同一段。
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, Optional


def maybe_proxy_cover(state: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    """如果服务启用了 ``image_proxy``，把 ``cover_url`` 重写成 ``/api/cover?url=...``。

    行为：

    - 取 ``item['cover']`` 或 ``item['cover_url']``（前者优先）。
    - 为空或 state 为 None → 原样返回。
    - ``state.image_proxy`` 为真且当前还不是代理 URL → 改写 ``cover`` 字段。

    在 ``movies`` / ``wanted`` 两个路由里共用，保持行为一致。
    """
    cover = item.get("cover") or item.get("cover_url")
    if not cover:
        return item
    if state is None:
        return item
    use_proxy: bool = bool(getattr(state, "image_proxy", False))
    if use_proxy and not cover.startswith("/api/cover?"):
        item["cover"] = "/api/cover?url=" + urllib.parse.quote(cover, safe="")
    return item


def proxied_url(cover: Optional[str], use_proxy: bool) -> Optional[str]:
    """工具函数：给定一个 cover URL 字符串，返回是否改写后的版本。

    用于像 movies.py 这样在循环里逐项处理且不想复制字典的场景。
    """
    if not cover or not use_proxy or cover.startswith("/api/cover?"):
        return cover
    return "/api/cover?url=" + urllib.parse.quote(cover, safe="")