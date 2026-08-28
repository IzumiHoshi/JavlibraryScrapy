"""wanted 状态筛选的单元测试。

不依赖 FastAPI / uvicorn，纯 WantedService.list() 行为：
- status_filter=downloading/downloaded/organized/none 各自命中预期条目
- 全局 status_counts 与 filter 无关
- status_filter 与 month / q / include_missing 组合正确
- 非法 status_filter 视同未传
- _status_of() 优先级：downloading > organized > downloaded > none
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from javlibraryscrapy.server.services.wanted import (  # noqa: E402
    STATUS_VALUES,
    WantedService,
    _status_of,
)


@pytest.fixture
def wanted_service():
    tmp = Path(tempfile.mkdtemp())
    data = tmp / "wanted.json"
    data.write_text(
        json.dumps(
            [
                # downloading (NAS active)
                {"code": "ABF-340", "title": "A", "release_date": "2026-08-01",
                 "_bucket": "2026-08", "_status": "ready"},
                # downloaded (NAS completed, not in local)
                {"code": "IPZZ-907", "title": "B", "release_date": "2026-08-05",
                 "_bucket": "2026-08", "_status": "ready"},
                # organized (in local library, may or may not be in NAS)
                {"code": "DSOD-001", "title": "E", "release_date": "2026-08-10",
                 "_bucket": "2026-08", "_status": "ready"},
                {"code": "HMN-880", "title": "D", "release_date": "",
                 "_bucket": "unknown", "_status": "failed"},
                # none（无任何标记）
                {"code": "SNOS-334", "title": "C", "release_date": "2026-07-01",
                 "_bucket": "2026-07", "_status": "ready"},
            ]
        ),
        encoding="utf-8",
    )
    return WantedService(data_path=data, javlibrary_proxy=None, javbus_proxy=None)


@pytest.fixture
def statuses():
    return {
        "downloading": {"ABF-340"},
        "downloaded": {"IPZZ-907"},
        "local_exists": {"DSOD-001", "HMN-880"},
    }


def _set(statuses, key):
    return statuses[key]


# -------------------- _status_of 单元测试 --------------------


def test_status_of_priority_downloading_wins(wanted_service, statuses):
    """即使在 local_exists 里，downloading 优先级最高。"""
    # 把 IPZZ-907 也加到 local —— 应该是 downloaded（不冲突）
    statuses_with_overlap = {
        "downloading": {"ABF-340"},
        "downloaded": set(),
        "local_exists": {"IPZZ-907"},  # 既已下载又在 local → organized
    }
    movies = wanted_service._movies
    assert _status_of(movies[1], set(), set(), {"IPZZ-907"}) == "organized"


def test_status_of_no_marks(wanted_service, statuses):
    """无任何标记 → none。"""
    movies = wanted_service._movies
    assert _status_of(movies[4], set(), set(), set()) == "none"


def test_status_of_missing_code(wanted_service, statuses):
    """code 为空 → none（防御）。"""
    assert _status_of({"code": ""}, set(), set(), set()) == "none"


# -------------------- list() 行为测试 --------------------


def test_status_counts_global(wanted_service, statuses):
    """status_counts 与 filter 无关，全局计数。"""
    r = wanted_service.list(
        nas_downloading=_set(statuses, "downloading"),
        nas_completed=_set(statuses, "downloaded"),
        local_exists_by_code=_set(statuses, "local_exists"),
    )
    # 预期: ABF-340=downloading, IPZZ-907=downloaded, DSOD-001=organized,
    #        HMN-880=organized, SNOS-334=none
    assert r["status_counts"] == {
        "none": 1,
        "downloading": 1,
        "downloaded": 1,
        "organized": 2,
    }


def test_status_counts_with_month_filter(wanted_service, statuses):
    """即使带 month 过滤，status_counts 仍是全局（不受 month 影响）。"""
    r = wanted_service.list(
        month="2026-08",
        nas_downloading=_set(statuses, "downloading"),
        nas_completed=_set(statuses, "downloaded"),
        local_exists_by_code=_set(statuses, "local_exists"),
    )
    assert r["status_counts"]["downloading"] == 1
    assert r["status_counts"]["none"] == 1  # SNOS-334 不在 2026-08


def test_filter_downloading(wanted_service, statuses):
    r = wanted_service.list(
        status_filter="downloading",
        nas_downloading=_set(statuses, "downloading"),
        nas_completed=_set(statuses, "downloaded"),
        local_exists_by_code=_set(statuses, "local_exists"),
    )
    assert r["total"] == 1
    assert [m["code"] for m in r["items"]] == ["ABF-340"]


def test_filter_organized(wanted_service, statuses):
    r = wanted_service.list(
        status_filter="organized",
        nas_downloading=_set(statuses, "downloading"),
        nas_completed=_set(statuses, "downloaded"),
        local_exists_by_code=_set(statuses, "local_exists"),
    )
    codes = [m["code"] for m in r["items"]]
    assert r["total"] == 2
    assert set(codes) == {"DSOD-001", "HMN-880"}


def test_filter_none(wanted_service, statuses):
    r = wanted_service.list(
        status_filter="none",
        nas_downloading=_set(statuses, "downloading"),
        nas_completed=_set(statuses, "downloaded"),
        local_exists_by_code=_set(statuses, "local_exists"),
    )
    assert r["total"] == 1
    assert [m["code"] for m in r["items"]] == ["SNOS-334"]


def test_filter_combined_with_month_and_q(wanted_service, statuses):
    """filter + month + q 组合。"""
    r = wanted_service.list(
        month="2026-08",
        status_filter="organized",
        nas_downloading=_set(statuses, "downloading"),
        nas_completed=_set(statuses, "downloaded"),
        local_exists_by_code=_set(statuses, "local_exists"),
    )
    # 2026-08 + organized → DSOD-001（HMN-880 在 unknown 桶）
    assert r["total"] == 1
    assert [m["code"] for m in r["items"]] == ["DSOD-001"]


def test_empty_filter_returns_all(wanted_service, statuses):
    """空 status_filter = 不过滤。"""
    r = wanted_service.list(
        nas_downloading=_set(statuses, "downloading"),
        nas_completed=_set(statuses, "downloaded"),
        local_exists_by_code=_set(statuses, "local_exists"),
    )
    assert r["total"] == 5
    assert r["status_filter"] == ""


def test_invalid_status_filter_ignored(wanted_service, statuses):
    """非法 status_filter → 视同未传，返回全集。"""
    r = wanted_service.list(
        status_filter="bogus",
        nas_downloading=_set(statuses, "downloading"),
        nas_completed=_set(statuses, "downloaded"),
        local_exists_by_code=_set(statuses, "local_exists"),
    )
    assert r["total"] == 5


def test_empty_nas_sets_falls_back_to_none(wanted_service):
    """没传 NAS/local_exists → 全部归入 none。"""
    r = wanted_service.list()
    assert r["status_counts"] == {
        "none": 5,
        "downloading": 0,
        "downloaded": 0,
        "organized": 0,
    }


def test_status_values_constant():
    """导出常量稳定。"""
    assert STATUS_VALUES == ("none", "downloading", "downloaded", "organized")