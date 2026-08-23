"""极空间 NAS 配置持久化（JSON 文件 + .env 初始种子）。

把 zspace 配置从 .env 迁到运行时可编辑的 JSON 文件（``output/zspace_config.json``），
用户直接在网页上填写，不再需要手改 .env / 重启服务。

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

字段语义对齐 ``Settings.zspace_*``；``.env`` 的同名变量在首次启动时作为初始
默认值（被 JSON 取代后 .env 仍然能用于 force-overwrite 但默认行为是 JSON 优先）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import Settings

logger = logging.getLogger("gallery.zspace_config")


CONFIG_FILENAME = "zspace_config.json"


@dataclass
class ZSpaceConfig:
    """极空间配置（线程安全，由 :class:`ZSpaceConfigStore` 管理持久化）。"""

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

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ZSpaceConfig":
        """从 dict 构造。空字段走默认；多余字段忽略。"""
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in allowed})


class ZSpaceConfigStore:
    """线程安全的 zspace 配置存储（单进程内单例）。

    JSON 文件位置：``<output_dir>/zspace_config.json``，由 create_app 时确定。
    首次加载：若 JSON 不存在，从 Settings.zspace_* 拷一份作为种子并立即落盘，
    后续以 JSON 为准。
    """

    def __init__(self, output_dir: Path, settings: Settings) -> None:
        self._path = Path(output_dir) / CONFIG_FILENAME
        self._lock = threading.RLock()
        # 从 JSON 读，缺失/损坏则从 settings 兜底
        cfg = self._load_from_disk() or ZSpaceConfig(
            enabled=settings.zspace_enabled,
            host=settings.zspace_host,
            user=settings.zspace_user,
            password=settings.zspace_password,
            device_id=settings.zspace_device_id,
            download_path=settings.zspace_download_path or "/sata14/my/data/zvideo/JAV",
        )
        with self._lock:
            self._config = cfg
        # JSON 不存在时落盘一次（避免下次重启再走 settings fallback）
        if not self._path.exists():
            self._save_locked()

    # ------------------------------------------------------------------ #
    # 公开 API
    # ------------------------------------------------------------------ #
    def get(self) -> ZSpaceConfig:
        with self._lock:
            return ZSpaceConfig(**asdict(self._config))

    def update(self, patch: Dict[str, Any]) -> ZSpaceConfig:
        """部分更新（POST /api/zspace/config 用）。空字符串视同 None。

        - 空 password 视为"保持原值"，避免误清空
        - password 取遮蔽值 ``"********"``（GET 返回的占位符）也视同"保持原值"，
          防止 API 客户端读到遮蔽值后 POST 回来把密码覆盖成 8 个星号
        - 其它空字符串视同 None
        """
        _MASK = "********"
        with self._lock:
            cur = asdict(self._config)
            for k, v in patch.items():
                if k not in cur:
                    continue
                if k == "password":
                    # 空 password 或遮蔽值 = 不修改
                    if isinstance(v, str) and v in ("", _MASK):
                        continue
                    cur[k] = v
                elif isinstance(v, str) and v.strip() == "":
                    cur[k] = None
                elif isinstance(v, bool):
                    cur[k] = bool(v)
                else:
                    cur[k] = v
            self._config = ZSpaceConfig.from_dict(cur)
            self._save_locked()
            return ZSpaceConfig(**asdict(self._config))

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _load_from_disk(self) -> Optional[ZSpaceConfig]:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"读 {self._path} 失败，走 .env 兜底：{e}")
            return None
        if not isinstance(data, dict):
            logger.warning(f"{self._path} 内容不是 dict，走 .env 兜底")
            return None
        try:
            return ZSpaceConfig.from_dict(data)
        except (TypeError, ValueError) as e:
            logger.warning(f"{self._path} 字段不合法，走 .env 兜底：{e}")
            return None

    def _save_locked(self) -> None:
        """写入磁盘。必须在 self._lock 持有时调用。

        原子写：先写 .tmp 再 rename。失败时清理半成品 .tmp 并传播 OSError，
        让路由层能把"未持久化"的事实反映给用户（之前是静默 log + 返回成功，
        配置只活在内存里，重启就丢 —— Sourcery review flag）。
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            tmp.write_text(
                json.dumps(self._config.to_dict(mask_password=False),
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError:
            # 清理半成品 tmp（避免下次再写时撞到旧文件；同时不污染 output/）
            try:
                tmp.unlink()
            except OSError:
                pass
            raise