"""``ZSpaceConfig`` 纯逻辑 + ``ZSpaceConfigStore`` 从 Settings 构造的测试。

注意：zspace 配置现在完全从 .env 读取、运行时只读（不再落盘 / 不再有 UI 编辑
入口）。所以这个文件只测：
  1. ``ZSpaceConfig.is_configured()`` 的字段判定（device_id 可选）
  2. ``ZSpaceConfig.to_dict(mask_password=...)`` 的遮蔽
  3. ``ZSpaceConfigStore(settings)`` 把 6 个字段从 Settings 拷到内部 dataclass

旧的"update / 落盘 / OSError 传播 / 路由层 POST /api/zspace/config"测试
已删除，因为对应的端点不再存在。

运行::

    uv run python tests/unit/test_zspace_config.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from javlibraryscrapy.server.config import Settings  # noqa: E402
from javlibraryscrapy.server.services.zspace_config import (  # noqa: E402
    ZSpaceConfig,
    ZSpaceConfigStore,
)


def _make_settings(**overrides) -> Settings:
    """最小 Settings：只关心 zspace 字段，其它走默认。"""
    base = dict(
        zspace_enabled=True,
        zspace_host="192.168.1.100",
        zspace_user="13800000000",
        zspace_password="original_pass",
        zspace_device_id="",
        zspace_download_path="/pool/zvideo/JAV",
    )
    base.update(overrides)
    return Settings(**base)


def _seed(s: Settings) -> ZSpaceConfig:
    """手工造一个 ZSpaceConfig（避开 ZSpaceConfigStore 持有 settings 的耦合）。"""
    return ZSpaceConfig(
        enabled=s.zspace_enabled,
        host=s.zspace_host,
        user=s.zspace_user,
        password=s.zspace_password,
        device_id=s.zspace_device_id,
        download_path=s.zspace_download_path or "/sata14/my/data/zvideo/JAV",
    )


# ====================================================================== #
# is_configured()：3 个必填 + enabled
# ====================================================================== #
class TestIsConfigured(unittest.TestCase):
    """``is_configured`` 是 routes/zspace.py 503 守门用。"""

    def test_all_required_set_returns_true(self):
        self.assertTrue(_seed(_make_settings()).is_configured())

    def test_disabled_returns_false(self):
        s = _make_settings(zspace_enabled=False)
        self.assertFalse(_seed(s).is_configured())

    def test_missing_host_returns_false(self):
        s = _make_settings(zspace_host=None)
        self.assertFalse(_seed(s).is_configured())

    def test_missing_user_returns_false(self):
        s = _make_settings(zspace_user=None)
        self.assertFalse(_seed(s).is_configured())

    def test_missing_password_returns_false(self):
        s = _make_settings(zspace_password=None)
        self.assertFalse(_seed(s).is_configured())

    def test_device_id_optional(self):
        """device_id 缺失不影响 is_configured（NAS auth 会自动生成）。"""
        s = _make_settings(zspace_device_id=None)
        self.assertTrue(_seed(s).is_configured())


# ====================================================================== #
# to_dict(mask_password=...)
# ====================================================================== #
class TestToDictMaskPassword(unittest.TestCase):
    """响应里密码必须是 ``"********"``，明文绝不能从 API 暴露。"""

    def test_mask_password_replaces_value(self):
        cfg = _seed(_make_settings(zspace_password="super_secret"))
        d = cfg.to_dict(mask_password=True)
        self.assertEqual(d["password"], "********")

    def test_no_mask_keeps_plain(self):
        cfg = _seed(_make_settings(zspace_password="super_secret"))
        d = cfg.to_dict(mask_password=False)
        self.assertEqual(d["password"], "super_secret")

    def test_mask_with_empty_password_is_noop(self):
        """空 password 不需要遮蔽（也没东西可遮蔽）。"""
        cfg = _seed(_make_settings(zspace_password=""))
        d = cfg.to_dict(mask_password=True)
        self.assertEqual(d["password"], "")

    def test_to_dict_preserves_other_fields(self):
        cfg = _seed(_make_settings(zspace_host="10.0.0.1", zspace_user="alice"))
        d = cfg.to_dict(mask_password=True)
        self.assertEqual(d["host"], "10.0.0.1")
        self.assertEqual(d["user"], "alice")


# ====================================================================== #
# ZSpaceConfigStore(settings)：从 .env 字段构造
# ====================================================================== #
class TestStoreFromSettings(unittest.TestCase):
    """store 只持有从 Settings 拷过来的 dataclass，运行时只读。"""

    def test_get_returns_dataclass_matching_settings(self):
        s = _make_settings(
            zspace_enabled=True,
            zspace_host="10.0.0.42",
            zspace_user="bob",
            zspace_password="hunter2",
            zspace_device_id="abcd",
            zspace_download_path="/pool/bob/JAV",
        )
        store = ZSpaceConfigStore(settings=s)
        cfg = store.get()
        self.assertEqual(asdict(cfg), {
            "enabled": True,
            "host": "10.0.0.42",
            "user": "bob",
            "password": "hunter2",
            "device_id": "abcd",
            "download_path": "/pool/bob/JAV",
        })

    def test_default_download_path_fallback(self):
        """zspace_download_path 为空字符串时回退到 /sata14/my/data/zvideo/JAV。"""
        s = _make_settings(zspace_download_path="")
        store = ZSpaceConfigStore(settings=s)
        self.assertEqual(store.get().download_path, "/sata14/my/data/zvideo/JAV")

    def test_get_returns_copy_not_internal_state(self):
        """修改返回值不应影响 store 内部状态（防御性 copy）。"""
        s = _make_settings(zspace_host="initial")
        store = ZSpaceConfigStore(settings=s)
        cfg = store.get()
        cfg.host = "mutated"  # type: ignore[misc]
        self.assertEqual(store.get().host, "initial")

    def test_disabled_store_is_not_configured(self):
        s = _make_settings(zspace_enabled=False)
        store = ZSpaceConfigStore(settings=s)
        self.assertFalse(store.get().is_configured())

    def test_default_settings_produce_unconfigured_store(self):
        """完全没设 zspace_* 的 Settings → store.is_configured() = False。

        ``_load_dotenv_once`` 在模块导入时强制把项目 .env 加载到 os.environ，
        pydantic-settings 的 env_file 还会再读一次 — 所以必须同时：
          1. 清掉 os.environ 的 ZSPACE_*
          2. 把 Settings.model_config["env_file"] 临时换成空文件
        才能拿到"完全没设 zspace_*"的状态。
        """
        from javlibraryscrapy.server.config import Settings as _Settings
        keys = (
            "ZSPACE_ENABLED", "ZSPACE_HOST", "ZSPACE_USER",
            "ZSPACE_PASSWORD", "ZSPACE_DEVICE_ID", "ZSPACE_DOWNLOAD_PATH",
        )
        saved_env = {k: os.environ.pop(k, None) for k in keys}
        saved_file = _Settings.model_config.get("env_file")
        try:
            # 用不存在的路径绕开 pydantic-settings 的 .env 读取
            _Settings.model_config["env_file"] = str(
                Path(tempfile.gettempdir()) / "_no_such_env_file_for_test"
            )
            s = _Settings()
            store = ZSpaceConfigStore(settings=s)
            cfg = store.get()
            self.assertFalse(cfg.is_configured())
            self.assertEqual(cfg.download_path, "/sata14/my/data/zvideo/JAV")
        finally:
            _Settings.model_config["env_file"] = saved_file
            for k, v in saved_env.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main(verbosity=2)
