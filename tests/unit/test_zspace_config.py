"""``ZSpaceConfigStore`` + ``/api/zspace/config`` 路由测试。

针对 Sourcery 在 bbba65e 上提的 3 条新 issues：

  1. ``_save_locked`` 之前静默吞 ``OSError`` —— POST /api/zspace/config 假装
     成功，配置只活在内存里，重启就丢。修复后必须 raise，路由映射成 500。
  2. ``GET /api/zspace/config`` 返回 ``"********"`` 遮蔽值；客户端如果原样
     ``POST`` 回来，会把真实密码覆写成 8 个星号，破坏后续 NAS 登录。
     修复后 ``update({"password": "********"})`` 视同"保持原值"。
  3. ``is_configured()`` docstring 说"4 字段必填"，实际只检 3 字段
     （device_id 是可选）。修复后 docstring 描述与实现一致。

运行::

    uv run python tests/unit/test_zspace_config.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from javlibraryscrapy.server.app import create_app  # noqa: E402
from javlibraryscrapy.server.config import Settings  # noqa: E402
from javlibraryscrapy.server.services.zspace_config import (  # noqa: E402
    ZSpaceConfig,
    ZSpaceConfigStore,
)


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #
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
    """手工造一个 ZSpaceConfig（避开 ZSpaceConfigStore.__init__ 的落盘副作用）。"""
    return ZSpaceConfig(
        enabled=s.zspace_enabled,
        host=s.zspace_host,
        user=s.zspace_user,
        password=s.zspace_password,
        device_id=s.zspace_device_id,
        download_path=s.zspace_download_path or "/sata14/my/data/zvideo/JAV",
    )


def _async_post(app, url: str, json_body: Optional[Dict[str, Any]] = None) -> httpx.Response:
    """httpx 0.28 的 ASGITransport 只支持 async，统一用 asyncio.run 包一层。"""
    transport = httpx.ASGITransport(app=app)

    async def _do() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.post(url, json=json_body)

    return asyncio.run(_do())


class _TmpDirMixin:
    """每个 case 自己的 tempfile.TemporaryDirectory。"""

    def setUp(self) -> None:
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp_output_dir = Path(self._tmp_ctx.name)

    def tearDown(self) -> None:
        self._tmp_ctx.cleanup()


# ====================================================================== #
# Issue 3: is_configured() 实际只检 3 个必填字段 + enabled
# ====================================================================== #
class TestIsConfigured(unittest.TestCase):
    """``is_configured`` 是 routes/zspace.py 503 守门用，必须与 docstring 一致。"""

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
        """Issue 3：device_id 缺失不影响 is_configured（NAS auth 会自动生成）。"""
        s = _make_settings(zspace_device_id=None)
        self.assertTrue(_seed(s).is_configured())


# ====================================================================== #
# Issue 2: password="********" 视同 "保持原值"，防止 GET→POST 回环
# ====================================================================== #
class TestPasswordUpdate(_TmpDirMixin, unittest.TestCase):
    def test_mask_value_does_not_overwrite(self):
        """GET 遮蔽值再 POST 回来，真实密码不变。"""
        store = ZSpaceConfigStore(
            output_dir=self.tmp_output_dir, settings=_make_settings()
        )
        cfg = store.update({"password": "********"})
        self.assertEqual(cfg.password, "original_pass")

    def test_empty_string_does_not_overwrite(self):
        """回归：空 password 仍走 keep 语义（避免误清空）。"""
        store = ZSpaceConfigStore(
            output_dir=self.tmp_output_dir, settings=_make_settings()
        )
        cfg = store.update({"password": ""})
        self.assertEqual(cfg.password, "original_pass")

    def test_real_password_overwrites(self):
        """真密码仍正常更新。"""
        store = ZSpaceConfigStore(
            output_dir=self.tmp_output_dir, settings=_make_settings()
        )
        cfg = store.update({"password": "new_secret_123"})
        self.assertEqual(cfg.password, "new_secret_123")

    def test_no_password_key_keeps_existing(self):
        """patch 不含 password → 保留原值。"""
        store = ZSpaceConfigStore(
            output_dir=self.tmp_output_dir, settings=_make_settings()
        )
        cfg = store.update({"host": "10.0.0.1"})
        self.assertEqual(cfg.password, "original_pass")
        self.assertEqual(cfg.host, "10.0.0.1")

    def test_round_trip_preserves_password(self):
        """GET → 把遮蔽值 POST 回来 → 再 GET，password 仍 = 原值。"""
        store = ZSpaceConfigStore(
            output_dir=self.tmp_output_dir, settings=_make_settings()
        )
        masked = store.get().to_dict(mask_password=True)
        self.assertEqual(masked["password"], "********")
        store.update({"password": masked["password"]})
        self.assertEqual(store.get().password, "original_pass")

    def test_round_trip_persists_real_password_to_disk(self):
        """遮蔽值回环不能让磁盘上的明文变成 8 星号。"""
        store = ZSpaceConfigStore(
            output_dir=self.tmp_output_dir, settings=_make_settings()
        )
        store.update({"password": "********"})
        raw = json.loads(
            (self.tmp_output_dir / "zspace_config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(raw["password"], "original_pass")


# ====================================================================== #
# Issue 1: _save_locked 写盘失败时传播 OSError
# ====================================================================== #
class TestSaveLockErrorPropagation(_TmpDirMixin, unittest.TestCase):
    def test_oserror_propagates_from_update(self):
        """Issue 1：update() 必须 raise OSError，不能静默 log 假装成功。"""
        store = ZSpaceConfigStore(
            output_dir=self.tmp_output_dir, settings=_make_settings()
        )
        with patch.object(store, "_save_locked", side_effect=OSError("disk full")):
            with self.assertRaises(OSError) as cm:
                store.update({"host": "10.0.0.1"})
            self.assertIn("disk full", str(cm.exception))

    def test_tmp_file_cleaned_on_save_failure(self):
        """Issue 1 附带：写盘失败应清理半成品 .tmp，不污染 output/。"""
        store = ZSpaceConfigStore(
            output_dir=self.tmp_output_dir, settings=_make_settings()
        )
        original_write = Path.write_text

        def fake_write(self, *a, **kw):
            if self.name.endswith(".tmp"):
                raise OSError("simulated disk full")
            return original_write(self, *a, **kw)

        with patch.object(Path, "write_text", fake_write):
            with self.assertRaises(OSError):
                store._save_locked()

        tmp_files = list(self.tmp_output_dir.glob("*.tmp"))
        self.assertEqual(
            tmp_files, [], f"残留 .tmp 文件未清理：{tmp_files}"
        )


# ====================================================================== #
# Issue 1 路由层：OSError → 500 + 可读 detail
# ====================================================================== #
class TestRouteOsErrorMapping(_TmpDirMixin, unittest.TestCase):
    def _make_app(self) -> object:
        # create_app → GalleryState.__init__ → load_movies 要求 data_path 是 JSON 文件
        # （不是目录），空列表足以满足路由测试（不需要真的 wanted 数据）。
        data_path = self.tmp_output_dir / "movies.json"
        data_path.write_text("[]", encoding="utf-8")
        return create_app(
            settings=_make_settings(),
            data_path=data_path,
            output_dir=self.tmp_output_dir,
            no_rescan_on_startup=True,
        )

    def test_update_config_returns_500_on_oserror(self):
        """POST /api/zspace/config 在写盘失败时必须返回 5xx，不能 200 假装成功。"""
        app = self._make_app()
        with patch(
            "javlibraryscrapy.server.services.zspace_config.ZSpaceConfigStore.update",
            side_effect=OSError("磁盘满"),
        ):
            r = _async_post(app, "/api/zspace/config", {"host": "10.0.0.1"})
        self.assertEqual(r.status_code, 500, r.text)
        body = r.json()
        self.assertIn("detail", body)
        # detail 至少要包含"无法写入"，让用户知道是持久化失败而非业务失败
        self.assertTrue(
            "无法写入" in body["detail"] or "磁盘满" in body["detail"],
            f"detail 不够可读：{body['detail']}",
        )

    def test_update_config_succeeds_on_normal_post(self):
        """回归：正常 POST 仍然返回 200 + 遮蔽 password。"""
        app = self._make_app()
        r = _async_post(
            app, "/api/zspace/config", {"host": "10.0.0.42", "password": "newpw"}
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["host"], "10.0.0.42")
        self.assertEqual(body["password"], "********")  # 响应必须遮蔽
        # 磁盘上是真明文
        raw = json.loads(
            (self.tmp_output_dir / "zspace_config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(raw["host"], "10.0.0.42")
        self.assertEqual(raw["password"], "newpw")


# ====================================================================== #
# 基础持久化 + seed（之前已覆盖，这里补全路径）
# ====================================================================== #
class TestPersistenceAndSeed(_TmpDirMixin, unittest.TestCase):
    def test_seed_when_json_missing(self):
        """JSON 不存在时从 .env 兜底并立即落盘。"""
        store = ZSpaceConfigStore(
            output_dir=self.tmp_output_dir, settings=_make_settings()
        )
        cfg = store.get()
        self.assertEqual(cfg.host, "192.168.1.100")
        self.assertEqual(cfg.user, "13800000000")
        self.assertEqual(cfg.password, "original_pass")
        path = self.tmp_output_dir / "zspace_config.json"
        self.assertTrue(path.exists())
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["password"], "original_pass")

    def test_disk_persists_after_update(self):
        store = ZSpaceConfigStore(
            output_dir=self.tmp_output_dir, settings=_make_settings()
        )
        store.update({"host": "10.0.0.99"})
        raw = json.loads(
            (self.tmp_output_dir / "zspace_config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(raw["host"], "10.0.0.99")
        self.assertEqual(raw["password"], "original_pass")

    def test_update_ignores_unknown_keys(self):
        """无关字段不会污染配置（防御 Pydantic extra='ignore' 之外的路径）。"""
        store = ZSpaceConfigStore(
            output_dir=self.tmp_output_dir, settings=_make_settings()
        )
        before = store.get()
        cfg = store.update({"nonexistent_field": "hacker", "host": "10.0.0.1"})
        self.assertEqual(cfg.host, "10.0.0.1")
        after = asdict(cfg)
        for k in asdict(before):
            self.assertIn(k, after)

    def test_atomic_write_no_leftover_tmp_on_success(self):
        """正常 save 后 .tmp 已被 rename，不应残留。"""
        store = ZSpaceConfigStore(
            output_dir=self.tmp_output_dir, settings=_make_settings()
        )
        store.update({"host": "10.0.0.1"})
        tmp_files = list(self.tmp_output_dir.glob("*.tmp"))
        self.assertEqual(
            tmp_files, [], f"成功路径下不应残留 .tmp：{tmp_files}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
