"""URL 重写辅助。

集中 cover 代理 URL 的改写逻辑，避免在多个路由里复制同一段。
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Optional

# 前端请求 cover 字节流时走这个 URL 前缀（与 routes/covers.py 的注册路径一致）
COVER_PROXY_PREFIX = "/api/cover?url="


def proxied_url(cover: Optional[str], state: Any) -> Optional[str]:
    """给定 cover URL 与服务 state，返回改写后的 URL。

    - ``state is None`` 或 ``state.image_proxy`` 为假 → 原样返回
    - ``cover`` 已经以 ``/api/cover?`` 开头 → 原样返回（防止重复嵌套）
    - ``cover`` 为空 → 返回 ``None``

    在 ``movies`` / ``wanted`` 两个路由里共用，保持行为一致。
    """
    if not cover:
        return cover
    if state is None:
        return cover
    if not bool(getattr(state, "image_proxy", False)):
        return cover
    if cover.startswith(COVER_PROXY_PREFIX):
        return cover
    return COVER_PROXY_PREFIX + urllib.parse.quote(cover, safe="")