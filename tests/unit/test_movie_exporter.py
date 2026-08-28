"""
javlibraryscrapy.scraping.exporter 的单元测试。

完全离线：不发起真实网络请求；通过 monkey-patch JavbusSpider 的 ``crawl_and_process``、
``download_cover``、``download_samples``，以及 ``_download_javlibrary_cover``，让
``MovieExporter.process_movie`` 在确定性环境下被测试。

运行：
    uv run pytest tests/unit/test_movie_exporter.py -v
"""

import asyncio
import base64
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# 把 src/ 加到 sys.path，方便直接 ``python -m`` 跑。
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from javlibraryscrapy.scraping.exporter import MovieExporter  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fake_png_bytes() -> bytes:
    """最小合法 PNG（1×1 透明），用于 cover temp。"""
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII="
    )


def _make_info(
    carid: str = "ABF-340",
    title: str = "Test Title",
    magnet: str = "magnet:?xt=urn:btih:DEADBEEF",
    samples: list[str] | None = None,
    video_path: str = "",
    cover_path: str = "",
) -> dict:
    return {
        "carid": carid,
        "title": title,
        "release_date": "2024-01-15",
        "director": "",
        "producer": "Test Studio",
        "publisher": "",
        "category": "Drama / Romance",
        "actors": "Actor A / Actor B",
        "magnet": magnet,
        "samples": samples or [],
        "path": video_path,
        "cover": cover_path,
    }


def _stub_download_methods(exporter: MovieExporter, cover_path: Path):
    """Stub：让 base class 的 cover/sample 下载直接生成空文件。

    返回值是 (cover_patch, samples_patch) 两个 context manager，调用方需
    ``with cover_patch, samples_patch:`` 同时进入。
    """
    from javlibraryscrapy.scraping.javbus import JavbusSpider

    def fake_download_cover(self, img_url, car_id):
        cover_path.write_bytes(_fake_png_bytes())
        return cover_path

    def fake_download_samples(self, sample_urls, car_id):
        paths = []
        for i, _ in enumerate(sample_urls, start=1):
            p = self.root_dir / f"{car_id}_sample_{i:03d}.jpg"
            p.write_bytes(b"fake_sample")
            paths.append(p)
        return paths

    cover_patch = patch.object(JavbusSpider, "download_cover", fake_download_cover)
    samples_patch = patch.object(JavbusSpider, "download_samples", fake_download_samples)
    return cover_patch, samples_patch


def _stub_javlibrary_cover(exporter: MovieExporter, succeed: bool = True):
    """Stub：MovieExporter._download_javlibrary_cover 不发请求。"""
    def fake(url, dest, timeout=10):
        if not succeed:
            return False
        dest.write_bytes(b"fake_poster")
        return True
    return patch.object(exporter, "_download_javlibrary_cover", fake)


# --------------------------------------------------------------------------- #
# 基本流程
# --------------------------------------------------------------------------- #
def test_process_movie_basic():
    """单部 process_movie：建子目录 + 写 NFO + fanart + cover + magnet 收集。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())

        e = MovieExporter(
            output_root=root,
            move_video=False,
            download_samples=False,
            collect_magnets=True,
        )
        with _stub_javlibrary_cover(e, succeed=False):  # 不下 poster
            asyncio.run(e.process_movie(_make_info(cover_path=str(cover_temp))))

        save_dir = root / "ABF-340 Test Title"
        assert save_dir.is_dir()
        assert (save_dir / "movie.nfo").is_file()
        assert (save_dir / "fanart.jpg").is_file()
        assert not (save_dir / "poster.jpg").exists()  # cover_urls 没给，跳过
        assert not cover_temp.exists()  # temp PNG 被 rename / unlink

        assert len(e._magnet_results) == 1
        assert e._magnet_results[0]["status"] == "ok"
        assert e._magnet_results[0]["code"] == "ABF-340"
        assert "ABF-340" in e._written_codes
        print("✅ test_process_movie_basic")


# --------------------------------------------------------------------------- #
# move_video 开关
# --------------------------------------------------------------------------- #
def test_move_video_true():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        video_src = root / "src.mp4"
        video_src.write_bytes(b"video")
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())

        e = MovieExporter(output_root=root, move_video=True)
        with _stub_javlibrary_cover(e, succeed=False):
            asyncio.run(e.process_movie(_make_info(
                video_path=str(video_src),
                cover_path=str(cover_temp),
            )))

        assert not video_src.exists(), "video 应当被移走"
        assert (root / "ABF-340 Test Title" / "ABF-340 Test Title.mp4").is_file()
        print("✅ test_move_video_true")


def test_move_video_false():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        video_src = root / "src.mp4"
        video_src.write_bytes(b"video")
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())

        e = MovieExporter(output_root=root, move_video=False)
        with _stub_javlibrary_cover(e, succeed=False):
            asyncio.run(e.process_movie(_make_info(
                video_path=str(video_src),
                cover_path=str(cover_temp),
            )))

        assert video_src.exists(), "video 不应被移走"
        print("✅ test_move_video_false")


def test_move_video_empty_path():
    """info['path']=''（wanted/单部刷新场景）不能被 Path('') 解释成 cwd。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        e = MovieExporter(output_root=root, move_video=True)
        with _stub_javlibrary_cover(e, succeed=False):
            # path='' + 无 cover → 只写 NFO
            asyncio.run(e.process_movie(_make_info(video_path="", cover_path="")))
        # 应当不抛异常；NFO 仍写入
        assert (root / "ABF-340 Test Title" / "movie.nfo").is_file()
        print("✅ test_move_video_empty_path")


# --------------------------------------------------------------------------- #
# download_samples 开关
# --------------------------------------------------------------------------- #
def test_download_samples_true():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())

        e = MovieExporter(output_root=root, download_samples=True)
        cover_patch, samples_patch = _stub_download_methods(e, cover_temp)
        with cover_patch, samples_patch, _stub_javlibrary_cover(e, succeed=False):
            asyncio.run(e.process_movie(_make_info(
                cover_path=str(cover_temp),
                samples=["https://example.com/s1.jpg", "https://example.com/s2.jpg"],
            )))

        save_dir = root / "ABF-340 Test Title"
        assert (save_dir / "sample_001.jpg").is_file()
        assert (save_dir / "sample_002.jpg").is_file()
        # 临时 <CARID>_sample_*.jpg 应被清理
        assert not list(root.glob("ABF-340_sample_*.jpg"))
        print("✅ test_download_samples_true")


def test_download_samples_false():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())

        e = MovieExporter(output_root=root, download_samples=False)
        with _stub_javlibrary_cover(e, succeed=False):
            asyncio.run(e.process_movie(_make_info(
                cover_path=str(cover_temp),
                samples=["https://example.com/s1.jpg"],
            )))

        save_dir = root / "ABF-340 Test Title"
        assert not (save_dir / "sample_001.jpg").exists()
        print("✅ test_download_samples_false")


# --------------------------------------------------------------------------- #
# collect_magnets 开关
# --------------------------------------------------------------------------- #
def test_collect_magnets_true_writes_files():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())

        e = MovieExporter(output_root=root, collect_magnets=True)
        with _stub_javlibrary_cover(e, succeed=False):
            asyncio.run(e.process_movie(_make_info(cover_path=str(cover_temp))))
            e._write_magnets_index()

        assert (root / "magnets.json").is_file()
        assert (root / "magnets_links.txt").is_file()
        data = json.loads((root / "magnets.json").read_text())
        assert data["schema_version"] == 2
        assert len(data["items"]) == 1
        links = (root / "magnets_links.txt").read_text()
        assert "magnet:?xt=urn:btih:DEADBEEF" in links
        print("✅ test_collect_magnets_true_writes_files")


def test_collect_magnets_false_no_files():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())

        e = MovieExporter(output_root=root, collect_magnets=False)
        with _stub_javlibrary_cover(e, succeed=False):
            asyncio.run(e.process_movie(_make_info(cover_path=str(cover_temp))))
            e._write_magnets_index()  # 收集为空，不写文件

        assert not (root / "magnets.json").exists()
        assert not (root / "magnets_links.txt").exists()
        print("✅ test_collect_magnets_false_no_files")


# --------------------------------------------------------------------------- #
# magnet status 映射
# --------------------------------------------------------------------------- #
def test_magnet_status_mapping():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        e = MovieExporter(output_root=root, collect_magnets=True)
        with _stub_javlibrary_cover(e, succeed=False):
            # ok：有 magnet
            asyncio.run(e.process_movie(_make_info(carid="OK-001", title="A",
                                                   magnet="magnet:?xt=OK")))
            # no_magnet：解析成功但 magnet 空
            asyncio.run(e.process_movie(_make_info(carid="NM-001", title="B",
                                                   magnet="")))
            # failed：title 空（解析失败）
            asyncio.run(e.process_movie(_make_info(carid="FAIL-001", title="",
                                                   magnet="")))

        statuses = {r["code"]: r["status"] for r in e._magnet_results}
        assert statuses["OK-001"] == "ok"
        assert statuses["NM-001"] == "no_magnet"
        assert "FAIL-001" not in statuses  # failed 的不入 _magnet_results
        # failed codes 走 _failed_codes
        assert "FAIL-001" in e._failed_codes
        print("✅ test_magnet_status_mapping")


# --------------------------------------------------------------------------- #
# fanart.jpg 已存在
# --------------------------------------------------------------------------- #
def test_existing_fanart_not_overwritten():
    """fanart.jpg 已存在时，cover temp 不被 rename 到 fanart，且 temp 被 unlink。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())

        # 预先放一个 fanart.jpg
        save_dir = root / "ABF-340 Test Title"
        save_dir.mkdir()
        fanart = save_dir / "fanart.jpg"
        fanart.write_bytes(b"existing_fanart")

        e = MovieExporter(output_root=root)
        with _stub_javlibrary_cover(e, succeed=False):
            asyncio.run(e.process_movie(_make_info(cover_path=str(cover_temp))))

        assert fanart.read_bytes() == b"existing_fanart", "fanart 不应被覆盖"
        assert not cover_temp.exists(), "cover temp 应当被清理"
        print("✅ test_existing_fanart_not_overwritten")


# --------------------------------------------------------------------------- #
# poster.jpg（cover_urls）
# --------------------------------------------------------------------------- #
def test_cover_urls_triggers_poster_download():
    """给 cover_urls 时，poster.jpg 被下载。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())

        e = MovieExporter(output_root=root)
        # process_movie 通过 self._cover_urls 拿 cover_url；调用 export_movies
        # 之外的场景下手动注入。
        e._cover_urls = {"ABF-340": "https://example.com/cover.jpg"}
        with _stub_javlibrary_cover(e, succeed=True):
            asyncio.run(e.process_movie(_make_info(cover_path=str(cover_temp))))

        save_dir = root / "ABF-340 Test Title"
        assert (save_dir / "poster.jpg").is_file()
        assert (save_dir / "poster.jpg").read_bytes() == b"fake_poster"
        print("✅ test_cover_urls_triggers_poster_download")


def test_cover_urls_missing_skips_poster():
    """没给 cover_urls 时，poster.jpg 不存在。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())

        e = MovieExporter(output_root=root)
        with _stub_javlibrary_cover(e, succeed=True):
            asyncio.run(e.process_movie(_make_info(cover_path=str(cover_temp))))
        save_dir = root / "ABF-340 Test Title"
        assert not (save_dir / "poster.jpg").exists()
        print("✅ test_cover_urls_missing_skips_poster")


# --------------------------------------------------------------------------- #
# 幂等性：第二次 process_movie 不报错
# --------------------------------------------------------------------------- #
def test_idempotent_rerun():
    """同一个车跑两次：第二次不报错，NFO 仍写，poster/fanart/sample 跳过。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())

        e = MovieExporter(output_root=root)
        e._cover_urls = {"ABF-340": "https://example.com/cover.jpg"}
        with _stub_javlibrary_cover(e, succeed=True):
            info = _make_info(cover_path=str(cover_temp))
            asyncio.run(e.process_movie(info))
            # 第二次：cover temp 已被消费（不存在）
            asyncio.run(e.process_movie(info))

        # 没有异常；NFO 写了
        save_dir = root / "ABF-340 Test Title"
        assert (save_dir / "movie.nfo").is_file()
        assert (save_dir / "fanart.jpg").is_file()
        assert (save_dir / "poster.jpg").is_file()
        # 第二次 cover 没东西可处理 → fanart 没被覆盖
        assert (save_dir / "fanart.jpg").read_bytes() == _fake_png_bytes()
        print("✅ test_idempotent_rerun")


# --------------------------------------------------------------------------- #
# Bug #1 回归：download_samples 部分失败时，sample idx 不错位、不缺失
# --------------------------------------------------------------------------- #
def test_download_samples_partial_failure_preserves_idx():
    """download_samples 返回不连续 path 列表时，_move_samples_to_target 仍按 URL idx 命名。

    场景：原 8 张 URL，下载时 idx=3 失败 → download_samples 返回 [path_1, path_2,
    path_4, ..., path_8]（7 个，缺 idx=3）。修复前调用方用 enumerate(start=1)，
    path_2 被命名为 sample_002.jpg（应该是 sample_003），idx=8 真的缺失。
    修复后从 src.name 反推 idx → 各自归位，idx=3 留空（不假造文件）。
    """
    from javlibraryscrapy.scraping.javbus import JavbusSpider

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())

        # Stub：模拟 idx=3 失败（其余成功）。每个 path 用不同字节便于断言谁是谁
        def fake_download_samples_partial(self, sample_urls, car_id):
            paths = []
            for idx, _ in enumerate(sample_urls, start=1):
                if idx == 3:
                    continue  # 模拟失败 → 不在 paths 里
                p = self.root_dir / f"{car_id}_sample_{idx:03d}.jpg"
                p.write_bytes(f"sample_idx_{idx}".encode())
                paths.append(p)
            return paths

        def fake_download_cover(self, img_url, car_id):
            cover_temp.write_bytes(_fake_png_bytes())
            return cover_temp

        e = MovieExporter(output_root=root, download_samples=True)
        with patch.object(JavbusSpider, "download_cover", fake_download_cover), \
             patch.object(JavbusSpider, "download_samples", fake_download_samples_partial), \
             _stub_javlibrary_cover(e, succeed=False):
            urls = [f"https://x.com/s{i}.jpg" for i in range(1, 9)]  # 8 张
            asyncio.run(e.process_movie(_make_info(
                cover_path=str(cover_temp),
                samples=urls,
            )))

        save_dir = root / "ABF-340 Test Title"
        # 期望：sample_001.jpg 来自 idx=1，sample_002.jpg 来自 idx=2，
        # sample_003.jpg 不存在（idx=3 失败），sample_004~008 各归其位
        assert (save_dir / "sample_001.jpg").read_bytes() == b"sample_idx_1"
        assert (save_dir / "sample_002.jpg").read_bytes() == b"sample_idx_2"
        assert not (save_dir / "sample_003.jpg").exists(), "idx=3 真失败，不假造文件"
        assert (save_dir / "sample_004.jpg").read_bytes() == b"sample_idx_4"
        assert (save_dir / "sample_005.jpg").read_bytes() == b"sample_idx_5"
        assert (save_dir / "sample_006.jpg").read_bytes() == b"sample_idx_6"
        assert (save_dir / "sample_007.jpg").read_bytes() == b"sample_idx_7"
        assert (save_dir / "sample_008.jpg").read_bytes() == b"sample_idx_8"
        # 临时文件全部清理
        assert not list(root.glob("ABF-340_sample_*.jpg"))
        print("✅ test_download_samples_partial_failure_preserves_idx")


# --------------------------------------------------------------------------- #
# cleanup：临时 PNG 清理
# --------------------------------------------------------------------------- #
def test_cleanup_temp_pngs():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 制造残留
        (root / "ABF-340.png").write_bytes(b"x")
        (root / "MIAB-001.png").write_bytes(b"x")
        # 不应误伤的：子目录里的 png
        sub = root / "ABF-340 Test"
        sub.mkdir()
        (sub / "fanart.png").write_bytes(b"x")

        e = MovieExporter(output_root=root)
        e._cleanup_temp_pngs()

        assert not (root / "ABF-340.png").exists()
        assert not (root / "MIAB-001.png").exists()
        assert (sub / "fanart.png").exists(), "子目录里的 png 不应被删"
        print("✅ test_cleanup_temp_pngs")


# --------------------------------------------------------------------------- #
# magnets_index 自定义路径
# --------------------------------------------------------------------------- #
def test_magnets_index_custom_path():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        custom_dir = root / "custom"
        custom_dir.mkdir()
        e = MovieExporter(
            output_root=root,
            collect_magnets=True,
            magnets_index=custom_dir / "mag.json",
        )
        cover_temp = root / "ABF-340.png"
        cover_temp.write_bytes(_fake_png_bytes())
        with _stub_javlibrary_cover(e, succeed=False):
            asyncio.run(e.process_movie(_make_info(cover_path=str(cover_temp))))
            e._write_magnets_index()

        assert (custom_dir / "mag.json").is_file()
        assert (custom_dir / "magnets_links.txt").is_file()
        assert not (root / "magnets.json").exists(), "默认路径不应被写"
        print("✅ test_magnets_index_custom_path")


# --------------------------------------------------------------------------- #
# export_movies 整体（mock 掉 crawl_and_process）
# --------------------------------------------------------------------------- #
def test_export_movies_calls_crawl_and_returns_stats():
    """export_movies 应该调 crawl_and_process，统计正确，cleanup 触发。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        e = MovieExporter(output_root=root, collect_magnets=True)

        async def fake_crawl(self, car_list):
            # 模拟 parse + process_movie 一部成功、一部 failed
            cover1 = root / "OK-001.png"
            cover1.write_bytes(_fake_png_bytes())
            await self.process_movie(_make_info(
                carid="OK-001", title="A", cover_path=str(cover1)
            ))
            await self.process_movie(_make_info(
                carid="FAIL-001", title="", cover_path=""
            ))

        from javlibraryscrapy.scraping.javbus import JavbusSpider
        with _stub_javlibrary_cover(e, succeed=False):
            with patch.object(JavbusSpider, "crawl_and_process", fake_crawl):
                stats = asyncio.run(e.export_movies([
                    ("OK-001", ""),
                    ("FAIL-001", ""),
                ]))

        assert stats["total"] == 2
        assert stats["written"] == 1
        assert stats["failed"] == 1
        assert stats["magnets_collected"] == 1
        print("✅ test_export_movies_calls_crawl_and_returns_stats")


def test_export_movies_on_progress_callback():
    """on_progress 被每个车调用一次（成功/失败两种状态）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        e = MovieExporter(output_root=root, collect_magnets=False)

        async def fake_crawl(self, car_list):
            cover1 = root / "OK-001.png"
            cover1.write_bytes(_fake_png_bytes())
            await self.process_movie(_make_info(
                carid="OK-001", title="A", cover_path=str(cover1)
            ))
            await self.process_movie(_make_info(
                carid="FAIL-001", title="", cover_path=""
            ))

        calls = []
        def progress(code, status):
            calls.append((code, status))

        from javlibraryscrapy.scraping.javbus import JavbusSpider
        with _stub_javlibrary_cover(e, succeed=False):
            with patch.object(JavbusSpider, "crawl_and_process", fake_crawl):
                asyncio.run(e.export_movies(
                    [("OK-001", ""), ("FAIL-001", "")],
                    on_progress=progress,
                ))

        # FAIL-001 不在 parsed_codes（process_movie 早返回）→ 不进 on_progress
        # 但 OK-001 → "ok"
        codes_called = [c for c, _ in calls]
        assert "OK-001" in codes_called
        for code, status in calls:
            if code == "OK-001":
                assert status == "ok"
        print("✅ test_export_movies_on_progress_callback")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    test_process_movie_basic()
    test_move_video_true()
    test_move_video_false()
    test_move_video_empty_path()
    test_download_samples_true()
    test_download_samples_false()
    test_collect_magnets_true_writes_files()
    test_collect_magnets_false_no_files()
    test_magnet_status_mapping()
    test_existing_fanart_not_overwritten()
    test_cover_urls_triggers_poster_download()
    test_cover_urls_missing_skips_poster()
    test_idempotent_rerun()
    test_cleanup_temp_pngs()
    test_magnets_index_custom_path()
    test_export_movies_calls_crawl_and_returns_stats()
    test_export_movies_on_progress_callback()
    test_download_samples_partial_failure_preserves_idx()
    print("\n🎉 ALL TESTS PASSED")
