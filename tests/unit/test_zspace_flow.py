"""zspace 极空间 NAS 下载流程测试。

测试目标（不依赖真实 NAS，用 monkey-patch 模拟 ``ZSpaceClient``）：

1. 路由参数校验
   - ``/api/zspace/submit`` 缺磁力 / 空 items / 短 magnet → 422
   - 未启用或未配齐配置 → 503 + 可读 detail
2. 端到端流程（mock client）
   - GET /api/zspace/status → 返回当前配置
   - GET /api/zspace/downloads → 透传 NAS 响应
   - POST /api/zspace/submit → 单项 ok / 单项失败 / 混合 → results[] 完整
   - 单项失败不影响其它项（continue 处理）
   - ``download_path`` 缺省时回退到 ``cfg.download_path``
3. 异常映射
   - ``ZSpaceError`` → ``results[].error``（登录/网络错误）
   - 其它 ``httpx.HTTPError`` → ``results[].error``（非预期异常）
   - 真正 ASGI 异常（``/downloads`` 端点）→ 502

运行::

    uv run python tests/unit/test_zspace_flow.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from javlibraryscrapy.server.app import create_app  # noqa: E402
from javlibraryscrapy.server.config import Settings  # noqa: E402
from javlibraryscrapy.server.services.zspace import ZSpaceError  # noqa: E402


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #
def _make_settings(**overrides) -> Settings:
    """最小 Settings：把 zspace 4 必填字段填齐，其它走默认。"""
    base = dict(
        zspace_enabled=True,
        zspace_host="192.168.1.100",
        zspace_user="13800000000",
        zspace_password="secret123",
        zspace_device_id="",
        zspace_download_path="/pool/my/data/test/",
    )
    base.update(overrides)
    return Settings(**base)


def _make_app(settings: Optional[Settings] = None):
    """造一个最小可路由的 FastAPI app（不需要真实 wanted 数据）。"""
    if settings is None:
        settings = _make_settings()
    with tempfile.TemporaryDirectory() as tmp:
        data_path = Path(tmp) / "movies.json"
        data_path.write_text("[]", encoding="utf-8")
        return create_app(
            settings=settings,
            data_path=data_path,
            output_dir=Path(tmp),
            no_rescan_on_startup=True,
        )


def _async(method: str, app, url: str, **kwargs) -> httpx.Response:
    """ASGITransport 包一层 asyncio.run（httpx 0.28 只支持 async）。"""
    transport = httpx.ASGITransport(app=app)

    async def _do() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(_do())


def _make_client_mock(
    submit_results: Optional[List[Dict[str, Any]]] = None,
    submit_side_effect: Optional[Exception] = None,
    list_result: Optional[Dict[str, Any]] = None,
    list_side_effect: Optional[Exception] = None,
):
    """构造一个 ``ZSpaceClient`` mock。

    - ``submit_results`` 是按顺序返回的 NAS 业务响应列表（每个是 dict）。
      列表用完后重复最后一个；如果不传则默认全部成功（code="200"）。
    - ``submit_side_effect`` 抛异常场景（模拟登录/网络失败）。
    - ``list_result`` / ``list_side_effect`` 用于 ``/downloads`` 端点。
    """
    client = AsyncMock()
    # submit_magnet
    if submit_side_effect is not None:
        client.submit_magnet.side_effect = submit_side_effect
    else:
        results = submit_results or [
            {"code": "200", "msg": "success", "data": {"id": f"task-{i}"}}
            for i in range(10)
        ]

        async def _submit(magnet: str, path: str, _i=[0]):
            r = results[_i[0] % len(results)]
            _i[0] += 1
            return dict(r)  # 拷贝一份防止上层污染 fixture

        client.submit_magnet.side_effect = _submit
    # list_downloads
    if list_side_effect is not None:
        client.list_downloads.side_effect = list_side_effect
    else:
        client.list_downloads.return_value = list_result or {
            "code": "200",
            "data": {"tasks": []},
        }
    return client


def _patch_zspace(client_mock) -> Any:
    """把 ``_get_or_create_client`` 替成返回 mock client。"""
    return patch(
        "javlibraryscrapy.server.routes.zspace._get_or_create_client",
        return_value=client_mock,
    )


# ====================================================================== #
# 路由参数校验
# ====================================================================== #
class TestSubmitValidation(unittest.TestCase):
    """submit 端点对入参的硬校验。"""

    def setUp(self) -> None:
        self.app = _make_app()

    def test_empty_items_returns_422(self):
        """items 空 → Pydantic min_length=1 → 422。"""
        r = _async("POST", self.app, "/api/zspace/submit", json={
            "items": [], "download_path": "/pool/test/"
        })
        self.assertEqual(r.status_code, 422, r.text)

    def test_short_magnet_returns_422(self):
        """magnet < 10 字符 → 422。"""
        r = _async("POST", self.app, "/api/zspace/submit", json={
            "items": [{"code": "ABF-340", "magnet": "short"}],
            "download_path": "/pool/test/",
        })
        self.assertEqual(r.status_code, 422, r.text)

    def test_missing_code_returns_422(self):
        """缺 code 字段 → 422。"""
        r = _async("POST", self.app, "/api/zspace/submit", json={
            "items": [{"magnet": "magnet:?xt=urn:btih:" + "a" * 40}],
            "download_path": "/pool/test/",
        })
        self.assertEqual(r.status_code, 422, r.text)

    def test_not_configured_returns_503(self):
        """未配置（enabled=false）→ 503 + 可读 detail。"""
        # 单独造一个 disabled app（不能用 mock，因为路由先判 cfg.is_configured）
        app = _make_app(_make_settings(zspace_enabled=False))
        r = _async("POST", app, "/api/zspace/submit", json={
            "items": [{"code": "ABF-340", "magnet": "magnet:?xt=urn:btih:" + "a" * 40}],
            "download_path": "/pool/test/",
        })
        self.assertEqual(r.status_code, 503, r.text)
        body = r.json()
        self.assertIn("zspace", body["detail"].lower())

    def test_missing_host_returns_503(self):
        """host 没填（必填字段缺失）→ 503。"""
        app = _make_app(_make_settings(zspace_host=None))
        r = _async("POST", app, "/api/zspace/submit", json={
            "items": [{"code": "ABF-340", "magnet": "magnet:?xt=urn:btih:" + "a" * 40}],
            "download_path": "/pool/test/",
        })
        self.assertEqual(r.status_code, 503, r.text)


# ====================================================================== #
# downloads 端点
# ====================================================================== #
class TestDownloadsEndpoint(unittest.TestCase):
    """``POST /api/zspace/downloads`` 透传 NAS 列表响应。"""

    def setUp(self) -> None:
        self.app = _make_app()

    def test_returns_nas_payload(self):
        expected = {"code": "200", "data": {"tasks": [{"id": "t1"}, {"id": "t2"}]}}
        client = _make_client_mock(list_result=expected)
        with _patch_zspace(client):
            r = _async("POST", self.app, "/api/zspace/downloads")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), expected)

    def test_zspace_error_returns_502(self):
        """ZSpaceError → 502 + 错误详情。"""
        client = _make_client_mock(list_side_effect=ZSpaceError("login expired"))
        with _patch_zspace(client):
            r = _async("POST", self.app, "/api/zspace/downloads")
        self.assertEqual(r.status_code, 502, r.text)
        self.assertIn("login expired", r.json()["detail"])


# ====================================================================== #
# submit 端到端流程
# ====================================================================== #
class TestSubmitFlow(unittest.TestCase):
    """``POST /api/zspace/submit`` 的完整业务流程。"""

    MAGNET_OK = "magnet:?xt=urn:btih:" + "a" * 40

    def setUp(self) -> None:
        self.app = _make_app()

    def test_all_success(self):
        """全部成功 → ok_count=N，results[].ok=true。"""
        client = _make_client_mock(submit_results=[
            {"code": "200", "msg": "ok", "data": {"id": f"task-{i}"}}
            for i in range(3)
        ])
        items = [{"code": f"ABF-{i}", "magnet": self.MAGNET_OK} for i in range(3)]
        with _patch_zspace(client):
            r = _async("POST", self.app, "/api/zspace/submit", json={
                "items": items, "download_path": "/pool/test/",
            })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["ok_count"], 3)
        self.assertEqual(body["download_path"], "/pool/test/")
        self.assertTrue(all(res["ok"] for res in body["results"]))

    def test_mixed_results_continues_on_failure(self):
        """部分失败（NAS 业务码 != 200）→ ok_count 只算成功的，failed 项带 msg。"""
        client = _make_client_mock(submit_results=[
            {"code": "200", "msg": "ok"},
            {"code": "N0xxxx", "msg": "磁力格式异常"},
            {"code": "200", "msg": "ok"},
        ])
        items = [
            {"code": "A-1", "magnet": self.MAGNET_OK},
            {"code": "A-2", "magnet": self.MAGNET_OK},
            {"code": "A-3", "magnet": self.MAGNET_OK},
        ]
        with _patch_zspace(client):
            r = _async("POST", self.app, "/api/zspace/submit", json={
                "items": items, "download_path": "/pool/test/",
            })
        body = r.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["ok_count"], 2)
        self.assertFalse(body["results"][1]["ok"])
        self.assertEqual(body["results"][1]["status_code"], "N0xxxx")
        self.assertEqual(body["results"][1]["msg"], "磁力格式异常")

    def test_login_error_marks_all_failed(self):
        """首次登录失败（ZSpaceError）→ 所有项都 error，整批基本跑不了。"""
        client = _make_client_mock(
            submit_side_effect=ZSpaceError("login failed: code=N001414"),
        )
        items = [{"code": f"A-{i}", "magnet": self.MAGNET_OK} for i in range(2)]
        with _patch_zspace(client):
            r = _async("POST", self.app, "/api/zspace/submit", json={
                "items": items, "download_path": "/pool/test/",
            })
        body = r.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["ok_count"], 0)
        for res in body["results"]:
            self.assertFalse(res["ok"])
            self.assertIn("N001414", res["error"])

    def test_unexpected_exception_isolated(self):
        """非 ZSpaceError 的未知异常被隔离（不污染其它项）。"""
        call_count = 0

        async def _submit_with_bomb(magnet: str, path: str):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("unexpected")
            return {"code": "200", "msg": "ok"}

        client = AsyncMock()
        client.submit_magnet.side_effect = _submit_with_bomb
        items = [{"code": f"A-{i}", "magnet": self.MAGNET_OK} for i in range(3)]
        with _patch_zspace(client):
            r = _async("POST", self.app, "/api/zspace/submit", json={
                "items": items, "download_path": "/pool/test/",
            })
        body = r.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["ok_count"], 2)
        # 第 2 项是异常，error 形如 "ValueError: unexpected"
        self.assertFalse(body["results"][1]["ok"])
        self.assertIn("ValueError", body["results"][1]["error"])
        self.assertIn("unexpected", body["results"][1]["error"])

    def test_download_path_falls_back_to_config(self):
        """body 不传 download_path → 用 cfg.download_path。"""
        client = _make_client_mock()
        items = [{"code": "A-1", "magnet": self.MAGNET_OK}]
        with _patch_zspace(client):
            r = _async("POST", self.app, "/api/zspace/submit", json={
                "items": items,  # 不传 download_path
            })
        body = r.json()
        # cfg.download_path 在 _make_settings 里是 "/pool/my/data/test/"
        self.assertEqual(body["download_path"], "/pool/my/data/test/")

    def test_non_dict_response_handled(self):
        """NAS 返回非 dict（罕见，比如字符串）→ 兜底 ok=False + raw 截断。"""
        async def _submit_str(magnet: str, path: str):
            return "OK"

        client = AsyncMock()
        client.submit_magnet.side_effect = _submit_str
        items = [{"code": "A-1", "magnet": self.MAGNET_OK}]
        with _patch_zspace(client):
            r = _async("POST", self.app, "/api/zspace/submit", json={
                "items": items, "download_path": "/pool/test/",
            })
        body = r.json()
        self.assertFalse(body["results"][0]["ok"])
        self.assertEqual(body["results"][0]["raw"], "OK")


# ====================================================================== #
# 状态端点（无网络依赖）
# ====================================================================== #
class TestStatusEndpoint(unittest.TestCase):
    """``GET /api/zspace/status`` 返回给前端按钮启用态用的元信息。"""

    def test_returns_configured_state(self):
        app = _make_app()
        r = _async("GET", app, "/api/zspace/status")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["configured"])
        self.assertTrue(body["enabled"])
        self.assertEqual(body["host"], "192.168.1.100")
        self.assertEqual(body["default_download_path"], "/pool/my/data/test/")
        self.assertFalse(body["device_id_set"])  # 空字符串 → False

    def test_disabled_returns_not_configured(self):
        app = _make_app(_make_settings(zspace_enabled=False))
        r = _async("GET", app, "/api/zspace/status")
        body = r.json()
        self.assertFalse(body["configured"])
        self.assertFalse(body["enabled"])


if __name__ == "__main__":
    unittest.main(verbosity=2)