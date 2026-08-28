"""
javlibraryscrapy.cli.workflow 的单元测试。

覆盖 step1 涉及的两块逻辑：
  1. ``find_video_files`` 递归扫描下载根（含任意深度子目录）+ 大小过滤
  2. ``_cleanup_empty_parents`` 视频移走后清理变空的原父目录（含嵌套空目录）

完全离线：用 ``tempfile.TemporaryDirectory`` 隔离文件系统。

运行：
    uv run pytest tests/unit/test_workflow.py -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

# 把 src/ 加到 sys.path，方便直接 ``python -m`` 跑。
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from javlibraryscrapy.cli.workflow import (  # noqa: E402
    _cleanup_empty_parents,
    find_video_files,
    step1_move_videos,
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
        intermediate = root / "intermediate"
        intermediate.mkdir()
        target = intermediate / "ABF-340.mp4"
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

        intermediate = root / "intermediate"
        intermediate.mkdir()
        target = intermediate / "ABF-340.mp4"
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

        intermediate = root / "intermediate"
        intermediate.mkdir()
        target = intermediate / "deep.mp4"
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

        intermediate = root / "intermediate"
        intermediate.mkdir()
        target = intermediate / "top.mp4"
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
            (root / "intermediate").mkdir(exist_ok=True)

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

        intermediate = root / "intermediate"
        intermediate.mkdir()
        target = intermediate / "deep.mp4"
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
# step1_move_videos：端到端（含清理）
# --------------------------------------------------------------------------- #
def test_step1_move_videos_cleans_empty_parents():
    """端到端：移动 → 清理空目录 → intermediate 拿到视频，原父目录被删。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        download = root / "downloads"
        download.mkdir()
        intermediate = root / "intermediate"

        # 一个种子一个文件夹
        sub = download / "ABF-340-C"
        sub.mkdir()
        video = sub / "ABF-340-C.mp4"
        _write_big_file(video, mb=600)

        ok = step1_move_videos(download, intermediate, min_size_mb=500)
        assert ok is True
        # 视频出现在 intermediate
        assert (intermediate / "ABF-340-C.mp4").is_file()
        # 原父目录被删
        assert not sub.exists()
        # downloads 根还在
        assert download.exists()
        print("✅ test_step1_move_videos_cleans_empty_parents")


def test_step1_move_videos_keeps_non_empty_parents():
    """端到端：原父目录还含其它文件 → 保留。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        download = root / "downloads"
        download.mkdir()
        intermediate = root / "intermediate"

        sub = download / "ABF-340-C"
        sub.mkdir()
        video = sub / "ABF-340-C.mp4"
        _write_big_file(video, mb=600)
        # 同目录还有别的元数据
        (sub / "info.txt").write_text("torrent metadata")

        step1_move_videos(download, intermediate, min_size_mb=500)

        assert (intermediate / "ABF-340-C.mp4").is_file()
        assert sub.exists()  # 还有 info.txt，未删
        assert (sub / "info.txt").exists()
        print("✅ test_step1_move_videos_keeps_non_empty_parents")


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
    print("\n🎉 ALL TESTS PASSED")
