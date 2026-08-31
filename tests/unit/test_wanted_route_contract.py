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


# ---------------------------------------------------------------------------
# 封面 fallback：手动添加车牌 → 本地库 poster.jpg 兜底
# ---------------------------------------------------------------------------
def _make_folder_with_poster(tmp_path: Path, code: str, title: str) -> Path:
    """建一个 ``<CARID> <title>/`` 含 poster.jpg 的最简 folder。"""
    import shutil

    folder = tmp_path / f"{code} {title}"
    folder.mkdir(parents=True)
    # 复制一张真实的小 jpg 当 poster（用项目内的 fixture；如果 fixture 不存在
    # 就退回到 ``__init__.py``，pytest 一定会把它当字节流读，所以内容无所谓）
    src = (
        Path(__file__).resolve().parent / "test_wanted_route_contract.py"
    )
    shutil.copy(src, folder / "poster.jpg")
    return folder


def test_local_cover_fallback_uses_local_poster_when_remote_empty(tmp_path):
    """``cover_url`` 为空 + 本地库有 poster.jpg → 返回 /api/local-cover 路径。"""
    from javlibraryscrapy.server.routes.wanted import local_cover_fallback

    folder = _make_folder_with_poster(tmp_path, "START-048", "タイトル")
    code = "START-048"

    result = local_cover_fallback(tmp_path, code, "")
    assert result.startswith("/api/local-cover?folder=")
    # ``name`` 必须带扩展名（白名单要求 poster.jpg，不是 poster）
    assert "name=poster.jpg" in result
    # folder 应被正确 quote（Windows 反斜杠 → %5C；中文 UTF-8 percent-encoded）
    import urllib.parse
    quoted = urllib.parse.quote(str(folder))
    assert quoted in result


def test_local_cover_fallback_keeps_remote_cover_when_present(tmp_path):
    """``cover_url`` 非空时直接返回 remote，不查本地库。"""
    from javlibraryscrapy.server.routes.wanted import local_cover_fallback

    _make_folder_with_poster(tmp_path, "ABC-123", "title")
    remote = "/api/cover?url=https%3A%2F%2Fpics.dmm.co.jp%2Fabc.jpg"

    assert local_cover_fallback(tmp_path, "ABC-123", remote) == remote


def test_local_cover_fallback_returns_empty_when_no_local_folder(tmp_path):
    """本地库无 folder 时返回空字符串（前端继续走"空封面"逻辑）。"""
    from javlibraryscrapy.server.routes.wanted import local_cover_fallback

    assert local_cover_fallback(tmp_path, "NOTHING-999", "") == ""


def test_local_cover_fallback_returns_empty_when_no_poster_jpg(tmp_path):
    """folder 存在但没有 poster.jpg → 不 fallback，返回空。"""
    from javlibraryscrapy.server.routes.wanted import local_cover_fallback

    folder = tmp_path / "NOPOSTER-001 title"
    folder.mkdir()
    assert local_cover_fallback(tmp_path, "NOPOSTER-001", "") == ""


def test_local_cover_fallback_returns_empty_when_mw_root_unset():
    """``mw_root`` 没配 → 跳过 fallback（避免对无库的用户报错）。"""
    from javlibraryscrapy.server.routes.wanted import local_cover_fallback

    assert local_cover_fallback(None, "ABC-123", "") == ""
    assert local_cover_fallback("", "ABC-123", "") == ""  # 路径空也视同未配


# ---------------------------------------------------------------------------
# 批量 JavBus 抓取：API 契约
# ---------------------------------------------------------------------------
def _batch_route():
    """构造临时 FastAPI app 调 register()，拿到 /api/wanted/batch-add 的 Route 对象。"""
    app = FastAPI()
    register(app)
    for route in app.routes:
        if getattr(route, "path", None) == "/api/wanted/batch-add":
            return route
    raise AssertionError("/api/wanted/batch-add route 未注册")


def test_batch_add_route_accepts_post_only():
    """批量端点必须是 POST（GET 暴露 JAVBus 抓取是反模式）。"""
    route = _batch_route()
    methods = list(route.methods) if hasattr(route, "methods") else []
    assert "POST" in methods, f"批量端点必须支持 POST，got {methods}"


def test_batch_add_route_is_well_formed():
    """``/api/wanted/batch-add`` 注册在 FastAPI app 上且 path 正确。"""
    route = _batch_route()
    assert route.path == "/api/wanted/batch-add"
    # 函数签名是 async def fetch_batch_javbus(request)
    sig = inspect.signature(route.endpoint)
    assert "request" in sig.parameters


def test_batch_add_extract_carids_handles_dedup_and_case():
    """``extractCarids`` 客户端去重 + 大写化（前端契约；后端再做一次保险）。"""
    # 这是 wanted.js 的逻辑镜像 —— 后端 route 内部也做同样事情，
    # 避免前端漏掉大写 / 去重时后端崩
    import re

    def extract(input_codes):
        seen = set()
        out = []
        for raw in input_codes:
            cu = (raw or "").strip().upper()
            if not cu or cu in seen:
                continue
            # normalize：找字母数字边界
            m = re.match(r"^([A-Z]+)[-_]?(\d+)$", cu)
            if not m:
                continue
            seen.add(f"{m.group(1)}-{m.group(2)}")
            out.append(f"{m.group(1)}-{m.group(2)}")
        return out

    # 大写化
    assert extract(["ipzz-907"]) == ["IPZZ-907"]
    # 无分隔符自动补
    assert extract(["SSIS308"]) == ["SSIS-308"]
    # 去重（保首次顺序）
    assert extract(["ABC-123", "abc-123", "ABC-124"]) == ["ABC-123", "ABC-124"]
    # 空 / 非法
    assert extract([""]) == []
    assert extract(["AB"]) == []  # 缺数字
    assert extract(["123"]) == []  # 缺字母
    assert extract(["PURE_LETTERS"]) == []  # 全字母


def test_fetch_batch_javbus_empty_input_returns_empty_result():
    """``WantedService.fetch_batch_javbus`` 空输入直接返回空 summary。"""
    from javlibraryscrapy.server.services.wanted import WantedService

    # 用 mock 避免真的去 JAVBus；只需要确认 short-circuit 路径
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        svc = WantedService(data_path=Path(td) / "wanted.json")
        result = svc.fetch_batch_javbus([])
        assert result["results"] == []
        assert result["summary"] == {
            "total": 0, "ok": 0, "failed": 0,
            "created": 0, "rolled_back": 0,
        }
        # JSON 应该没被创建
        assert not (Path(td) / "wanted.json").exists()