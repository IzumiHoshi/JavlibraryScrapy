"""
javlibraryscrapy.cli.workflow 的单元测试。

覆盖：
  1. ``find_video_files`` 递归扫描下载根（含任意深度子目录）+ 大小过滤
  2. ``_cleanup_empty_parents`` 视频移走后清理变空的原父目录（含嵌套空目录）
  3. ``step1_move_videos`` 端到端（含清理 + dry-run + 返回 moved_paths；视频落到
     ``<output_path>/_staging/`` 子目录避免污染顶层）
  4. ``step2_clean_at_prefix_for_paths`` 精准去 @ 前缀（只处理传入列表，不误伤旧文件）
  5. ``step3_scrape_from_paths`` 从指定文件列表构建 car_list，验证：
     - MovieExporter 用 ``bucket_by_month=True``
     - 结束后清理 _staging/

完全离线：step1/2 用 tempfile.TemporaryDirectory 隔离；step3 用 patch 避免真实网络。

运行：
    uv run pytest tests/unit/test_workflow.py -v
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

# 把 src/ 加到 sys.path，方便直接 ``python -m`` 跑。
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from javlibraryscrapy.cli.workflow import (  # noqa: E402
    _cleanup_empty_parents,
    _STAGING_DIR,
    find_video_files,
    step1_move_videos,
    step2_clean_at_prefix_for_paths,
    step3_scrape_from_paths,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _write_big_file(path: Path, mb: int = 600) -> None:
    """写一个 ``mb`` MB 的稀疏文件（速度快，不真占盘）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.seek(mb * 1024 * 1024 - 1)
        f.write(b"\0")


# --------------------------------------------------------------------------- #
# find_video_files：递归 + 大小过滤
# --------------------------------------------------------------------------- #
def test_find_video_files_recursive_includes_nested():
    """递归扫描：顶层、一层子目录、两层子目录的视频都要找到。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 顶层
        _write_big_file(root / "top.mp4", mb=600)
        # 一层
        sub1 = root / "ABF-340"
        sub1.mkdir()
        _write_big_file(sub1 / "ABF-340.mp4", mb=600)
        # 两层
        sub2 = root / "nested" / "MIAB-001"
        sub2.mkdir(parents=True)
        _write_big_file(sub2 / "MIAB-001.mkv", mb=700)

        files = find_video_files(root, min_size_mb=500)
        names = sorted(f.name for f in files)
        assert names == ["ABF-340.mp4", "MIAB-001.mkv", "top.mp4"], names
        print("✅ test_find_video_files_recursive_includes_nested")


def test_find_video_files_filters_by_size():
    """小于阈值的文件被过滤掉。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_big_file(root / "big.mp4", mb=600)
        _write_big_file(root / "small.mp4", mb=10)  # < 500MB 阈值

        files = find_video_files(root, min_size_mb=500)
        names = [f.name for f in files]
        assert "big.mp4" in names
        assert "small.mp4" not in names
        print("✅ test_find_video_files_filters_by_size")


def test_find_video_files_filters_by_extension():
    """非视频扩展名被忽略。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_big_file(root / "movie.mp4", mb=600)
        _write_big_file(root / "subs.srt", mb=600)
        _write_big_file(root / "torrent.torrent", mb=600)

        files = find_video_files(root, min_size_mb=500)
        names = [f.name for f in files]
        assert names == ["movie.mp4"]
        print("✅ test_find_video_files_filters_by_extension")


def test_find_video_files_skips_hidden_dirs():
    """``os.walk`` 原地修改 ``dirs`` 跳过 ``.`` 开头的隐藏目录。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_big_file(root / "visible.mp4", mb=600)
        # 隐藏目录里放视频：不应被扫到
        hidden = root / ".cache"
        hidden.mkdir()
        _write_big_file(hidden / "hidden.mp4", mb=600)

        files = find_video_files(root, min_size_mb=500)
        names = [f.name for f in files]
        assert "visible.mp4" in names
        assert "hidden.mp4" not in names
        print("✅ test_find_video_files_skips_hidden_dirs")


def test_find_video_files_nonexistent_dir_returns_empty():
    """下载目录不存在时返回空列表（不抛异常）。"""
    files = find_video_files(Path("/nonexistent/path/xyz"), min_size_mb=500)
    assert files == []
    print("✅ test_find_video_files_nonexistent_dir_returns_empty")


# --------------------------------------------------------------------------- #
# _cleanup_empty_parents：移动后清理空目录
# --------------------------------------------------------------------------- #
def test_cleanup_empty_parents_removes_empty():
    """视频移走后父目录变空 → 删除该父目录。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        download = root / "download"
        download.mkdir()
        sub = download / "ABF-340"
        sub.mkdir()
        video = sub / "ABF-340.mp4"
        video.write_bytes(b"x" * 100)

        # 模拟移动：把 video 移出 sub
        output = root / "output"
        output.mkdir()
        target = output / "ABF-340.mp4"
        original_parent = video.parent
        shutil.move(str(video), str(target))

        # 清理的是【下载端】的原父目录
        removed = _cleanup_empty_parents(original_parent, stop_at=download)
        assert removed == 1
        assert not sub.exists()
        assert download.exists()  # stop_at 永不被删
        print("✅ test_cleanup_empty_parents_removes_empty")


def test_cleanup_empty_parents_keeps_non_empty():
    """父目录里还有其它文件 → 不删。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sub = root / "ABF-340"
        sub.mkdir()
        video = sub / "ABF-340.mp4"
        video.write_bytes(b"x" * 100)
        # 同目录还有别的文件
        (sub / "metadata.txt").write_text("torrent info")

        output = root / "output"
        output.mkdir()
        target = output / "ABF-340.mp4"
        shutil.move(str(video), str(target))

        removed = _cleanup_empty_parents(target, stop_at=root)
        assert removed == 0
        assert sub.exists()
        assert (sub / "metadata.txt").exists()
        print("✅ test_cleanup_empty_parents_keeps_non_empty")


def test_cleanup_empty_parents_recursive_nested():
    """多层嵌套都变空 → 一路向上删到 stop_at（stop_at 自身保留）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        download = root / "download"
        download.mkdir()
        nested = download / "a" / "b" / "c"
        nested.mkdir(parents=True)
        video = nested / "deep.mp4"
        video.write_bytes(b"x" * 100)

        output = root / "output"
        output.mkdir()
        target = output / "deep.mp4"
        original_parent = video.parent
        shutil.move(str(video), str(target))

        removed = _cleanup_empty_parents(original_parent, stop_at=download)
        # c/、b/、a/ 三个目录都空了 → 全删；download 不删
        assert removed == 3
        assert not (download / "a").exists()
        assert download.exists()
        print("✅ test_cleanup_empty_parents_recursive_nested")


def test_cleanup_empty_parents_stop_at_protected():
    """视频直接在 stop_at 根下 → stop_at 永不被删（返回 0）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        video = root / "top.mp4"
        video.write_bytes(b"x" * 100)

        output = root / "output"
        output.mkdir()
        target = output / "top.mp4"
        original_parent = video.parent  # == root
        shutil.move(str(video), str(target))

        removed = _cleanup_empty_parents(original_parent, stop_at=root)
        assert removed == 0
        assert root.exists()
        assert root.is_dir()
        print("✅ test_cleanup_empty_parents_stop_at_protected")


def test_cleanup_empty_parents_outside_stop_at_refused():
    """parent 不在 stop_at 子树 → 拒绝操作（防误删下载根外目录）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        outside = Path(td).parent / f"outside_test_{id(root)}"
        outside.mkdir()
        try:
            sub = outside / "ABF-340"
            sub.mkdir()
            # original_parent 是 outside/ABF-340/，不在 root 子树下
            original_parent = sub
            (root / "output").mkdir(exist_ok=True)

            removed = _cleanup_empty_parents(original_parent, stop_at=root)
            assert removed == 0
            # outside 及其子目录未被删
            assert outside.exists()
            assert sub.exists()
        finally:
            shutil.rmtree(outside, ignore_errors=True)
        print("✅ test_cleanup_empty_parents_outside_stop_at_refused")


def test_cleanup_empty_parents_stops_at_non_empty():
    """向上过程中遇到非空目录 → 停在该层，外部目录保留。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        download = root / "download"
        download.mkdir()
        # a/ 是「非空」（里面有 b/ + sibling.txt）；b/ 移空后应只删 b/，不动 a/
        a = download / "a"
        a.mkdir()
        (a / "sibling.txt").write_text("keep me")
        b = a / "b"
        b.mkdir()
        video = b / "deep.mp4"
        video.write_bytes(b"x" * 100)

        output = root / "output"
        output.mkdir()
        target = output / "deep.mp4"
        original_parent = video.parent  # == b
        shutil.move(str(video), str(target))

        removed = _cleanup_empty_parents(original_parent, stop_at=download)
        # b/ 空了 → 删；a/ 有 sibling.txt → 停
        assert removed == 1
        assert not b.exists()
        assert a.exists()
        assert (a / "sibling.txt").exists()
        print("✅ test_cleanup_empty_parents_stops_at_non_empty")


# --------------------------------------------------------------------------- #
# step1_move_videos：端到端（含清理 + 返回 moved_paths）
# --------------------------------------------------------------------------- #
def test_step1_move_videos_cleans_empty_parents():
    """端到端：移动 → 清理空目录 → _staging 拿到视频，原父目录被删；output 顶层干净。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        download = root / "downloads"
        download.mkdir()
        output = root / "output"

        # 一个种子一个文件夹
        sub = download / "ABF-340-C"
        sub.mkdir()
        video = sub / "ABF-340-C.mp4"
        _write_big_file(video, mb=600)

        moved = step1_move_videos(download, output, min_size_mb=500)
        staging = output / _STAGING_DIR
        # 视频出现在 _staging（不是 output 顶层！）
        assert (staging / "ABF-340-C.mp4").is_file()
        # 原父目录被删
        assert not sub.exists()
        # downloads 根还在
        assert download.exists()
        # output 顶层没有被污染
        assert not (output / "ABF-340-C.mp4").exists()
        # 返回的列表包含 staging 下的目标路径
        assert any(p.name == "ABF-340-C.mp4" for p in moved)
        assert all(p.parent == staging for p in moved)
        print("✅ test_step1_move_videos_cleans_empty_parents")


def test_step1_move_videos_keeps_non_empty_parents():
    """端到端：原父目录还含其它文件 → 保留。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        download = root / "downloads"
        download.mkdir()
        output = root / "output"

        sub = download / "ABF-340-C"
        sub.mkdir()
        video = sub / "ABF-340-C.mp4"
        _write_big_file(video, mb=600)
        # 同目录还有别的元数据
        (sub / "info.txt").write_text("torrent metadata")

        step1_move_videos(download, output, min_size_mb=500)

        assert (output / _STAGING_DIR / "ABF-340-C.mp4").is_file()
        assert sub.exists()  # 还有 info.txt，未删
        assert (sub / "info.txt").exists()
        print("✅ test_step1_move_videos_keeps_non_empty_parents")


def test_step1_move_videos_returns_moved_paths():
    """返回的 list[Path] 顺序稳定，可直接传给 step2/step3；路径都在 _staging 下。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        download = root / "downloads"
        download.mkdir()
        output = root / "output"
        staging = output / _STAGING_DIR

        # 故意按非字母序的文件名创建（验证 find_video_files 排序后顺序稳定）
        _write_big_file(download / "z.mp4", mb=600)
        _write_big_file(download / "a.mp4", mb=600)
        _write_big_file(download / "m.mp4", mb=600)

        moved = step1_move_videos(download, output, min_size_mb=500)
        assert len(moved) == 3
        # 按字母序
        names = [p.name for p in moved]
        assert names == ["a.mp4", "m.mp4", "z.mp4"]
        # 所有路径都在 _staging 下（不是 output 顶层）
        assert all(p.parent == staging for p in moved)
        print("✅ test_step1_move_videos_returns_moved_paths")


def test_step1_move_videos_no_videos_returns_empty_list():
    """没找到视频时返回空列表（不是 False / None）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        download = root / "downloads"
        download.mkdir()
        output = root / "output"

        moved = step1_move_videos(download, output, min_size_mb=500)
        assert moved == []
        # _staging 不应被创建
        assert not (output / _STAGING_DIR).exists()
        print("✅ test_step1_move_videos_no_videos_returns_empty_list")


def test_step1_move_videos_does_not_pollute_output_top():
    """已有 output 顶层有用户数据时，step1 不能在那里落视频。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        download = root / "downloads"
        download.mkdir()
        output = root / "output"
        output.mkdir()
        # 模拟 output 顶层已经有用户数据
        (output / "2010-07").mkdir()
        (output / "2010-07" / "OLD-001 keep.mp4").write_bytes(b"keep")

        sub = download / "ABF-340-C"
        sub.mkdir()
        video = sub / "ABF-340-C.mp4"
        _write_big_file(video, mb=600)

        step1_move_videos(download, output, min_size_mb=500)

        # 顶层未污染
        assert not (output / "ABF-340-C.mp4").exists()
        # 但 _staging 有
        assert (output / _STAGING_DIR / "ABF-340-C.mp4").is_file()
        # 用户数据未动
        assert (output / "2010-07" / "OLD-001 keep.mp4").read_bytes() == b"keep"
        print("✅ test_step1_move_videos_does_not_pollute_output_top")


# --------------------------------------------------------------------------- #
# dry-run：只打印计划，不动文件
# --------------------------------------------------------------------------- #
def test_step1_move_videos_dry_run_does_not_modify():
    """dry_run=True 时：源文件不动、原父目录不删、output/_staging 无文件落地。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        download = root / "downloads"
        download.mkdir()
        output = root / "output"
        staging = output / _STAGING_DIR

        sub = download / "ABF-340-C"
        sub.mkdir()
        video = sub / "ABF-340-C.mp4"
        _write_big_file(video, mb=600)

        moved = step1_move_videos(
            download, output, min_size_mb=500, dry_run=True,
        )

        # 视频仍在原位
        assert video.is_file()
        # 原父目录未删
        assert sub.exists()
        # _staging 没创建
        assert not staging.exists()
        # 但返回的列表仍然包含计划路径（指向 _staging）
        assert len(moved) == 1
        assert moved[0] == staging / "ABF-340-C.mp4"
        print("✅ test_step1_move_videos_dry_run_does_not_modify")


def test_step1_move_videos_dry_run_skips_existing():
    """dry_run + 目标已存在 → 走跳过分支，不打印 [DRY-RUN] 计划。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        download = root / "downloads"
        download.mkdir()
        output = root / "output"
        staging = output / _STAGING_DIR
        staging.mkdir(parents=True)

        sub = download / "ABF-340-C"
        sub.mkdir()
        video = sub / "ABF-340-C.mp4"
        _write_big_file(video, mb=600)
        # 目标已存在
        (staging / "ABF-340-C.mp4").write_bytes(b"existing")

        moved = step1_move_videos(
            download, output, min_size_mb=500, dry_run=True,
        )
        # 源文件未动
        assert video.is_file()
        # staging 里文件仍是原内容
        assert (staging / "ABF-340-C.mp4").read_bytes() == b"existing"
        # 跳过的文件不在返回值里
        assert moved == []
        print("✅ test_step1_move_videos_dry_run_skips_existing")


# --------------------------------------------------------------------------- #
# step2_clean_at_prefix_for_paths：精准去 @ 前缀
# --------------------------------------------------------------------------- #
def test_step2_cleans_at_prefix_in_list():
    """只对传入的 files 去 @ 前缀；output 下其它文件不动。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        output = root / "output"
        output.mkdir()

        # step1 移来的"新"视频
        new1 = output / "hkbisi.com@ABF-340-C.mp4"
        new1.write_bytes(b"new1")
        new2 = output / "site@MIAB-001.mp4"
        new2.write_bytes(b"new2")
        # 没 @ 的视频
        new3 = output / "PPPE-435-AI.mp4"
        new3.write_bytes(b"new3")

        # output 下之前已存在的旧文件（带 @）—— 不应被处理
        old = output / "old@SSIS-001.mp4"
        old.write_bytes(b"old")

        cleaned = step2_clean_at_prefix_for_paths([new1, new2, new3])

        assert cleaned == 2
        # @ 前缀已去
        assert (output / "ABF-340-C.mp4").is_file()
        assert (output / "MIAB-001.mp4").is_file()
        assert (output / "PPPE-435-AI.mp4").is_file()
        # 旧文件未被处理（精准）
        assert (output / "old@SSIS-001.mp4").is_file()
        # 重命名前的路径已不存在
        assert not new1.exists()
        assert not new2.exists()
        print("✅ test_step2_cleans_at_prefix_in_list")


def test_step2_handles_collision_with_counter():
    """目标名已存在时自动追加 _1, _2... 后缀。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        output = root / "output"
        output.mkdir()

        # 目标已存在
        (output / "ABF-340.mp4").write_bytes(b"existing")
        # step1 移来的同目标名
        new = output / "site@ABF-340.mp4"
        new.write_bytes(b"new")

        cleaned = step2_clean_at_prefix_for_paths([new])

        assert cleaned == 1
        assert (output / "ABF-340.mp4").read_bytes() == b"existing"  # 原文件未覆盖
        assert (output / "ABF-340_1.mp4").read_bytes() == b"new"  # 新文件追加 _1
        print("✅ test_step2_handles_collision_with_counter")


def test_step2_skips_missing_files():
    """传入的路径已被外部脚本删了 → 静默跳过。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        output = root / "output"
        output.mkdir()

        missing = output / "site@ABF-340.mp4"  # 不创建
        cleaned = step2_clean_at_prefix_for_paths([missing])
        assert cleaned == 0
        print("✅ test_step2_skips_missing_files")


def test_step2_empty_list():
    """空列表 → 返回 0，不抛异常。"""
    cleaned = step2_clean_at_prefix_for_paths([])
    assert cleaned == 0
    print("✅ test_step2_empty_list")


# --------------------------------------------------------------------------- #
# step3_scrape_from_paths：从指定文件列表构建 car_list
# --------------------------------------------------------------------------- #
def test_step3_builds_car_list_and_calls_exporter():
    """从 source_paths 提取车牌，调用 MovieExporter；output 已有旧子目录不被扫。

    验证 MovieExporter 的调用方式：
      - output_root = <output>/_staging/
      - bucket_by_month=True
      - magnets_index = <output>/magnets.json
    跑完 _staging/ 被清理（兜底）。
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        output = root / "output"
        output.mkdir()
        staging = output / _STAGING_DIR
        staging.mkdir()
        # 让 _staging 里先放一个无关文件 →验证兜底逻辑会清掉
        (staging / "leftover.txt").write_bytes(b"x")

        # step1/2 处理后落在 _staging 顶层的视频
        v1 = staging / "ABF-340-C.mp4"
        v1.write_bytes(b"x")
        v2 = staging / "MIAB-001.mp4"
        v2.write_bytes(b"x")
        # 无法识别车号
        v3 = staging / "random.mp4"
        v3.write_bytes(b"x")

        # output 下已有之前整理好的旧文件夹（不应被扫）
        old_folder = output / "OLD-001 Already"
        old_folder.mkdir()
        (old_folder / "OLD-001 Already.mp4").write_bytes(b"x")

        # Mock MovieExporter 避免真实网络 + 验证 cars 结构
        with patch("javlibraryscrapy.cli.workflow.MovieExporter") as MockExporter:
            mock_instance = MockExporter.return_value
            mock_instance.export_movies = AsyncMock(return_value={
                "total": 2, "written": 2, "failed": 0, "skipped": 0, "magnets_collected": 2,
            })
            ok = asyncio.run(step3_scrape_from_paths(
                output, [v1, v2, v3],
            ))

        assert ok is True
        # MockExporter 被调用一次
        assert MockExporter.call_count == 1
        # 验证调用参数：output_root = staging, bucket_by_month=True
        call_kwargs = MockExporter.call_args.kwargs
        assert call_kwargs["output_root"] == staging
        assert call_kwargs["bucket_by_month"] is True
        assert call_kwargs["move_video"] is True
        assert call_kwargs["download_samples"] is True
        assert call_kwargs["collect_magnets"] is True
        assert call_kwargs["magnets_index"] == output / "magnets.json"
        # cars 是 [(car_id, video_path), ...] 结构
        export_args = mock_instance.export_movies.call_args
        cars = export_args.args[0] if export_args.args else export_args.kwargs["car_list"]
        car_codes = [c[0] for c in cars]
        # 没把 OLD-001（旧子目录里的）一起算进去
        assert "OLD-001" not in car_codes
        # 第三个文件（random.mp4）被跳过
        assert len(cars) == 2
        # _staging 兜底清理（leftover.txt + 子目录应该都清掉）
        assert not staging.exists()
        print("✅ test_step3_builds_car_list_and_calls_exporter")


def test_step3_no_recognizable_codes_returns_false():
    """所有视频都识别不出车牌 → 返回 False，不调用 exporter。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        output = root / "output"
        output.mkdir()

        v1 = output / _STAGING_DIR / "random1.mp4"
        v1.parent.mkdir(parents=True)
        v1.write_bytes(b"x")
        v2 = output / _STAGING_DIR / "random2.mp4"
        v2.write_bytes(b"x")

        with patch("javlibraryscrapy.cli.workflow.MovieExporter") as MockExporter:
            ok = asyncio.run(step3_scrape_from_paths(output, [v1, v2]))

        assert ok is False
        MockExporter.assert_not_called()
        print("✅ test_step3_no_recognizable_codes_returns_false")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    test_find_video_files_recursive_includes_nested()
    test_find_video_files_filters_by_size()
    test_find_video_files_filters_by_extension()
    test_find_video_files_skips_hidden_dirs()
    test_find_video_files_nonexistent_dir_returns_empty()
    test_cleanup_empty_parents_removes_empty()
    test_cleanup_empty_parents_keeps_non_empty()
    test_cleanup_empty_parents_recursive_nested()
    test_cleanup_empty_parents_stop_at_protected()
    test_cleanup_empty_parents_outside_stop_at_refused()
    test_cleanup_empty_parents_stops_at_non_empty()
    test_step1_move_videos_cleans_empty_parents()
    test_step1_move_videos_keeps_non_empty_parents()
    test_step1_move_videos_returns_moved_paths()
    test_step1_move_videos_no_videos_returns_empty_list()
    test_step1_move_videos_does_not_pollute_output_top()
    test_step1_move_videos_dry_run_does_not_modify()
    test_step1_move_videos_dry_run_skips_existing()
    test_step2_cleans_at_prefix_in_list()
    test_step2_handles_collision_with_counter()
    test_step2_skips_missing_files()
    test_step2_empty_list()
    test_step3_builds_car_list_and_calls_exporter()
    test_step3_no_recognizable_codes_returns_false()
    print("\n🎉 ALL TESTS PASSED")