"""极空间（zspace）NAS 集成：把 wanted 抓到的磁力提交到 NAS 下载。

包内封装 zspace_skill/nas/（vendored as :mod:`.zspace_nas`），复用其
RSA 登录 + cookie + token 续期逻辑。本模块只做两件事：

1. 从 :class:`~javlibraryscrapy.server.services.zspace_config.ZSpaceConfig`
   读配置（不是 .env）—— 用户通过网页 UI 改完立即生效。
2. 暴露 ``submit_magnet`` / ``list_downloads`` 高层方法。

注意
----
- 配置变更检测：每次调用前 hash 一下 ``(host, user, password, device_id)``，
  变了就 aclose 旧 NasClient + 重 build 新实例（vendored 客户端从 module-level
  env 读配置，重 build 是唯一干净的切换方式）。
- ``/downloader/share/add`` 的 body schema 在 zspace_skill 仓库里被标注"待测"，
  本模块按推断（``url`` / ``downloadDir`` / ``type=magnet``）提交；NAS 真返回
  错误时把原始响应透传出去，方便上层定位字段名问题。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Dict, Optional

from .zspace_config import ZSpaceConfig

logger = logging.getLogger("gallery.zspace")


class ZSpaceError(RuntimeError):
    """调用 NAS 出错时抛出（包装 RuntimeError 让路由能区分）。"""


def _config_signature(cfg: ZSpaceConfig) -> tuple:
    """配置指纹：4 个会影响 NasClient 行为的字段。"""
    return (cfg.host, cfg.user, cfg.password, cfg.device_id)


class ZSpaceClient:
    """极空间 NAS 客户端（包装 vendored ``zspace_nas.NasClient``）。

    参数
    ----
    get_config : Callable[[], ZSpaceConfig]
        返回当前配置的 callable（每次访问 ``_ensure_client`` 时拉最新值）。
        通常传 ``app.state.zspace_config_store.get``。
    """

    def __init__(self, get_config: Callable[[], ZSpaceConfig]) -> None:
        self._get_config = get_config
        self._nas: Any = None
        self._sig: Optional[tuple] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _ensure_env(self, cfg: ZSpaceConfig) -> None:
        """把 cfg 的字段写到 ``os.environ``，供 vendored nas 包读取。

        vendored ``nas/proto.py`` 在 import 时按 ``NAS_HOST`` 算 ``NAS_BASE``，
        所以必须先设 env 再 import。模块被 Python 缓存后 ``NAS_BASE`` 不变 -
        因此配置变更时必须 aclose 旧 client + 重 build（见 ``_ensure_client``）。
        """
        if cfg.host:
            os.environ["NAS_HOST"] = cfg.host
        if cfg.user:
            os.environ["NAS_USER"] = cfg.user
        if cfg.password:
            os.environ["NAS_PASSWORD"] = cfg.password
        if cfg.device_id:
            os.environ["NAS_DEVICE_ID"] = cfg.device_id

    async def _ensure_client(self) -> Any:
        """懒加载 + 配置变更检测：cfg 变了就 aclose + 重建。"""
        cfg = self._get_config()
        sig = _config_signature(cfg)
        if self._nas is not None and sig == self._sig:
            return self._nas
        async with self._lock:
            # 二次检查：可能其他协程已经重建过了
            if self._nas is not None and sig == self._sig:
                return self._nas
            if self._nas is not None:
                # 配置变了 → 关闭旧 client（drop httpx pool + cookies）
                try:
                    await self._nas.aclose()
                except Exception:  # noqa: BLE001
                    pass
                self._nas = None
            self._ensure_env(cfg)
            # 必须在 _ensure_env() 之后 import（proto.py 在 import 时算 NAS_BASE）
            from .zspace_nas import NasClient
            self._nas = NasClient()
            self._sig = sig
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