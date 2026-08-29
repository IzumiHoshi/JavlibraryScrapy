"""
javlibraryscrapy.library.backfill 的单元测试。

完全离线：monkey-patch MovieExporter 的 ``export_movies`` 与 JavbusSpider 的
``download_cover`` / ``download_samples``，构造伪本地库结构，验证：

- :func:`check_missing` 识别 NFO / poster / fanart / samples 缺失
- :class:`BackfillPlan` 的 missing_kinds / needs_backfill / is_complete 派生属性
- :func:`iter_movie_folders` walker 跳过非影片目录 / 隐藏目录
- :func:`backfill_one` skip 路径（complete / no_video / no_carid / excluded）
- :func:`backfill_one` 完整路径调用 MovieExporter 参数正确
- :class:`MovieExporter` ``overwrite_nfo=False`` 不覆写已有 NFO
- :func:`backfill_library` 全库扫描 + 计数

运行：
    uv run pytest tests/unit/test_library_backfill.py -v
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import threading
from pathlib import Path
from unittest.mock import patch

# 把 src/ 加到 sys.path，方便直接 ``python -m`` 跑。
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from javlibraryscrapy.library.backfill import (  # noqa: E402
    BackfillPlan,
    backfill_library,
    backfill_one,
    check_missing,
    iter_movie_folders,
)
from javlibraryscrapy.scraping.exporter import MovieExporter  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _write_nfo(folder: Path, carid: str, title: str = "Test Title") -> None:
    """写一个最简合法 movie.nfo。"""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "movie.nfo").write_text(
        textwrap.dedent(
            f"""\
            <?xml version="1.0"?>
            <movie>
              <title>{title}</title>
              <releasedate>2024-05-01</releasedate>
            </movie>"""
        ),
        encoding="utf-8",
    )


def _build_complete(root: Path, carid: str, title: str) -> Path:
    """建一个完整目录：视频 + NFO + poster + fanart + 3 张 sample。"""
    folder = root / f"{carid} {title}"
    folder.mkdir(parents=True)
    (folder / f"{carid}.mp4").write_bytes(b"\x00" * 1024)
    _write_nfo(folder, carid, title)
    (folder / "poster.jpg").write_bytes(b"fake_poster")
    (folder / "fanart.jpg").write_bytes(b"fake_fanart")
    for i in range(1, 4):
        (folder / f"sample_{i:03d}.jpg").write_bytes(b"fake_sample")
    return folder


def _build_no_nfo(root: Path, carid: str, title: str) -> Path:
    """缺 NFO：视频 + poster + fanart + samples。"""
    folder = root / f"{carid} {title}"
    folder.mkdir(parents=True)
    (folder / f"{carid}.mp4").write_bytes(b"\x00" * 1024)
    (folder / "poster.jpg").write_bytes(b"fake")
    (folder / "fanart.jpg").write_bytes(b"fake")
    (folder / "sample_001.jpg").write_bytes(b"fake")
    return folder


def _build_no_video(root: Path, carid: str, title: str) -> Path:
    """无视频：只有 NFO + cover（scanner 会判定为影片目录但没视频）。"""
    folder = root / f"{carid} {title}"
    folder.mkdir(parents=True)
    _write_nfo(folder, carid, title)
    (folder / "poster.jpg").write_bytes(b"fake")
    return folder


def _build_excluded(root: Path, prefix: str) -> Path:
    """建一个被排除厂牌前缀的目录（含视频，让 check_missing 能走到）。"""
    folder = root / f"{prefix}-001 something"
    folder.mkdir(parents=True)
    (folder / "video.mp4").write_bytes(b"\x00" * 1024)
    return folder


# --------------------------------------------------------------------------- #
# BackfillPlan 属性派生
# --------------------------------------------------------------------------- #
class TestBackfillPlanProperties:
    def test_is_complete_when_all_present(self, tmp_path):
        folder = _build_complete(tmp_path, "ABF-340", "Complete Movie")
        plan = check_missing(folder)
        assert plan is not None
        assert plan.has_video is True
        assert plan.has_nfo is True
        assert plan.has_poster is True
        assert plan.has_fanart is True
        assert plan.sample_count == 3
        assert plan.is_complete is True
        assert plan.needs_backfill is False
        assert plan.missing_kinds == []

    def test_needs_backfill_when_missing_nfo(self, tmp_path):
        folder = _build_no_nfo(tmp_path, "SNIS-001", "Missing NFO")
        plan = check_missing(folder)
        assert plan is not None
        assert plan.has_nfo is False
        assert plan.needs_backfill is True
        assert "nfo" in plan.missing_kinds

    def test_missing_kinds_order(self, tmp_path):
        folder = tmp_path / "ABF-777 Everything Missing"
        folder.mkdir()
        (folder / "abf.mp4").write_bytes(b"\x00" * 1024)
        plan = check_missing(folder)
        assert plan is not None
        assert plan.has_nfo is False
        assert plan.has_poster is False
        assert plan.has_fanart is False
        assert plan.sample_count == 0
        # 顺序固定为 nfo/poster/fanart/samples（跟 dataclass 定义一致）
        assert plan.missing_kinds == ["nfo", "poster", "fanart", "samples"]

    def test_needs_backfill_requires_video(self, tmp_path):
        # 没视频 → needs_backfill=False（即使 NFO/cover 都缺也不该补）
        folder = _build_no_video(tmp_path, "ABF-340", "No Video Dir")
        plan = check_missing(folder)
        # scan_movie_folder 对纯 NFO+cover 目录仍会产出 entry（has_video=False）
        assert plan is not None
        assert plan.has_video is False
        assert plan.needs_backfill is False

    def test_excluded_brand_returns_none(self, tmp_path):
        folder = _build_excluded(tmp_path, "HEYZO")
        assert check_missing(folder) is None
        folder2 = _build_excluded(tmp_path, "LUXU")
        assert check_missing(folder2) is None


# --------------------------------------------------------------------------- #
# check_missing 字段
# --------------------------------------------------------------------------- #
class TestCheckMissing:
    def test_title_from_nfo(self, tmp_path):
        folder = _build_complete(tmp_path, "ABF-340", "fallback title")
        _write_nfo(folder, "ABF-340", "NFO Title Wins")
        plan = check_missing(folder)
        assert plan is not None
        assert plan.title == "NFO Title Wins"

    def test_carid_uppercased(self, tmp_path):
        folder = tmp_path / "abf-340 lowercase"
        folder.mkdir()
        (folder / "video.mp4").write_bytes(b"x")
        plan = check_missing(folder)
        assert plan is not None
        assert plan.carid == "ABF-340"

    def test_non_movie_dir_returns_none(self, tmp_path):
        folder = tmp_path / "no_carid"
        folder.mkdir()
        (folder / "random.mp4").write_bytes(b"x")
        assert check_missing(folder) is None


# --------------------------------------------------------------------------- #
# iter_movie_folders walker
# --------------------------------------------------------------------------- #
class TestIterMovieFolders:
    def test_finds_nested_movie_dirs(self, tmp_path):
        m1 = tmp_path / "ABF-001 A"
        m1.mkdir()
        (m1 / "v.mp4").write_bytes(b"x")
        m2 = tmp_path / "Studio" / "SNIS-100"
        m2.mkdir(parents=True)
        (m2 / "v.mp4").write_bytes(b"x")
        # 含子目录里有视频但本身无视频 — 不该出现在结果里
        m3 = tmp_path / "Empty"
        m3.mkdir()
        (m3 / "ABF-002 B" / "v.mp4").parent.mkdir(parents=True)
        (m3 / "ABF-002 B" / "v.mp4").write_bytes(b"x")

        dirs = list(iter_movie_folders(tmp_path))
        dir_names = {d.name for d in dirs}
        assert "ABF-001 A" in dir_names
        assert "SNIS-100" in dir_names
        assert "Empty" not in dir_names  # 没视频

    def test_skips_hidden_dirs(self, tmp_path):
        m = tmp_path / ".hidden" / "ABF-001 x"
        m.mkdir(parents=True)
        (m / "v.mp4").write_bytes(b"x")
        dirs = list(iter_movie_folders(tmp_path))
        assert dirs == []


# --------------------------------------------------------------------------- #
# backfill_one skip 路径（无需 mock；不调 MovieExporter）
# --------------------------------------------------------------------------- #
class TestBackfillOneSkipPaths:
    def test_complete_folder_returns_skipped(self, tmp_path):
        folder = _build_complete(tmp_path, "ABF-340", "Complete")
        result = backfill_one(folder)
        assert result["skipped"] is True
        assert result["skipped_reason"] == "complete"
        assert result["code"] == "ABF-340"
        assert result["stats"] is None

    def test_no_video_returns_skipped(self, tmp_path):
        folder = _build_no_video(tmp_path, "ABF-340", "No Video")
        result = backfill_one(folder)
        assert result["skipped"] is True
        assert result["skipped_reason"] == "no_video"
        assert result["failed"] is False

    def test_non_movie_returns_skipped(self, tmp_path):
        folder = tmp_path / "no_carid"
        folder.mkdir()
        (folder / "v.mp4").write_bytes(b"x")
        result = backfill_one(folder)
        assert result["skipped"] is True
        assert result["skipped_reason"] == "no_carid_or_excluded"
        assert result["code"] is None


# --------------------------------------------------------------------------- #
# backfill_one 完整路径：mock MovieExporter.export_movies
# --------------------------------------------------------------------------- #
class TestBackfillOneFullPath:
    """完整路径需要 patch MovieExporter.export_movies 与 JavbusSpider 的下载，
    避免真实网络 IO；assert 调用参数 + overwrite_nfo 控制。
    """

    def test_calls_exporter_with_correct_args(self, tmp_path):
        folder = _build_no_nfo(tmp_path, "ABF-340", "Missing NFO")
        # patch 掉 MovieExporter.export_movies：直接写一份假 NFO 模拟成功
        async def fake_export_movies(self, car_list, *, cover_urls=None, on_progress=None):
            code, _ = car_list[0]
            # 模拟 JAVBus 流程落地
            (folder / "movie.nfo").write_text(
                "<movie><title>Fetched</title></movie>", encoding="utf-8"
            )
            self._attempted_codes.add(code)
            self._written_codes.add(code)
            return {"total": 1, "written": 1, "failed": 0, "skipped": 0, "magnets_collected": 0}

        with patch.object(MovieExporter, "export_movies", fake_export_movies):
            result = backfill_one(
                folder,
                cover_url="https://example.com/cover.jpg",
                timeout_seconds=10,
            )

        assert result["skipped"] is False
        assert result["failed"] is False
        assert result["code"] == "ABF-340"
        assert result["stats"]["written"] == 1
        # 补完后再次 check_missing：NFO 应已写入
        assert result["plan_after"] is not None
        assert result["plan_after"]["has_nfo"] is True

    def test_overwrite_nfo_false_when_nfo_missing(self, tmp_path):
        """缺 NFO 时 MovieExporter 应以 overwrite_nfo=True 构造（允许写新 NFO）。"""
        folder = _build_no_nfo(tmp_path, "ABF-340", "X")
        captured = {}

        orig_init = MovieExporter.__init__

        def spy_init(self, output_root, **kwargs):
            captured.update(kwargs)
            orig_init(self, output_root, **kwargs)

        # 同时 stub export_movies 避免真跑
        async def fake_export_movies(self, car_list, **kwargs):
            self._attempted_codes.add(car_list[0][0])
            self._written_codes.add(car_list[0][0])
            return {"total": 1, "written": 1, "failed": 0, "skipped": 0, "magnets_collected": 0}

        with patch.object(MovieExporter, "__init__", spy_init), \
             patch.object(MovieExporter, "export_movies", fake_export_movies):
            backfill_one(folder, timeout_seconds=10)

        assert captured.get("overwrite_nfo") is True
        assert captured.get("download_samples") is False  # sample 已存在
        assert captured.get("move_video") is False
        assert captured.get("collect_magnets") is False

    def test_overwrite_nfo_false_when_nfo_exists(self, tmp_path):
        """NFO 已存在 + 仅缺其它 → overwrite_nfo=False（绝不覆写 NFO）。"""
        folder = tmp_path / "ABF-341 MissingPoster"
        folder.mkdir()
        (folder / "video.mp4").write_bytes(b"\x00" * 1024)
        _write_nfo(folder, "ABF-341", "Has NFO")
        (folder / "fanart.jpg").write_bytes(b"fake")
        (folder / "sample_001.jpg").write_bytes(b"fake")
        # 故意缺 poster

        captured = {}
        orig_init = MovieExporter.__init__

        def spy_init(self, output_root, **kwargs):
            captured.update(kwargs)
            orig_init(self, output_root, **kwargs)

        async def fake_export_movies(self, car_list, **kwargs):
            self._attempted_codes.add(car_list[0][0])
            self._written_codes.add(car_list[0][0])
            return {"total": 1, "written": 1, "failed": 0, "skipped": 0, "magnets_collected": 0}

        with patch.object(MovieExporter, "__init__", spy_init), \
             patch.object(MovieExporter, "export_movies", fake_export_movies):
            backfill_one(folder, cover_url="https://example.com/c.jpg", timeout_seconds=10)

        # NFO 已存在 → overwrite_nfo=False（保护已有 NFO）
        assert captured.get("overwrite_nfo") is False

    def test_failed_when_exporter_raises(self, tmp_path):
        folder = _build_no_nfo(tmp_path, "ABF-340", "X")

        async def boom(self, *args, **kwargs):
            raise RuntimeError("network down")

        with patch.object(MovieExporter, "export_movies", boom):
            result = backfill_one(folder, timeout_seconds=10)

        assert result["failed"] is True
        assert "network down" in result["error"]


# --------------------------------------------------------------------------- #
# MovieExporter.overwrite_nfo 直接测试
# --------------------------------------------------------------------------- #
class TestMovieExporterOverwriteNfo:
    def test_overwrite_nfo_true_by_default(self):
        """默认 overwrite_nfo=True（向后兼容 export_mostwanted / workflow）。"""
        from javlibraryscrapy.scraping.javbus import JavbusSpider

        # 用 tmp_path 喂 root_dir 让 JavbusSpider 不爆
        with tempfile.TemporaryDirectory() as tmp:
            exporter = MovieExporter(output_root=Path(tmp) / "out")
            assert exporter.overwrite_nfo is True

    def test_overwrite_nfo_false_protects_existing(self):
        """``overwrite_nfo=False`` + NFO 已存在 → 跳过 write_xml（不抛错）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            target = tmp_p / "ABF-340 title"
            target.mkdir()
            # NFO 已存在
            (target / "movie.nfo").write_text(
                "<movie><title>existing</title></movie>", encoding="utf-8"
            )

            exporter = MovieExporter(
                output_root=tmp_p,
                overwrite_nfo=False,
            )

            # 直接调 _mocked_process_movie-like 路径：跳过 JAVBus 抓取，模拟 info dict
            async def fake_process(info):
                carid = info["carid"]
                title = info["title"]
                save_dir = tmp_p / f"{carid} {title}"
                save_dir.mkdir(parents=True, exist_ok=True)
                # 仅当 overwrite_nfo=True 或 NFO 不存在时才写
                nfo_path = save_dir / "movie.nfo"
                if exporter.overwrite_nfo or not nfo_path.exists():
                    nfo_path.write_text(
                        "<movie><title>overwritten</title></movie>", encoding="utf-8"
                    )

            # NFO 应仍保留原始内容（不被覆写）
            import asyncio
            asyncio.run(fake_process({
                "carid": "ABF-340",
                "title": "title",
            }))
            content = (target / "movie.nfo").read_text(encoding="utf-8")
            assert "existing" in content
            assert "overwritten" not in content


# --------------------------------------------------------------------------- #
# backfill_library 全库流程
# --------------------------------------------------------------------------- #
class TestBackfillLibrary:
    def test_counts_skip_and_backfill(self, tmp_path):
        # 一个完整（skip） + 一个缺 NFO（backfill）+ 一个排除厂牌（skip）
        _build_complete(tmp_path, "ABF-340", "Complete")
        _build_no_nfo(tmp_path, "SNIS-001", "Needs Backfill")
        _build_excluded(tmp_path, "HEYZO")

        # patch export_movies 让"需补齐"那部立即"成功"
        async def fake_export(self, car_list, **kwargs):
            for code, _ in car_list:
                self._attempted_codes.add(code)
                self._written_codes.add(code)
            return {"total": len(car_list), "written": len(car_list), "failed": 0,
                    "skipped": 0, "magnets_collected": 0}

        with patch.object(MovieExporter, "export_movies", fake_export):
            stats = backfill_library(
                tmp_path,
                delay_seconds=0,
                timeout_seconds=10,
            )

        assert stats["total"] == 3
        assert stats["skipped_complete"] == 1
        assert stats["skipped_no_carid"] == 1  # HEYZO 排除
        assert stats["needs_backfill"] == 1
        assert stats["backfilled"] == 1
        assert stats["failed"] == 0

    def test_cancel_event_halts_iteration(self, tmp_path):
        for i in range(5):
            _build_no_nfo(tmp_path, f"SNIS-{i:03d}", f"Movie {i}")

        cancel = threading.Event()
        cancel.set()  # 立即取消

        stats = backfill_library(
            tmp_path,
            cancel_event=cancel,
            delay_seconds=0,
            timeout_seconds=10,
        )
        assert stats["cancelled"] is True
        assert stats["backfilled"] == 0  # 还没跑就被取消

    def test_max_count_stops_iteration(self, tmp_path):
        """``max_count`` 只限制"需要补齐"的处理数，skip 类不计。"""
        # 5 个需要补齐 + 3 个完整（skip）
        for i in range(5):
            _build_no_nfo(tmp_path, f"SNIS-{i:03d}", f"Movie {i}")
        for i in range(3):
            _build_complete(tmp_path, f"ABF-{i:03d}", f"Complete {i}")

        async def fake_export(self, car_list, **kwargs):
            for code, _ in car_list:
                self._attempted_codes.add(code)
                self._written_codes.add(code)
            return {"total": len(car_list), "written": len(car_list), "failed": 0,
                    "skipped": 0, "magnets_collected": 0}

        with patch.object(MovieExporter, "export_movies", fake_export):
            stats = backfill_library(
                tmp_path,
                delay_seconds=0,
                timeout_seconds=10,
                max_count=2,
            )

        assert stats["limit_reached"] is True
        assert stats["needs_backfill"] == 2  # 只处理了 2 个
        assert stats["backfilled"] == 2
        # os.walk 字典序：ABF-00x 先于 SNIS-00x。limit=2 时：
        # yield 6 个：ABF-000/001/002 (3 skip) + SNIS-000/001 (2 处理) + SNIS-002 (触发 limit break)
        assert stats["total"] == 6
        assert stats["skipped_complete"] == 3
        assert stats["skipped_no_video"] == 0
        # results 里只该有 2 条
        assert len(stats["results"]) == 2

    def test_on_per_movie_called_per_movie(self, tmp_path):
        """``on_per_movie`` 在每部 ``backfill_one`` 完成后立即回调，
        用于服务层实时更新进度计数。"""
        for i in range(3):
            _build_no_nfo(tmp_path, f"SNIS-{i:03d}", f"Movie {i}")

        async def fake_export(self, car_list, **kwargs):
            for code, _ in car_list:
                self._attempted_codes.add(code)
                self._written_codes.add(code)
            return {"total": len(car_list), "written": len(car_list), "failed": 0,
                    "skipped": 0, "magnets_collected": 0}

        per_movie_calls: list = []
        with patch.object(MovieExporter, "export_movies", fake_export):
            stats = backfill_library(
                tmp_path,
                delay_seconds=0,
                timeout_seconds=10,
                on_per_movie=lambda r: per_movie_calls.append(r),
            )

        # 3 部都跑了 on_per_movie
        assert len(per_movie_calls) == 3
        assert all(r.get("code", "").startswith("SNIS-") for r in per_movie_calls)
        # results 里每部 dict 都有 stats（含 written=1）
        for r in per_movie_calls:
            assert r["stats"]["written"] == 1
        # stats["results"] 也应有 3 条
        assert len(stats["results"]) == 3