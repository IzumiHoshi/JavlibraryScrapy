"""极空间 NAS 配置（运行时只读，源是 .env）。

设计
----
- 所有字段都从 :class:`Settings` 的 ``zspace_*`` 字段读取，启动时构造一次；
  修改配置 = 改 ``.env`` + 重启服务，不再提供运行时编辑入口。
- :class:`ZSpaceConfigStore` 只是内存里的配置 holder，给路由 + ``ZSpaceClient``
  提供 ``get()`` callable；既不读盘也不写盘。
- :class:`ZSpaceConfig` dataclass 保留 mask_password 等序列化方法，
  方便路由在响应里遮蔽密码。

数据结构
--------
.. code-block:: json

    {
      "enabled": true,
      "host": "192.168.1.100",
      "user": "138xxxxxxxx",
      "password": "xxxxxxxx",
      "device_id": "",
      "download_path": "/sata14/my/data/zvideo/JAV"
    }
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from ..config import Settings

logger = logging.getLogger("gallery.zspace_config")


@dataclass
class ZSpaceConfig:
    """极空间配置（只读快照）。"""

    enabled: bool = False
    host: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None
    device_id: Optional[str] = None
    download_path: str = "/sata14/my/data/zvideo/JAV"

    def is_configured(self) -> bool:
        """启用 + 3 个必填字段都非空才算"配置完成"（device_id 可选；routes/zspace 503 守门用）。"""
        return bool(
            self.enabled
            and self.host
            and self.user
            and self.password
        )

    def to_dict(self, mask_password: bool = False) -> Dict[str, Any]:
        """转 dict 供 API 返回。``mask_password=True`` 时把 password 替换成
        ``"********"``（仅显示用，存盘不丢明文）。"""
        d = asdict(self)
        if mask_password and d.get("password"):
            d["password"] = "********"
        return d


class ZSpaceConfigStore:
    """极空间配置 holder：从 :class:`Settings` 一次性构造，运行时只读。

    启动期由 :func:`create_app` 注入 ``app.state.zspace_config_store``；
    路由通过 ``store.get()`` 拿到当前 ``ZSpaceConfig`` 快照判断是否启用、
    取 ``download_path`` 默认值；``ZSpaceClient`` 通过构造时传入的
    ``get_config`` callable 拉到最新值。
    """

    def __init__(self, settings: Settings) -> None:
        self._config = ZSpaceConfig(
            enabled=settings.zspace_enabled,
            host=settings.zspace_host,
            user=settings.zspace_user,
            password=settings.zspace_password,
            device_id=settings.zspace_device_id,
            download_path=settings.zspace_download_path or "/sata14/my/data/zvideo/JAV",
        )

    def get(self) -> ZSpaceConfig:
        """返回当前配置的浅拷贝 dataclass（防调用方改坏内部状态）。"""
        return ZSpaceConfig(**asdict(self._config))
