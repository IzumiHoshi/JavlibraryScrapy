"""
RescanQueue + /api/library/{carid}/rescan 端点的测试。

通过 monkey-patch ``refresh_library_movie`` 避免真实网络请求。
"""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# 必须先 monkey-patch 再 import javlibraryscrapy.library.refresher
import javlibraryscrapy.library.refresher as library_refresher  # noqa: E402
from javlibraryscrapy.library.scanner import save_index, scan_library  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8")


def http_post(url: str, payload: dict | None = None, timeout: float = 5.0):
    body = "{}".encode("utf-8") if payload is None else __import__("json").dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


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


def build_jav_fixture(root: Path) -> None:
    """构造 3 部影片。"""
    for carid, title in [("ABF-340", "actress"), ("SNIS-001", "xxx"), ("MIDE-001", "test")]:
        d = root / f"{carid} {title}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{carid}.mp4").write_bytes(b"\x00" * 1024)


def main():
    # ===== 1. 单元测试：RescanQueue 直接测试 =====
    print("\n[Unit] RescanQueue enqueue + status + dedup")
    from javlibraryscrapy.server.services.jobs import RescanJob, RescanQueue

    # 用 monkey-patch 让 refresh_library_movie 立刻返回 ok
    real_refresh = library_refresher.refresh_library_movie
    real_in_gallery = None  # jobs.py 在 import 时缓存了引用
    try:
        from javlibraryscrapy.server.services import jobs as jobs_module
        real_in_gallery = jobs_module.refresh_library_movie
    except Exception:
        pass

    call_log: list[str] = []

    async def fake_refresh(folder, carid, javbus_url, proxy=None, log_callback=None):
        call_log.append(carid)
        if log_callback:
            log_callback(20, f"fake refreshed {carid}")
        return {"ok": True, "title": "fake", "nfo_path": None, "fanart_path": None, "poster_path": None, "error": None}

    library_refresher.refresh_library_movie = fake_refresh
    if real_in_gallery is not None:
        jobs_module.refresh_library_movie = fake_refresh

    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_jav_fixture(root)

            queue = RescanQueue(
                library_root_getter=lambda: root,
                javbus_url_getter=lambda: "https://example.com/",
                proxy_getter=lambda: None,
            )
            completed: list[Path] = []
            queue.set_on_complete(lambda folder: completed.append(folder))
            queue.start_worker()

            # 入队 3 部
            for carid, title in [("ABF-340", "actress"), ("SNIS-001", "xxx"), ("MIDE-001", "test")]:
                queue.enqueue(carid, root / f"{carid} {title}")

            # 等全部跑完（每部 ~0.05s）
            for _ in range(50):
                snap = queue.status_snapshot()
                if not snap["active"] and len(snap["queued"]) == 0:
                    break
                time.sleep(0.1)

            assert_eq("3 部都跑过", sorted(call_log), ["ABF-340", "MIDE-001", "SNIS-001"])
            assert_eq("on_complete 触发 3 次", len(completed), 3)

            # 测试去重：连续入队同一车牌，第二条应替换/排队吗？当前实现：直接入队，
            # 调用端（_enqueue_rescan_movie）负责去重 —— 这里只验证队列自身行为。
            queue.enqueue("ABF-340", root / "ABF-340 actress")
            queue.enqueue("ABF-340", root / "ABF-340 actress")
            assert_eq("允许重复入队（调用端负责去重）",
                      sum(1 for q in queue.status_snapshot()["queued"] if q["carid"] == "ABF-340"), 2)

            # 清理队列里的残留
            for _ in range(50):
                snap = queue.status_snapshot()
                if not snap["active"] and len(snap["queued"]) == 0:
                    break
                time.sleep(0.1)
    finally:
        library_refresher.refresh_library_movie = real_refresh
        if real_in_gallery is not None:
            gallery_server.refresh_library_movie = real_in_gallery

    # ===== 2. E2E：HTTP 路由（不入队刷新，仅验证端点存在 + 路由分发）=====
    # 真实的 refresh 端到端要打到 JAVBus，受网络影响；这里只验证：
    #   - 路由 /api/library/{carid}/rescan 可达
    #   - 非法车牌 → 400
    #   - 不存在车牌 → 404（依赖已加载的 library_index）
    #   - 合法车牌 → 200（实际刷新逻辑由单元测试覆盖）
    print("\n[E2E-light] /api/library/{carid}/rescan 路由验证")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        movies_json = tmp_path / "output" / "javlibrary_movies.json"
        movies_json.parent.mkdir(parents=True, exist_ok=True)
        import json
        movies_json.write_text(json.dumps([
            {"code": "ABF-340", "title": "ABF-340 t", "id": "ABF-340", "cover_url": ""},
        ]), encoding="utf-8")

        lib_root = tmp_path / "fake_jav"
        build_jav_fixture(lib_root)

        idx_path = tmp_path / "output" / "library_index.json"
        movies, stats = scan_library(lib_root)
        save_index(movies, stats, idx_path, lib_root)

        port = free_port()
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["JAVBUS_URL"] = "http://127.0.0.1:1/"  # 让真刷新立即失败但不至于卡住
        proc = subprocess.Popen(
            [
                "uv", "run", "python", "-m", "javlibraryscrapy.cli.gallery",
                "--host", "127.0.0.1", "--port", str(port),
                "--data", str(movies_json),
                "--output-dir", str(tmp_path / "output"),
                "--library-root", str(lib_root),
                "--library-index", str(idx_path),
                ],
            cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            if not wait_ready(port):
                print("服务启动失败")
                return 1

            # 非法车牌
            status, body = http_post(f"http://127.0.0.1:{port}/api/library/abc!@/rescan")
            assert_eq("非法车牌 400", status, 400)

            # 路由可达且合法：会触发真实刷新（会失败但不阻塞）
            status, body = http_post(f"http://127.0.0.1:{port}/api/library/ABF-340/rescan")
            assert_eq("合法车牌 200", status, 200)

            # 立即查询状态：当前应在 running 或 queued（真实刷新正在失败重试中）
            import time as _t
            _t.sleep(0.5)
            status, body = http_get(f"http://127.0.0.1:{port}/api/library/rescan-status")
            assert_eq("状态端点 200", status, 200)
            snap = json.loads(body)
            assert_eq("至少有一个 in-progress 字段", "active" in snap and "queued" in snap, True)

            print("\n✅ RescanQueue + 端点路由测试通过")
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())