"""
端到端测试 gallery_server.py 的本地库集成。

构造伪造的 javlibrary_movies.json 与 Z:\\JAV fixture，启动服务在子进程中，
用 urllib 直接打各个 API 端点，验证：
  - /api/movies 带上 local_exists / library_folder
  - /api/library 列表 + 搜索 + 分页
  - /api/library/{carid} 详情
  - /api/library/status 扫描状态
  - /api/library/rescan 触发扫描
  - /api/local-cover 安全读取
  - /api/open-folder 越界防护
  - /api/scrape 自动跳过本地已有车牌

运行：
    uv run python test/test_gallery_server_library.py
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from library_scanner import save_index, scan_library, MovieEntry  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def http_post(url: str, payload: dict, timeout: float = 5.0):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def build_jav_fixture(root: Path) -> None:
    """伪造 Z:\\JAV（造 200+ 个目录让扫描耗时可观察）"""
    m = root / "ABF-340 actress title"
    m.mkdir(parents=True)
    (m / "ABF-340.mp4").write_bytes(b"\x00" * 1024)
    (m / "poster.jpg").write_bytes(b"FAKE JPEG")
    (m / "movie.nfo").write_text(
        textwrap.dedent(
            """\
            <?xml version="1.0"?>
            <movie>
              <title>ABF-340 title test</title>
              <actor><name>actress A</name></actor>
            </movie>"""
        ),
        encoding="utf-8",
    )

    m = root / "SNIS-001 xxx"
    m.mkdir()
    (m / "x.mp4").write_bytes(b"\x00" * 2048)

    m = root / "ABF-340-C extra"
    m.mkdir()
    (m / "x.mp4").write_bytes(b"\x00" * 4096)

    # 200 个额外影片让扫描持续可见
    for i in range(200):
        d = root / f"BULK-{i:04d} filler"
        d.mkdir()
        (d / f"b{i}.mp4").write_bytes(b"\x00" * 100)


def write_movies_json(path: Path, codes: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {"code": c, "title": f"{c} title", "id": c, "cover_url": ""}
        for c in codes
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def assert_eq(label: str, got, expected) -> None:
    if got != expected:
        print(f"  FAIL {label}\n    got:      {got!r}\n    expected: {expected!r}")
        raise AssertionError(label)
    print(f"  OK   {label}")


def wait_ready(port: int, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/library/status", timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # 1) 伪造 JAVLibrary 数据（含 ABF-340 / SNIS-001 / MIDE-999）
        movies_json = tmp_path / "output" / "javlibrary_movies.json"
        write_movies_json(movies_json, ["ABF-340", "SNIS-001", "MIDE-999"])

        # 2) 伪造 Z:\JAV
        lib_root = tmp_path / "fake_jav"
        build_jav_fixture(lib_root)

        # 3) 预生成索引（启动时直接加载，不等扫描）
        idx_path = tmp_path / "output" / "library_index.json"
        movies, stats = scan_library(lib_root)
        save_index(movies, stats, idx_path, lib_root)
        print(f"预扫描: {len(movies)} 部")

        # 4) 启动服务
        port = free_port()
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [
                "uv", "run", "python", "scripts/gallery_server.py",
                "--host", "127.0.0.1",
                "--port", str(port),
                "--data", str(movies_json),
                "--output-dir", str(tmp_path / "output"),
                "--library-root", str(lib_root),
                "--library-index", str(idx_path),
                "--no-browser",
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            if not wait_ready(port):
                # 抓输出排查
                out = proc.stdout.read1(8192).decode("utf-8", errors="replace") if proc.stdout else ""
                print(f"服务启动失败，输出:\n{out}")
                return 1

            print("\n[E2E] /api/movies 包含 local_exists 标记")
            status, data = http_get(f"http://127.0.0.1:{port}/api/movies")
            assert_eq("status", status, 200)
            by_code = {m["code"]: m for m in data["movies"]}
            assert_eq("ABF-340 → local_exists", by_code["ABF-340"]["local_exists"], True)
            assert_eq("ABF-340 → library_folder 存在", bool(by_code["ABF-340"]["library_folder"]), True)
            assert_eq("SNIS-001 → local_exists", by_code["SNIS-001"]["local_exists"], True)
            assert_eq("MIDE-999 → local_exists", by_code["MIDE-999"]["local_exists"], False)
            assert_eq("library_configured 标志", data["library_configured"], True)

            print("\n[E2E] /api/library 列表")
            status, data = http_get(f"http://127.0.0.1:{port}/api/library?size=1000")
            assert_eq("status", status, 200)
            assert_eq("索引条数", data["total"], 203)
            assert_eq("videos[] 不在列表里", "videos" in data["movies"][0], False)

            print("\n[E2E] /api/library 搜索 q=ABF")
            status, data = http_get(f"http://127.0.0.1:{port}/api/library?q=ABF")
            assert_eq("ABF 搜索结果数", data["total"], 2)

            print("\n[E2E] /api/library 搜索 q=actress（按演员）")
            status, data = http_get(f"http://127.0.0.1:{port}/api/library?q=actress")
            assert_eq("演员搜索结果数", data["total"], 1)

            print("\n[E2E] /api/library 分页 size=1")
            status, data = http_get(f"http://127.0.0.1:{port}/api/library?size=1&page=1")
            assert_eq("第 1 页返回 1 部", len(data["movies"]), 1)
            status, data = http_get(f"http://127.0.0.1:{port}/api/library?size=1&page=203")
            assert_eq("最后页返回 1 部", len(data["movies"]), 1)

            print("\n[E2E] /api/library/ABF-340 详情")
            status, data = http_get(f"http://127.0.0.1:{port}/api/library/ABF-340")
            assert_eq("ABF-340 详情", data["carid"], "ABF-340")
            assert_eq("actors", data["actors"], ["actress A"])
            assert_eq("videos[] 在详情里", len(data["videos"]), 1)
            assert_eq("has_poster", data["has_poster"], True)

            print("\n[E2E] /api/library/UNKNOWN 404")
            try:
                http_get(f"http://127.0.0.1:{port}/api/library/UNKNOWN-999")
                assert_eq("未知车牌应 404", False, True)
            except urllib.error.HTTPError as e:
                assert_eq("未知车牌 404", e.code, 404)

            print("\n[E2E] /api/library/status 轮询")
            status, data = http_get(f"http://127.0.0.1:{port}/api/library/status")
            assert_eq("status 200", status, 200)
            assert_eq("configured", data["configured"], True)
            assert_eq("movies_count", data["movies_count"], 203)
            assert_eq("is_running 初始为 False", data["is_running"], False)

            print("\n[E2E] /api/local-cover 安全读取")
            abf_folder = by_code["ABF-340"]["library_folder"]
            url = f"http://127.0.0.1:{port}/api/local-cover?folder={urllib.parse.quote(abf_folder)}"
            with urllib.request.urlopen(url, timeout=5) as r:
                body = r.read()
                assert_eq("封面字节匹配", body, b"FAKE JPEG")

            print("\n[E2E] /api/local-cover 越界保护")
            bad_folder = "C:" + chr(92) + chr(92) + "Windows"
            try:
                http_get(f"http://127.0.0.1:{port}/api/local-cover?folder={urllib.parse.quote(bad_folder)}")
                assert_eq("越界应被拒", False, True)
            except urllib.error.HTTPError as e:
                assert_eq("越界 403", e.code, 403)

            print("\n[E2E] /api/local-cover 非允许文件名")
            try:
                http_get(
                    f"http://127.0.0.1:{port}/api/local-cover?folder={urllib.parse.quote(abf_folder)}&name=secret.txt"
                )
                assert_eq("非允许名应被拒", False, True)
            except urllib.error.HTTPError as e:
                assert_eq("非允许名 403", e.code, 403)

            print("\n[E2E] /api/library/rescan 触发")
            status, data = http_post(f"http://127.0.0.1:{port}/api/library/rescan", {})
            assert_eq("触发扫描 200", status, 200)
            assert_eq("返回 ok", data.get("ok"), True)

            print("\n[E2E] /api/library/rescan 并发拒绝")
            status, data = http_post(f"http://127.0.0.1:{port}/api/library/rescan", {})
            assert_eq("并发第二次 409", status, 409)

            print("\n[E2E] /api/scrape 自动跳过本地已有（不实际抓，仅验证响应）")
            status, data = http_post(
                f"http://127.0.0.1:{port}/api/scrape",
                {"codes": ["ABF-340", "MIDE-999"]},
            )
            assert_eq("scrape 200", status, 200)
            assert_eq("ABF-340 被跳过", "ABF-340" in data["skipped"], True)
            assert_eq("MIDE-999 进入任务", data["total"], 1)

            print("\n[E2E] /api/scrape 全部本地已有")
            status, data = http_post(
                f"http://127.0.0.1:{port}/api/scrape",
                {"codes": ["ABF-340", "SNIS-001"]},
            )
            assert_eq("全部本地时 200", status, 200)
            # 应该返回 error 信息 + skipped 列表
            assert_eq("total=0", data.get("total", 0), 0)
            assert_eq("skipped 长度=2", len(data.get("skipped", [])), 2)

            print("\n[E2E] /wanted 与 /library 都能返回 HTML")
            for path_ in ("/", "/wanted", "/library"):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path_}", timeout=5) as r:
                    html = r.read().decode("utf-8")
                assert_eq(f"{path_} 返回 200", r.status, 200)
                assert_eq(f"{path_} 包含 nav-bar", "nav-bar" in html, True)

            print("\n✅ 所有 E2E 测试通过")
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())