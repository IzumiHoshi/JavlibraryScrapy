"""回归测试：``GET /api/wanted`` 的 query 参数声明。

背景：之前 bug 是路由把 query 参数名写成了 ``status_filter``，但前端
URL 用的是 ``?status=downloading``。FastAPI 默认按参数名匹配 query，
于是 ``status_filter`` 永远是空字符串，filter 形同虚设。

测试策略：直接 import 路由模块，检查 ``list_wanted`` 路由依赖项里
声明了 ``status`` query 参数，且不接受别名（默认 alias 是参数名本身）。

不依赖 FastAPI TestClient / 子进程 —— 纯 import + inspect，秒级跑完。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from javlibraryscrapy.server.routes.wanted import register  # noqa: E402
from fastapi import FastAPI, Query  # noqa: E402


def _get_list_wanted_route():
    """构造临时 FastAPI app 调 register()，拿到 /api/wanted 的 Route 对象。"""
    app = FastAPI()
    register(app)
    for route in app.routes:
        # starlette Route has .path; our handler is the endpoint
        if getattr(route, "path", None) == "/api/wanted":
            return route
    raise AssertionError("/api/wanted route 未注册")


def _get_query_param_names(route) -> list[str]:
    """从 Route.dependant 里抽出 query 参数名列表。

    FastAPI 把每个 query 参数建模成一个 ``Query(...)`` 字段，
    通过 ``inspect.signature(endpoint)`` 能拿到参数名列表。
    """
    sig = inspect.signature(route.endpoint)
    return list(sig.parameters.keys())


def test_list_wanted_accepts_status_query_param():
    """``status`` 必须作为 query 参数存在（前端 URL 用 ``?status=``）。"""
    route = _get_list_wanted_route()
    names = _get_query_param_names(route)
    assert "status" in names, (
        f"前端 URL 用 ?status=downloading，但路由参数列表没 'status'：{names}"
    )


def test_list_wanted_does_not_rename_status_param():
    """参数名就是 URL 上的 query key —— 不能写 ``status_filter`` 然后指望
    FastAPI 自动绑 ``?status=``。

    如果之前有人改错了，把 alias 也写上才能匹配。验证没有错配的 alias。
    """
    route = _get_list_wanted_route()
    sig = inspect.signature(route.endpoint)
    status_param = sig.parameters["status"]
    default = status_param.default
    # FastAPI 不同版本的 Query 是函数 / 类的差异，不 isinstance
    # 直接看 duck-typed 的 alias / name 字段
    alias = getattr(default, "alias", None)
    assert alias is None or alias == "status", (
        f"status 参数 alias={alias!r}，"
        f"应该留空或等于 'status'（前端 URL 用 ?status=downloading）"
    )


def test_list_wanted_existing_params_still_present():
    """修复参数名时不能误删其它字段。"""
    route = _get_list_wanted_route()
    names = set(_get_query_param_names(route))
    # 必有的核心参数（其它可选字段省略以便 PR review 容易看懂）
    for required in ("month", "page", "size", "status", "q"):
        assert required in names, f"缺少 query 参数：{required!r}（names={names}）"


def test_wanted_status_values_constant_is_stable():
    """STATUS_VALUES 与前端 chip 的 data-status 对齐 —— 别轻易改。"""
    from javlibraryscrapy.server.services.wanted import STATUS_VALUES

    assert STATUS_VALUES == ("none", "downloading", "downloaded", "organized")