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

import httpx

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

        vendored patch (2026-08-23)：version/device/_l 也设 env vars，让
        ``proto.common_query`` 输出 web UI 实际用的版本号和 PC 设备标识。
        """
        if cfg.host:
            os.environ["NAS_HOST"] = cfg.host
        if cfg.user:
            os.environ["NAS_USER"] = cfg.user
        if cfg.password:
            os.environ["NAS_PASSWORD"] = cfg.password
        if cfg.device_id:
            os.environ["NAS_DEVICE_ID"] = cfg.device_id
        # vendored patch：vendor 的 NAS_BASE 在 import 时算，但那时 host 还没设。
        # 这里补设 + reload proto 模块（如果已 import），让 NAS_BASE 用真实 host。
        if cfg.host:
            os.environ["NAS_BASE"] = f"http://{cfg.host}:5055"
            try:
                import importlib
                import sys
                from . import zspace_nas
                if "javlibraryscrapy.server.services.zspace_nas.proto" in sys.modules:
                    importlib.reload(sys.modules["javlibraryscrapy.server.services.zspace_nas.proto"])
                importlib.reload(zspace_nas)
            except Exception:
                pass
        # vendor 原值与真实 web UI 不一致；先设默认值，第一次 _ensure_client 时
        # 由 login 响应回填准确 version
        os.environ.setdefault("NAS_VERSION", "2.3.2025112601")
        os.environ.setdefault("NAS_DEVICE", "PC")
        os.environ.setdefault("NAS_LANG", "zh_cn")

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
            # login 拿到 data.version 后回填 NAS_VERSION，让后续 common_query 用真实版本
            if not self._nas._logged_in:
                await self._nas.login()
                # data.version 是 int (NAS 注册时间戳)，env var 必须是 str
                ver = str(self._nas._cookies.get("version", "") or os.environ.get("NAS_VERSION", "2.3.2025112601"))
                os.environ["NAS_VERSION"] = ver
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

        vendored patch (2026-08-23)：web UI 实际端点是 ``/downloader/add/link``，
        body 字段是 ``uri`` + ``dir``（不是 ``url``/``downloadDir``），
        公共参数（version/device_id/device/_l/token/nasid）必须**放在 body** 里
        而不是 query string——vendor 注释里猜的 ``/downloader/share/add`` + 字段名
        是错的，会被 NAS 业务层拒为 N202003。
        """
        nas = await self._ensure_client()
        # 触发 login（如果还没），登录后 _cookies 里有 token/nas_id/version
        cookies = dict(nas._cookies)
        token = cookies.get("token", "")
        nasid = cookies.get("nas_id", "")
        version = str(cookies.get("version", "") or os.environ.get("NAS_VERSION", "2.3.2025112601"))
        device_id = nas._device_id
        body = {
            "uri": magnet_url,
            "dir": download_dir,
            "plat": "web",
            "version": version,
            "device_id": device_id,
            "device": cookies.get("device", "PC"),
            "_l": cookies.get("_l", "zh_cn"),
            "token": token,
            "nasid": nasid,
        }
        base = os.environ.get('NAS_BASE') or 'http://' + os.environ.get('NAS_HOST', '') + ':5055'
        url = f"{base}/downloader/add/link"
        try:
            r = await nas._client.post(url, data=body, cookies=cookies)
            try:
                return r.json()
            except Exception:
                return {"_status": r.status_code, "_raw": r.text[:300]}
        except RuntimeError as e:
            raise ZSpaceError(str(e)) from e
        except (httpx.HTTPError, ValueError) as e:
            raise ZSpaceError(f"{type(e).__name__}: {e}") from e

    async def list_downloads(self) -> Dict[str, Any]:
        """列出当前 NAS 下载任务（POST ``/downloader/list`` body ``{}``）。

        vendored patch (2026-08-23)：list 端点也能跑通，但需要 cookies 里带
        ``nas_id``（=qc_name）。Vendored client 之前 cookies 没存这个字段。
        """
        nas = await self._ensure_client()
        try:
            return await nas.post("/downloader/list", {})
        except RuntimeError as e:
            raise ZSpaceError(str(e)) from e
        except (httpx.HTTPError, ValueError) as e:
            raise ZSpaceError(f"{type(e).__name__}: {e}") from e

    async def aclose(self) -> None:
        """关闭 vendored httpx 客户端（lifespan 退出时调用）。"""
        if self._nas is not None:
            try:
                await self._nas.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._nas = None