"""
javlibraryscrapy.library.scanner 的手动测试脚本。

构造一个伪 JAV 库结构（含正常影片、无 NFO、多视频、无车牌、重复车牌、深层递归、
BOM 编码），跑 scan_library，验证索引内容、双向前缀匹配、原子落盘/重载。

运行：
    uv run pytest tests/unit/test_library_scanner.py
    # 或：uv run python -m tests.unit.test_library_scanner
"""

import json
import sys
import tempfile
import textwrap
from pathlib import Path

# 把 src/ 加到 sys.path，方便直接 `python -m tests.unit.test_library_scanner` 跑。
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from javlibraryscrapy.library.scanner import (  # noqa: E402
    LibraryIndex,
    ScanProgress,
    load_index,
    save_index,
    scan_library,
)


def build_fixture(root: Path) -> None:
    """构造伪 JAV 库（含各类边界场景）。"""
    # 1) 正常影片（车牌 + 演员 + 标题）
    m = root / "ABF-340 actress title"
    m.mkdir()
    (m / "ABF-340.mp4").write_bytes(b"\x00" * 1024)
    (m / "poster.jpg").write_bytes(b"fake")
    (m / "movie.nfo").write_text(
        textwrap.dedent(
            """\
            <?xml version="1.0"?>
            <movie>
              <title>ABF-340 title test</title>
              <releasedate>2024-05-01</releasedate>
              <actor><name>actress A</name></actor>
              <actor><name>actress B</name></actor>
              <actor><name>actress C</name></actor>
            </movie>"""
        ),
        encoding="utf-8",
    )

    # 2) 没有 NFO
    m = root / "SNIS-001 xxx"
    m.mkdir()
    (m / "snis.mp4").write_bytes(b"\x00" * 2048)

    # 3) 多视频（CD1/CD2）
    m = root / "MIDE-001"
    m.mkdir()
    (m / "MIDE-001-CD1.mp4").write_bytes(b"\x00" * 1024)
    (m / "MIDE-001-CD2.mp4").write_bytes(b"\x00" * 2048)
    (m / "movie.nfo").write_text("<movie><title>x</title></movie>", encoding="utf-8")

    # 4) 文件夹名无法识别车牌
    bad = root / "no_carid_folder"
    bad.mkdir()
    (bad / "random.mp4").write_bytes(b"x")

    # 5) 重复车牌（size 较小应被舍弃）
    dup = root / "ABF-340 older"
    dup.mkdir()
    (dup / "abf340-old.mp4").write_bytes(b"\x00" * 512)

    # 6) 深层递归（应在 ABF-777 处停，不进 CD1）
    deep = root / "Studio" / "ABF-777"
    deep.mkdir(parents=True)
    (deep / "a.mp4").write_bytes(b"\x00" * 1024)
    (deep / "CD1").mkdir()
    (deep / "CD1" / "cd1.mp4").write_bytes(b"\x00" * 512)

    # 7) 没有视频的目录（应被忽略）
    novid = root / "NOVID-001 xxx"
    novid.mkdir()
    (novid / "movie.nfo").write_text("<movie><title>x</title></movie>", encoding="utf-8")

    # 8) BOM 测试
    bom = root / "BOM-123 test"
    bom.mkdir()
    (bom / "bom.mp4").write_bytes(b"\x00" * 1024)
    (bom / "movie.nfo").write_text(
        "﻿<?xml version=\"1.0\"?><movie><title>BOM test</title></movie>",
        encoding="utf-8",
    )

    # 9) 后缀带 -C 的子版本（验证双向前缀）
    sub = root / "ABF-340-C extra"
    sub.mkdir()
    (sub / "abf340c.mp4").write_bytes(b"\x00" * 4096)


def assert_eq(label: str, got, expected) -> None:
    if got != expected:
        print(f"  FAIL {label}")
        print(f"       got:      {got!r}")
        print(f"       expected: {expected!r}")
        raise AssertionError(label)
    print(f"  OK   {label}")


def test_scan_basic():
    print("\n[1] scan_library 基本场景")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_fixture(root)

        progress = ScanProgress()
        movies, stats = scan_library(root, progress=progress)

        assert_eq("scan 完成后 is_complete", progress.is_complete, True)
        assert_eq("索引条目数", len(movies), 6)
        assert_eq("扫到的目录数 >= 10", stats.total_folders_scanned >= 10, True)
        assert_eq(
            "无 NFO 列表",
            sorted(Path(p).name for p in stats.folders_without_nfo),
            sorted(["ABF-340 older", "ABF-340-C extra", "ABF-777", "SNIS-001 xxx"]),
        )
        assert_eq(
            "无车牌目录",
            [Path(p).name for p in stats.folders_no_carid],
            ["no_carid_folder"],
        )
        assert_eq("重复车牌舍弃数 >= 1", len(stats.duplicate_carids) >= 1, True)
        assert_eq("ABF-340 保留 size 大的", movies["ABF-340"].total_size_bytes, 1024)
        assert_eq("ABF-340-C 单独存在（子版本被保留）", movies["ABF-340-C"].total_size_bytes, 4096)
        assert_eq("MIDE-001 视频数", movies["MIDE-001"].video_count, 2)
        assert_eq("BOM-123 标题解析（去 BOM）", movies["BOM-123"].title, "BOM test")


def test_prefix_matching():
    print("\n[2] 双向前缀匹配")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_fixture(root)
        movies, _ = scan_library(root)
        idx = LibraryIndex(movies)

        # 精确匹配
        m = idx.find_match("ABF-340")
        assert_eq("精确匹配 ABF-340", m.carid, "ABF-340")

        # 精确命中 -C（本地独立有 ABF-340-C 条目）
        m = idx.find_match("ABF-340-C")
        assert_eq("ABF-340-C 命中自身（精确 > 前缀）", m.carid, "ABF-340-C")

        # 主版本前缀命中（若本地有 -C，命中 -C 因为更长更具体）
        # —— 这个用例只能在没有精确 ABF-340-C 条目时才体现纯前缀匹配

        # 不存在的车牌
        m = idx.find_match("UNKNOWN-999")
        assert_eq("未知车牌返回 None", m is None, True)

        # 大小写不敏感
        m = idx.find_match("abf-340")
        assert_eq("小写匹配", m.carid, "ABF-340")


def test_prefix_matching_subversion_fallback():
    """当本地只有子版本（无主版本条目）时，主版本查询应通过前缀命中子版本。"""
    print("\n[2b] 双向前缀匹配：仅子版本存在时回退到主版本")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # 只有 ABF-340-C，没有 ABF-340
        m = root / "ABF-340-C only"
        m.mkdir()
        (m / "x.mp4").write_bytes(b"\x00" * 100)

        movies, _ = scan_library(root)
        idx = LibraryIndex(movies)

        # 查询 ABF-340（主版本）→ 应通过 l.startswith(t) 命中 ABF-340-C
        hit = idx.find_match("ABF-340")
        assert_eq("主版本查询 → 命中本地 ABF-340-C", hit.carid, "ABF-340-C")


def test_save_load_roundtrip():
    print("\n[3] save_index + load_index 往返")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_fixture(root)
        movies, stats = scan_library(root)

        idx_path = Path(tmp) / "lib.json"
        save_index(movies, stats, idx_path, root)

        data = load_index(idx_path)
        assert_eq("重载版本", data["schema_version"], 1)
        assert_eq("重载 movies 数", len(data["movies"]), 6)
        assert_eq("重载 root 字段", data["root"], str(root))
        assert_eq("重载 stats.movies_indexed", data["stats"]["movies_indexed"], 6)

        # 从 dict 重建 LibraryIndex
        idx2 = LibraryIndex.from_dict(data)
        assert_eq("LibraryIndex 重建后大小", len(idx2), 6)
        assert_eq(
            "重建后前缀匹配（ABF-340-C 精确命中自身）",
            idx2.find_match("ABF-340-C").carid,
            "ABF-340-C",
        )


def test_load_missing_file():
    print("\n[4] 加载不存在的索引返回 None")
    with tempfile.TemporaryDirectory() as tmp:
        result = load_index(Path(tmp) / "nope.json")
        assert_eq("缺失返回 None", result is None, True)


def test_load_corrupt_file():
    print("\n[5] 损坏的索引返回 None")
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad.json"
        bad.write_text("not valid json {{{", encoding="utf-8")
        result = load_index(bad)
        assert_eq("损坏返回 None", result is None, True)


def test_version_mismatch():
    print("\n[6] 版本不匹配返回 None")
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "oldver.json"
        bad.write_text(json.dumps({"schema_version": 999, "movies": {}}), encoding="utf-8")
        result = load_index(bad)
        assert_eq("旧版本返回 None", result is None, True)


def test_atomic_write():
    print("\n[7] 原子写入：不应留下 .tmp 残骸")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "r"
        root.mkdir()
        (root / "TEST-001").mkdir()
        (root / "TEST-001" / "test.mp4").write_bytes(b"x" * 100)

        idx_path = Path(tmp) / "lib.json"
        movies, stats = scan_library(root)
        save_index(movies, stats, idx_path, root)

        assert_eq(".tmp 已被 rename 清理", idx_path.with_suffix(".json.tmp").exists(), False)
        assert_eq("最终文件存在", idx_path.exists(), True)


def main():
    tests = [
        test_scan_basic,
        test_prefix_matching,
        test_prefix_matching_subversion_fallback,
        test_save_load_roundtrip,
        test_load_missing_file,
        test_load_corrupt_file,
        test_version_mismatch,
        test_atomic_write,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError:
            failed += 1
    print()
    if failed:
        print(f"FAIL {failed} 项失败")
        sys.exit(1)
    else:
        print("PASS 所有测试通过")


if __name__ == "__main__":
    main()