"""极空间（zspace）NAS 集成：把 wanted 抓到的磁力提交到 NAS 下载。

包内封装 zspace_skill/nas/（vendored as :mod:`.zspace_nas`），复用其
RSA 登录 + cookie + token 续期逻辑。本模块只做两件事：

1. 把 ``settings.zspace_*`` 注入到 ``os.environ``（vendored 包从 env 读配置，
   且 ``NAS_BASE`` 在 import 时即被算好），再懒加载 vendored ``NasClient``。
2. 暴露 ``submit_magnet`` / ``list_downloads`` 高层方法。

注意
----
- ``/downloader/share/add`` 的 body schema 在 zspace_skill 仓库里被标注"待测"，
  本模块按推断（``url`` / ``downloadDir`` / ``type=magnet``）提交；NAS 真返回
  错误时把原始响应透传出去，方便上层定位字段名问题。
- ``/downloader/list`` 是已验证可用的 list 接口。
- 客户端实例是单例（``app.state.zspace``），多线程/多请求复用同一会话，
  ``asyncio.Lock`` 防止并发重登。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from ..config import Settings

logger = logging.getLogger("gallery.zspace")


class ZSpaceError(RuntimeError):
    """调用 NAS 出错时抛出（包装 RuntimeError 让路由能区分）。"""


class ZSpaceClient:
    """极空间 NAS 客户端（包装 vendored ``zspace_nas.NasClient``）。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._nas: Any = None  # 真正实例化推迟到第一次调用，避免启动期就发登录请求
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _ensure_env(self) -> None:
        """把 Settings.zspace_* 写到 ``os.environ``，供 vendored nas 包读取。

        vendored ``nas/proto.py`` 在 import 时按 ``NAS_HOST`` 算 ``NAS_BASE``，
        所以必须先设 env 再 import。模块被 Python 缓存后 ``NAS_BASE`` 不变 —
        因此 ``ZSpaceClient`` 是单例 + 第一调用前完成 env 设置。
        """
        s = self._settings
        if s.zspace_host:
            os.environ["NAS_HOST"] = s.zspace_host
        if s.zspace_user:
            os.environ["NAS_USER"] = s.zspace_user
        if s.zspace_password:
            os.environ["NAS_PASSWORD"] = s.zspace_password
        if s.zspace_device_id:
            os.environ["NAS_DEVICE_ID"] = s.zspace_device_id

    async def _ensure_client(self) -> Any:
        """懒加载 + 并发安全的 NasClient 单例。"""
        if self._nas is not None:
            return self._nas
        async with self._lock:
            if self._nas is None:
                self._ensure_env()
                # 必须在 _ensure_env() 之后 import（proto.py 在 import 时算 NAS_BASE）
                from .zspace_nas import NasClient
                self._nas = NasClient()
        return self._nas

    # ------------------------------------------------------------------ #
    # 公开 API
    # ------------------------------------------------------------------ #
    async def submit_magnet(self, magnet_url: str, download_dir: str) -> Dict[str, Any]:
        """提交单个磁力到极空间下载器。

        返回 ``nas.post()`` 的原始 dict（NAS 业务码在 ``code`` 字段）。
        抛 :class:`ZSpaceError` 表示登录/网络层失败；业务码非 200 不抛，
        由调用方根据 ``code`` 判断。
        """
        nas = await self._ensure_client()
        body = {
            "url": magnet_url,
            "downloadDir": download_dir,
            "type": "magnet",
        }
        try:
            return await nas.post("/downloader/share/add", body)
        except RuntimeError as e:
            # 登录失败 / N001414（设备未验证）等。包装成 ZSpaceError 方便上层定位。
            raise ZSpaceError(str(e)) from e

    async def list_downloads(self) -> Dict[str, Any]:
        """列出当前 NAS 下载任务（POST ``/downloader/list`` body ``{}``）。"""
        nas = await self._ensure_client()
        try:
            return await nas.post("/downloader/list", {})
        except RuntimeError as e:
            raise ZSpaceError(str(e)) from e

    async def aclose(self) -> None:
        """关闭 vendored httpx 客户端（lifespan 退出时调用）。"""
        if self._nas is not None:
            try:
                await self._nas.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._nas = None


def is_configured(settings: Settings) -> bool:
    """检查 Settings 是否满足启用 zspace 的最低配置。

    缺任意一项都视为未配置（路由层会 503）。"""
    return bool(
        settings.zspace_enabled
        and settings.zspace_host
        and settings.zspace_user
        and settings.zspace_password
    )