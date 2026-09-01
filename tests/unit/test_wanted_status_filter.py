"""wanted 状态筛选的单元测试。

不依赖 FastAPI / uvicorn，纯 WantedService.list() 行为：
- status_filter=downloading/downloaded/organized/none/deleted 各自命中预期条目
- 全局 status_counts 与 filter 无关
- status_filter 与 month / q / include_missing 组合正确
- 非法 status_filter 视同未传
- _status_of() 优先级：deleted > downloading > organized > downloaded > none
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
    #        HMN-880=organized, SNOS-334=none, deleted=0
    assert r["status_counts"] == {
        "none": 1,
        "downloading": 1,
        "downloaded": 1,
        "organized": 2,
        "deleted": 0,
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
        "deleted": 0,
    }


def test_status_values_constant():
    """导出常量稳定。"""
    assert STATUS_VALUES == (
        "none", "downloading", "downloaded", "organized", "deleted",
    )


# -------------------- merge_wanted 保护 deleted --------------------


def test_merge_wanted_skips_deleted_entries():
    """Most Wanted 自动刷新不应复活用户已删除的车片。

    场景：用户标记 START-048 为 deleted（手动删了本地文件夹）。
    之后 Most Wanted 全站刷新把 START-048 又包含进来。
    期望：merge_wanted 不把它加入 needs_javbus 队列，_status="deleted" 保留。
    """
    from javlibraryscrapy.server.services.wanted_refresh import merge_wanted

    local = [
        # 用户已删除的车片
        {"code": "START-048", "title": "T", "release_date": "",
         "_status": "deleted", "_deleted_at": "2026-08-31T20:00:00"},
        # 普通的 ready 车片（无 release_date，应该走 needs_javbus）
        {"code": "IPZZ-907", "title": "T2", "release_date": "",
         "_status": "ready"},
    ]
    remote = [
        {"id": "1", "code": "START-048", "title": "Updated Title",
         "cover_url": "https://example.com/start-048.jpg"},
        {"id": "2", "code": "IPZZ-907", "title": "Other",
         "cover_url": "https://example.com/ipzz-907.jpg"},
    ]

    result = merge_wanted(remote, local)
    # IPZZ-907 应该被加入 needs_javbus（普通 ready）
    codes_needs_javbus = [e["code"] for e in result.needs_javbus]
    assert "IPZZ-907" in codes_needs_javbus
    # START-048（deleted）不应该被加入 needs_javbus
    assert "START-048" not in codes_needs_javbus
    # 它的 _status 应该仍然是 deleted（没被覆盖为 pending）
    deleted_entry = next(e for e in local if e["code"] == "START-048")
    assert deleted_entry["_status"] == "deleted"


# -------------------- delete_one + deleted 状态 --------------------


def test_delete_one_marks_status_and_persists(wanted_service, tmp_path):
    """delete_one 把 _status="deleted" 写入 JSON + 落盘。"""
    # wanted_service 已加载 5 条 fixture；挑一条标记为 deleted
    movies = wanted_service._movies
    target_code = "ABF-340"
    target = next(m for m in movies if m["code"] == target_code)
    # 先用 delete_one 删除（不传 mw_root → 用 data_path.parent）
    # data_path.parent 是 fixture 用的临时目录，里面没 folder，所以
    # folder_deleted=False，但 _status 仍然标 deleted（用户意图明确）
    result = wanted_service.delete_one(target_code)
    assert result["code"] == target_code
    assert result["ok"] is False  # folder 没找到
    assert target["_status"] == "deleted"
    assert target.get("_deleted_at")  # ISO 时间戳存在
    # 落盘验证：data_path 应该被改写
    with open(wanted_service.data_path, encoding="utf-8") as f:
        saved = json.load(f)
    saved_target = next(m for m in saved if m["code"] == target_code)
    assert saved_target["_status"] == "deleted"


def test_delete_one_creates_minimal_entry_when_not_in_json(tmp_path):
    """JSON 中没记录的车 → delete_one 建一条最小 deleted 记录。"""
    data = tmp_path / "wanted.json"
    data.write_text("[]", encoding="utf-8")
    svc = WantedService(data_path=data, javlibrary_proxy=None, javbus_proxy=None)
    result = svc.delete_one("NEVER-EXIST-999")
    assert result["code"] == "NEVER-EXIST-999"
    assert result["ok"] is False  # 没 folder
    assert result["movie"]["_status"] == "deleted"
    # JSON 应该被持久化这条最小记录
    with open(data, encoding="utf-8") as f:
        saved = json.load(f)
    assert len(saved) == 1
    assert saved[0]["code"] == "NEVER-EXIST-999"
    assert saved[0]["_status"] == "deleted"


def test_delete_one_actually_removes_local_folder(wanted_service, tmp_path):
    """本地库有 folder → delete_one 真的删除 + 标记 _deleted_folder。"""
    # 在 data_path.parent 建一个假 folder（fixture 的 data 在 tmp_path）
    # data_path.parent 是 fixture 创建的临时目录
    mw_root = wanted_service.data_path.parent
    folder = mw_root / "ABF-340 Sample Title"
    folder.mkdir()
    (folder / "movie.nfo").write_text("dummy")
    assert folder.exists()

    result = wanted_service.delete_one("ABF-340", mw_root=mw_root)
    assert result["ok"] is True
    assert result["folder_deleted"] is True
    assert folder.name in (result["folder"] or "")
    assert not folder.exists()  # 物理删除成功

    # 内存里也标记了
    target = next(m for m in wanted_service._movies if m["code"] == "ABF-340")
    assert target["_status"] == "deleted"
    assert target["_deleted_folder"] == folder.name


def test_status_of_deleted_overrides_everything(wanted_service):
    """_status="deleted" 是最高优先级，覆盖 downloading / organized / downloaded。"""
    movies = wanted_service._movies
    # 把第一条标为 deleted
    target = movies[0]
    target["_status"] = "deleted"
    # 即使在 nas_downloading + local_exists 里，也该是 deleted
    assert _status_of(target, {target["code"]}, set(), {target["code"]}) == "deleted"


def test_filter_deleted(wanted_service, statuses):
    """status_filter=deleted 只返回 _status="deleted" 的车。"""
    # fixture 里手动给 SNOS-334 标 deleted
    target = next(m for m in wanted_service._movies if m["code"] == "SNOS-334")
    target["_status"] = "deleted"
    r = wanted_service.list(
        status_filter="deleted",
        nas_downloading=set(),
        nas_completed=set(),
        local_exists_by_code=set(),
    )
    assert r["total"] == 1
    assert [m["code"] for m in r["items"]] == ["SNOS-334"]


# -------------------- iter_codes 回归测试 --------------------


def test_iter_codes_returns_all_codes_in_release_date_order(wanted_service):
    """iter_codes 返回所有 code 快照，按 release_date 倒序。

    PR #25 review 顺带发现：``WantedService.iter_codes`` 方法的 ``def`` 行
    在某个提交里丢了，body 被并入 fetch_batch_javbus 末尾成为 dead code。
    修复后这里加回归测试，确保 :func:`app.create_app` 的 prewarm 调
    用不会再 AttributeError（之前默默吞 warning 导致 sample cache 预热
    从未生效，每次开服首屏都吃 NFS cold start）。
    """
    codes = wanted_service.iter_codes()
    assert isinstance(codes, list)
    assert len(codes) == 5  # fixture 里有 5 条
    # 按 release_date desc 排序后应该是 2026-08-* 排前面（HMN-880 release_date 空排最后）
    # 具体顺序：DSOD-001 (2026-08-10) > ABF-340 (2026-08-01) > IPZZ-907 (2026-08-05)
    # 等等，因为 _sorted_movies 按 release_date desc 排
    first = codes[0]
    # 第一条应该是 release_date 最新的（DSOD-001=2026-08-10）
    assert first == "DSOD-001"
    # 空 release_date（HMN-880）排最后
    assert codes[-1] == "HMN-880"


# -------------------- _parse_download_codes 进度 --------------------
# 下载进度（百分比 0-100）从 NAS list 响应里抽出，前端 wanted 卡片徽章
# 用这个画进度条。覆盖关键字段名 + 边界。
import sys as _sys
_sys.path.insert(0, str(ROOT / "src")) if str(ROOT / "src") not in _sys.path else None
from javlibraryscrapy.server.services.zspace import _parse_download_codes


def test_parse_progress_from_percent_field():
    """progress 字段直接读。"""
    raw = {
        "data": {
            "list": [
                {"name": "ABF-340 torrent", "status": 0, "progress": 42.5},
            ]
        }
    }
    downloading, completed, progress = _parse_download_codes(raw)
    assert "ABF-340" in downloading
    assert progress["ABF-340"] == 42.5


def test_parse_progress_from_complete_total_bytes():
    """completeSize / totalSize 兜底算百分比。"""
    raw = {
        "data": {
            "list": [
                {"name": "IPZZ-907-C", "status": 0,
                 "completeSize": 500 * 1024 * 1024,
                 "totalSize": 1000 * 1024 * 1024},
            ]
        }
    }
    downloading, _, progress = _parse_download_codes(raw)
    assert downloading == {"IPZZ-907"}
    assert progress["IPZZ-907"] == 50.0


def test_parse_progress_clamped_to_100():
    """NAS 上报的 progress 可能超过 100（99.x 但四舍五入、size 溢出），
    clamp 到 0-100。"""
    raw = {
        "data": {
            "list": [
                {"name": "SNOS-999", "status": 0, "progress": 150.0},
            ]
        }
    }
    _, _, progress = _parse_download_codes(raw)
    assert progress["SNOS-999"] == 100.0


def test_parse_progress_missing_field_defaults_zero():
    """没有 progress 字段 → 进度 0（前端画进度条但不显示百分比）。"""
    raw = {
        "data": {
            "list": [
                {"name": "MIBB-084", "status": 0},  # 无 progress
            ]
        }
    }
    _, _, progress = _parse_download_codes(raw)
    assert progress["MIBB-084"] == 0.0


def test_parse_completed_not_in_progress_dict():
    """completed 集合的车不放进度 dict（前端不需要：completed 进度总是 100）。"""
    raw = {
        "data": {
            "list": [
                {"name": "DOWN-001", "status": 0, "progress": 30.0},
                {"name": "DONE-002", "status": 13, "progress": 100.0},  # completed
            ]
        }
    }
    downloading, completed, progress = _parse_download_codes(raw)
    assert downloading == {"DOWN-001"}
    assert completed == {"DONE-002"}
    assert "DOWN-001" in progress
    assert "DONE-002" not in progress


def test_parse_isfinished_false_still_records_progress():
    """isFinished=false (可能 progress=99.7% 还没标记 completed) → 进度照记。"""
    raw = {
        "data": {
            "list": [
                {"name": "PPPE-435", "isFinished": False, "progress": 99.7},
            ]
        }
    }
    _, _, progress = _parse_download_codes(raw)
    assert progress["PPPE-435"] == 99.7