#!/usr/bin/env python
"""
影片画廊本地服务器

把 output/ 下的 JAVLibrary 抓取结果（javlibrary_movies.json / .csv）以卡片形式
展示在网页上，勾选影片后一键调用 JavbusSpider.crawl_and_process 抓取磁力链接，
结果写入 output/magnets.json 与 output/magnets_links.txt。

仅依赖 Python 标准库的 http.server，无需额外 Web 框架。

用法：
    uv run python scripts/gallery_server.py
    uv run python scripts/gallery_server.py --port 8000 --data output/javlibrary_movies.json
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.parse
import uuid
import webbrowser
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

# 先加载项目根目录的 .env，再导入 JavbusSpider；其构造函数会读取代理配置。
load_dotenv(ROOT / ".env")

from javbus_scrapling import JavbusSpider  # noqa: E402
from library_scanner import (  # noqa: E402
    LibraryIndex,
    ScanProgress,
    load_index,
    save_index,
    scan_library,
)

logger = logging.getLogger("gallery")

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "gallery.html"
MAX_CODES_PER_JOB = 300
MAX_LOG_LINES = 800


def proxy_url_from_env() -> Optional[str]:
    """读取项目 .env 中配置的代理地址。"""
    return os.getenv("PROXY", "").strip() or None


def proxy_enabled_from_env() -> bool:
    """读取 PROXY_ENABLED；仅用于控制封面代理的 auto 模式。"""
    return os.getenv("PROXY_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# --------------------------------------------------------------------------- #
# 数据加载
# --------------------------------------------------------------------------- #
def local_ip_address() -> str:
    """尽力获取当前机器可供局域网访问的 IPv4 地址。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def load_movies(data_path: Path) -> List[Dict[str, str]]:
    """
    从 JSON 或 CSV 加载影片列表，按 code 去重（保留首次出现的顺序）。

    Args:
        data_path: 数据文件路径（.json 或 .csv）

    Returns:
        [{"code", "title", "id", "cover_url"}, ...]
    """
    if not data_path.exists():
        # 允许 json/csv 互为回退
        alt = data_path.with_suffix(".csv" if data_path.suffix == ".json" else ".json")
        if alt.exists():
            logger.warning(f"{data_path.name} 不存在，改用 {alt.name}")
            data_path = alt
        else:
            raise FileNotFoundError(f"未找到数据文件：{data_path}")

    if data_path.suffix.lower() == ".csv":
        with open(data_path, "r", encoding="utf-8", newline="") as f:
            raw = list(csv.DictReader(f))
    else:
        with open(data_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):  # 兼容 {"movies": [...]} 形式
            raw = raw.get("movies", [])

    movies: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = (item.get("code") or "").strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        movies.append(
            {
                "code": code,
                "title": (item.get("title") or "").strip(),
                "id": (item.get("id") or "").strip(),
                "cover_url": (item.get("cover_url") or "").strip(),
            }
        )

    logger.info(f"已加载 {len(movies)} 部影片：{data_path}")
    return movies


# --------------------------------------------------------------------------- #
# 抓取任务
# --------------------------------------------------------------------------- #
class ScrapeJob:
    """一次磁力抓取任务的状态容器（线程安全）"""

    def __init__(self, job_id: str, codes: List[str]):
        self.id = job_id
        self.codes = codes
        self.status = "running"  # running | done | error
        self.error: Optional[str] = None
        self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.outputs: Dict[str, str] = {}
        self.logs: deque[str] = deque(maxlen=MAX_LOG_LINES)
        # Q4：本地库已存在、被前端发起但服务端过滤掉的 codes
        self.skipped: List[str] = []
        self._lock = threading.Lock()
        self._items: Dict[str, Dict[str, Any]] = {
            code: {"code": code, "status": "pending", "title": "", "magnet": None}
            for code in codes
        }

    def match_code(self, car_id: str) -> Optional[str]:
        """把 JAVBus 返回的车牌映射回请求列表中的 code"""
        if not car_id:
            return None
        key = car_id.strip().upper()
        return key if key in self._items else None

    def mark(self, code: str, status: str, **fields: Any) -> None:
        with self._lock:
            item = self._items.get(code)
            if item is None:
                return
            item["status"] = status
            item.update(fields)

    def add_log(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)

    def results(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(self._items[code]) for code in self.codes]

    def finalize(self) -> None:
        """把仍未处理的条目标记为失败"""
        with self._lock:
            for item in self._items.values():
                if item["status"] == "pending":
                    item["status"] = "failed"

    def snapshot(self) -> Dict[str, Any]:
        items = self.results()
        finished = sum(1 for i in items if i["status"] != "pending")
        current = next((i["code"] for i in items if i["status"] == "pending"), None)
        with self._lock:
            logs = list(self.logs)
        return {
            "id": self.id,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "total": len(items),
            "finished": finished,
            "current": current if self.status == "running" else None,
            "succeeded": sum(1 for i in items if i["status"] == "ok"),
            "items": items,
            "logs": logs,
            "outputs": dict(self.outputs),
        }


class JobLogHandler(logging.Handler):
    """把爬虫的日志转发到任务状态里，供网页实时展示"""

    def __init__(self, job: ScrapeJob):
        super().__init__(level=logging.INFO)
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stamp = datetime.now().strftime("%H:%M:%S")
            self.job.add_log(f"{stamp} {record.getMessage()}")
        except Exception:  # 日志本身不能拖垮任务
            pass


class MagnetSpider(JavbusSpider):
    """
    只取磁力链接的 JavbusSpider 子类。

    - 跳过封面下载（覆写 download_cover）
    - 不落地 NFO / 视频文件（覆写 process_movie），只把结果记录到 ScrapeJob
    """

    def __init__(self, job: ScrapeJob, root_dir: Optional[Path] = None):
        super().__init__(root_dir=root_dir)
        self.job = job

    async def download_cover(self, img_url: str, car_id: str) -> Optional[Path]:
        return None

    async def process_movie(self, info: Dict[str, Any]) -> None:
        code = self.job.match_code(info.get("carid", ""))
        if code is None:
            logger.warning(f"忽略无法匹配的结果：{info.get('carid', '(空)')}")
            return

        magnet = info.get("magnet")
        self.job.mark(
            code,
            "ok" if magnet else "no_magnet",
            title=info.get("title", ""),
            magnet=magnet,
            release_date=info.get("release_date", ""),
            actors=info.get("actors", ""),
        )
        logger.info(
            "%s：解析结果回写任务，magnet=%s，长度=%d",
            code,
            "已获取" if magnet else "为空",
            len(magnet or ""),
        )
        logger.info(f"{code}：{'已获取磁力链接' if magnet else '页面无磁力链接'}")


def write_job_outputs(
    job: ScrapeJob,
    output_dir: Path,
    javbus_url: str,
    library_index: Optional[LibraryIndex] = None,
) -> Dict[str, str]:
    """把抓取结果写入 magnets.json（schema_version: 2）与 magnets_links.txt。

    Q4：本地库已存在但被前端发起、服务端过滤掉的 codes 也会写入 JSON（status=local_skip），
    但不会出现在 magnets_links.txt 里。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results = job.results()  # 仅本次任务实际抓取的 codes

    def annotate(r: Dict[str, Any]) -> Dict[str, Any]:
        match = library_index.find_match(r["code"]) if library_index else None
        return {
            **r,
            "local_exists": match is not None,
            "library_folder": match.folder if match else None,
        }

    items: List[Dict[str, Any]] = [annotate(r) for r in results]

    # 加入被跳过的 codes（status=local_skip，无 magnet）
    for code in job.skipped:
        match = library_index.find_match(code) if library_index else None
        items.append(
            {
                "code": code,
                "title": "",
                "magnet": None,
                "status": "local_skip",
                "release_date": "",
                "actors": "",
                "javbus_url": f"{javbus_url}{code}",
                "local_exists": True,
                "library_folder": match.folder if match else None,
            }
        )

    json_path = output_dir / "magnets.json"
    payload = {
        "schema_version": 2,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # 磁力链接文件：只含真正抓到 magnet 的条目，跳过 local_skip 与失败项
    links_path = output_dir / "magnets_links.txt"
    links = [r["magnet"] for r in results if r.get("magnet")]
    with open(links_path, "w", encoding="utf-8") as f:
        f.write("\n".join(links))
        if links:
            f.write("\n")

    logger.info(
        f"已写入 {json_path}（{len(items)} 条，本地跳过 {len(job.skipped)} 条）"
        f"与 {links_path}（{len(links)} 条磁力）"
    )
    return {"json": str(json_path), "links": str(links_path)}


def create_magnet_spider(
    job: ScrapeJob, output_dir: Path, proxy: Optional[str]
) -> MagnetSpider:
    """创建磁力爬虫，并显式应用从项目 .env 读取的代理。"""
    spider = MagnetSpider(job=job, root_dir=output_dir)
    spider.proxy_enabled = proxy is not None
    spider.proxy = proxy
    return spider


def run_scrape_job(
    job: ScrapeJob,
    output_dir: Path,
    proxy: Optional[str],
    library_index: Optional[LibraryIndex] = None,
) -> None:
    """在后台线程中执行抓取（内部自建事件循环）。"""
    handler = JobLogHandler(job)
    logging.getLogger().addHandler(handler)
    try:
        spider = create_magnet_spider(job, output_dir, proxy)
        logger.info(f"磁力抓取代理：{'已启用' if proxy else '未启用'}")
        # crawl_and_process 需要 [(车牌, 视频路径), ...]，这里没有本地视频，占位空串
        car_list = [(code, "") for code in job.codes]
        logger.info(f"开始抓取 {len(car_list)} 个车牌的磁力链接")
        asyncio.run(spider.crawl_and_process(car_list))
        job.status = "done"
    except Exception as e:  # noqa: BLE001 - 任何异常都要回报给页面
        logger.error(f"抓取任务失败：{e}")
        job.status = "error"
        job.error = str(e)
    finally:
        job.finalize()
        try:
            job.outputs = write_job_outputs(
                job,
                output_dir,
                os.getenv("JAVBUS_URL", "https://www.javbus.com/"),
                library_index=library_index,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"写入结果文件失败：{e}")
            job.error = job.error or f"写入结果文件失败：{e}"
        logging.getLogger().removeHandler(handler)


# --------------------------------------------------------------------------- #
# 应用状态
# --------------------------------------------------------------------------- #
class GalleryApp:
    """服务器共享状态：影片数据、当前任务、封面代理配置、本地库索引"""

    def __init__(
        self,
        data_path: Path,
        output_dir: Path,
        image_proxy_mode: str,
        library_root: Optional[Path] = None,
        library_index_path: Optional[Path] = None,
    ):
        self.data_path = data_path
        self.output_dir = output_dir
        self.movies = load_movies(data_path)

        # 磁力抓取始终使用 .env 中的 PROXY；PROXY_ENABLED 仅保留给封面 auto 模式。
        self.proxy = proxy_url_from_env()
        self.cover_proxy = self.proxy if proxy_enabled_from_env() else None
        self.user_agent = os.getenv("USER_AGENT", "Mozilla/5.0")
        self.verify_ssl = os.getenv("VERIFY_SSL", "False").lower() == "true"
        self.download_timeout = int(os.getenv("DOWNLOAD_TIMEOUT", "10"))
        self.javbus_url = os.getenv("JAVBUS_URL", "https://www.javbus.com/")
        if not self.javbus_url.endswith("/"):
            self.javbus_url += "/"

        # auto：PROXY_ENABLED=true 且配了 PROXY 才走服务端代理拉图
        if image_proxy_mode == "auto":
            self.image_proxy = bool(self.cover_proxy)
        else:
            self.image_proxy = image_proxy_mode == "on"
            if self.image_proxy:
                self.cover_proxy = self.proxy

        self.cover_cache_dir = output_dir / ".cover_cache"
        self.job: Optional[ScrapeJob] = None
        self._lock = threading.Lock()

        # ---- 本地影片库 ----
        # library_root 可为 None（未配置）；配置后必须有可读的本地目录
        self.library_root: Optional[Path] = library_root
        self.library_index_path: Path = library_index_path or (
            output_dir / "library_index.json"
        )
        self.library_index: LibraryIndex = LibraryIndex.empty()
        self.library_stats: Dict[str, Any] = {}
        self.library_scanned_at: Optional[str] = None
        self.scan_state: ScanProgress = ScanProgress()
        self._scan_lock = threading.Lock()
        self._maybe_load_library_index()

    # ---- 本地库 -------------------------------------------------------- #
    def _maybe_load_library_index(self) -> None:
        """启动时尝试加载已有索引。失败或 root 不一致时不报错（等手动刷新）。"""
        if not self.library_root:
            return
        if not self.library_index_path.exists():
            return
        data = load_index(self.library_index_path)
        if data is None:
            return
        if data.get("root") and data.get("root") != str(self.library_root):
            logger.warning(
                f"索引 root ({data.get('root')}) 与当前配置 "
                f"({self.library_root}) 不一致，标记为待重建"
            )
            return
        self.library_index = LibraryIndex.from_dict(data)
        self.library_stats = data.get("stats", {}) or {}
        self.library_scanned_at = data.get("scanned_at")
        logger.info(
            f"已加载本地库索引：{len(self.library_index)} 部，"
            f"上次扫描 {self.library_scanned_at}"
        )

    def start_rescan(self) -> bool:
        """触发后台扫描。返回 True 表示已启动，False 表示已在运行。

        is_running 在持锁状态下同步置位，再 spawn 线程，杜绝两次快速调用之间的 race。
        """
        with self._scan_lock:
            if self.scan_state.is_running:
                return False
            new_state = ScanProgress()
            new_state.is_running = True  # 关键：立即标记，让后续调用看到
            self.scan_state = new_state
            threading.Thread(target=self._run_rescan, daemon=True).start()
            return True

    def _run_rescan(self) -> None:
        """后台线程：扫描 → 落盘 → 替换索引。"""
        if not self.library_root:
            self.scan_state.is_running = False
            return
        try:
            # is_running 已在 start_rescan 中标记
            logger.info(f"开始后台扫描 {self.library_root} …")
            movies, stats = scan_library(
                self.library_root, progress=self.scan_state
            )
            save_index(movies, stats, self.library_index_path, self.library_root)
            # 重新加载并原子替换引用
            data = load_index(self.library_index_path)
            if data:
                self.library_index = LibraryIndex.from_dict(data)
                self.library_stats = data.get("stats", {}) or {}
                self.library_scanned_at = data.get("scanned_at")
            self.scan_state.is_complete = True
            logger.info(
                f"扫描完成：{len(self.library_index)} 部，"
                f"耗时 {stats.duration_seconds:.1f}s"
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"扫描失败：{e}")
            self.scan_state.error = str(e)
        finally:
            self.scan_state.is_running = False

    def is_within_library(self, path: Path) -> bool:
        """检查 path 是否在配置的 library_root 内（防越界）。"""
        if not self.library_root:
            return False
        try:
            target = path.resolve()
            root = self.library_root.resolve()
            return str(target).startswith(str(root))
        except OSError:
            return False

    def open_in_explorer(self, path: Path) -> None:
        """用系统资源管理器打开本地文件夹。"""
        path_str = str(path)
        if sys.platform == "win32":
            os.startfile(path_str)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", path_str], check=True)
        else:
            subprocess.run(["xdg-open", path_str], check=True)

    # ---- 任务 ---------------------------------------------------------- #
    def start_job(self, codes: List[str]) -> ScrapeJob:
        with self._lock:
            if self.job is not None and self.job.status == "running":
                raise RuntimeError("已有抓取任务正在运行，请等待完成")
            job = ScrapeJob(uuid.uuid4().hex[:12], codes)
            self.job = job
        threading.Thread(
            target=run_scrape_job,
            args=(job, self.output_dir, self.proxy, self.library_index),
            daemon=True,
        ).start()
        return job

    def get_job(self, job_id: str) -> Optional[ScrapeJob]:
        job = self.job
        return job if job is not None and job.id == job_id else None

    # ---- 封面 ---------------------------------------------------------- #
    def fetch_cover(self, url: str) -> Optional[Tuple[bytes, str]]:
        """服务端拉取封面（带代理与磁盘缓存），返回 (内容, content-type)"""
        if not url.startswith(("http://", "https://")):
            return None

        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            suffix = ".jpg"
        content_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }[suffix]

        cache_file = self.cover_cache_dir / (
            hashlib.sha1(url.encode("utf-8")).hexdigest() + suffix
        )
        if cache_file.exists():
            return cache_file.read_bytes(), content_type

        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Referer": "https://www.javlibrary.com/",
                    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                },
                timeout=self.download_timeout,
                proxies=(
                    {"http": self.cover_proxy, "https": self.cover_proxy}
                    if self.cover_proxy
                    else None
                ),
                verify=self.verify_ssl,
            )
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"封面下载失败 {url}: {e}")
            return None

        self.cover_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(resp.content)
        return resp.content, resp.headers.get("Content-Type", content_type)


# --------------------------------------------------------------------------- #
# HTTP 处理
# --------------------------------------------------------------------------- #
class GalleryHandler(BaseHTTPRequestHandler):
    server_version = "JavGallery/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> GalleryApp:
        return self.server.app  # type: ignore[attr-defined]

    # ---- 响应助手 ------------------------------------------------------ #
    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("%s - %s", self.address_string(), fmt % args)

    # ---- 路由 ---------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html", "/wanted"):
            return self._serve_page()
        if path == "/library":
            return self._serve_page()
        if path == "/api/movies":
            return self._serve_movies()
        if path == "/api/cover":
            return self._serve_cover(parsed.query)
        if path == "/api/local-cover":
            return self._serve_local_cover(parsed.query)
        if path.startswith("/api/job/"):
            return self._serve_job(path[len("/api/job/") :])
        # 本地库 API
        if path == "/api/library":
            return self._serve_library_list(parsed.query)
        if path == "/api/library/status":
            return self._serve_library_status()
        if path == "/api/library/warnings":
            return self._serve_library_warnings()
        if path.startswith("/api/library/"):
            carid = path[len("/api/library/"):]
            if carid:
                return self._serve_library_detail(carid)
        if path == "/favicon.ico":
            return self._send_bytes(b"", "image/x-icon", HTTPStatus.NO_CONTENT)

        self._send_json({"error": "未找到该路径"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/scrape":
            return self._start_scrape()
        if path == "/api/library/rescan":
            return self._trigger_rescan()
        if path == "/api/open-folder":
            return self._open_folder()
        self._send_json({"error": "未找到该路径"}, HTTPStatus.NOT_FOUND)

    # ---- 各端点 -------------------------------------------------------- #
    def _serve_page(self) -> None:
        if not TEMPLATE_PATH.exists():
            return self._send_bytes(
                f"缺少页面模板：{TEMPLATE_PATH}".encode("utf-8"),
                "text/plain; charset=utf-8",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        self._send_bytes(TEMPLATE_PATH.read_bytes(), "text/html; charset=utf-8")

    def _serve_movies(self) -> None:
        app = self.app
        idx = app.library_index
        movies = []
        for m in app.movies:
            cover = m["cover_url"]
            if cover and app.image_proxy:
                cover = "/api/cover?url=" + urllib.parse.quote(cover, safe="")
            # 双向前缀匹配本地库
            lib_match = idx.find_match(m["code"]) if idx else None
            movies.append(
                {
                    **m,
                    "cover": cover,
                    "javbus_url": app.javbus_url + m["code"],
                    "local_exists": lib_match is not None,
                    "library_folder": lib_match.folder if lib_match else None,
                }
            )

        job = app.job
        self._send_json(
            {
                "movies": movies,
                "source": str(app.data_path),
                "output_dir": str(app.output_dir),
                "active_job": job.id if job and job.status == "running" else None,
                "library_configured": app.library_root is not None,
            }
        )

    def _serve_cover(self, query: str) -> None:
        url = urllib.parse.parse_qs(query).get("url", [""])[0]
        result = self.app.fetch_cover(url)
        if result is None:
            return self._send_json({"error": "封面获取失败"}, HTTPStatus.NOT_FOUND)
        body, content_type = result
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def _serve_job(self, job_id: str) -> None:
        job = self.app.get_job(job_id)
        if job is None:
            return self._send_json({"error": "任务不存在"}, HTTPStatus.NOT_FOUND)
        self._send_json(job.snapshot())

    def _start_scrape(self) -> None:
        try:
            payload = self._read_json()
        except json.JSONDecodeError:
            return self._send_json({"error": "请求体不是合法 JSON"}, HTTPStatus.BAD_REQUEST)

        raw_codes = payload.get("codes") if isinstance(payload, dict) else None
        if not isinstance(raw_codes, list):
            return self._send_json({"error": "缺少 codes 列表"}, HTTPStatus.BAD_REQUEST)

        codes: List[str] = []
        for item in raw_codes:
            if not isinstance(item, str):
                continue
            code = item.strip().upper()
            # 车牌只允许字母、数字、连字符与下划线，防止拼进 URL 时被注入
            if code and re.fullmatch(r"[A-Z0-9_-]{2,32}", code) and code not in codes:
                codes.append(code)

        if not codes:
            return self._send_json({"error": "没有有效的车牌"}, HTTPStatus.BAD_REQUEST)
        if len(codes) > MAX_CODES_PER_JOB:
            return self._send_json(
                {"error": f"一次最多抓取 {MAX_CODES_PER_JOB} 个车牌"},
                HTTPStatus.BAD_REQUEST,
            )

        # Q4 决策：本地库已存在的车牌自动跳过（不入 magnets_links.txt）
        idx = self.app.library_index
        skipped: List[str] = []
        scrape_codes: List[str] = []
        for code in codes:
            if idx and idx.find_match(code):
                skipped.append(code)
            else:
                scrape_codes.append(code)

        if not scrape_codes:
            return self._send_json(
                {
                    "error": f"全部 {len(codes)} 个车牌本地已存在，无需抓取",
                    "skipped": skipped,
                },
                HTTPStatus.OK,
            )

        try:
            job = self.app.start_job(scrape_codes)
            job.skipped = skipped  # 记录本地库跳过的 codes
        except RuntimeError as e:
            return self._send_json({"error": str(e)}, HTTPStatus.CONFLICT)

        self._send_json(
            {
                "job_id": job.id,
                "total": len(scrape_codes),
                "skipped": skipped,
            }
        )

    # ---- 本地库端点 --------------------------------------------------- #
    def _serve_library_list(self, query: str) -> None:
        """GET /api/library?q=&page=&size=&sort="""
        qs = urllib.parse.parse_qs(query)
        q = (qs.get("q", [""])[0] or "").strip()
        q_upper = q.upper()
        q_lower = q.lower()
        try:
            page = max(1, int(qs.get("page", ["1"])[0]))
        except ValueError:
            page = 1
        try:
            size = int(qs.get("size", ["100"])[0])
        except ValueError:
            size = 100
        size = min(200, max(1, size))
        sort = qs.get("sort", ["carid"])[0]
        if sort not in ("carid", "mtime"):
            sort = "carid"

        app = self.app
        if app.library_root is None:
            return self._send_json(
                {"error": "未配置 LIBRARY_ROOT", "configured": False},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

        idx = app.library_index
        items = idx.all_sorted()
        if sort == "mtime":
            items = sorted(items, key=lambda e: e.modified, reverse=True)

        if q:
            def matches(e: Any) -> bool:
                if q_upper in e.carid:
                    return True
                if q_lower in (e.title or "").lower():
                    return True
                return any(q_lower in a.lower() for a in e.actors)

            items = [e for e in items if matches(e)]

        total = len(items)
        start = (page - 1) * size
        page_items = items[start : start + size]

        self._send_json(
            {
                "configured": True,
                "root": str(app.library_root),
                "scanned_at": app.library_scanned_at,
                "total": total,
                "page": page,
                "size": size,
                "q": q,
                "sort": sort,
                "movies": [
                    {
                        "carid": e.carid,
                        "folder": e.folder,
                        "title": e.title,
                        "actors": e.actors,
                        "release_date": e.release_date,
                        "has_nfo": e.has_nfo,
                        "has_poster": e.has_poster,
                        "has_fanart": e.has_fanart,
                        "has_video": e.has_video,
                        "video_count": e.video_count,
                        "total_size_bytes": e.total_size_bytes,
                        "modified": e.modified,
                        # videos[] 不在列表里，避免大 payload
                    }
                    for e in page_items
                ],
            }
        )

    def _serve_library_detail(self, carid: str) -> None:
        """GET /api/library/{carid}（含 videos[]）"""
        if not re.fullmatch(r"[A-Z0-9_-]{2,32}", carid.strip().upper()):
            return self._send_json({"error": "非法的车牌"}, HTTPStatus.BAD_REQUEST)
        entry = self.app.library_index.get(carid)
        if entry is None:
            return self._send_json({"error": "未找到该车牌"}, HTTPStatus.NOT_FOUND)
        self._send_json(entry.to_dict())

    def _serve_library_status(self) -> None:
        """GET /api/library/status（轮询扫描进度）"""
        app = self.app
        s = app.scan_state
        self._send_json(
            {
                "configured": app.library_root is not None,
                "root": str(app.library_root) if app.library_root else None,
                "movies_count": len(app.library_index),
                "scanned_at": app.library_scanned_at,
                "is_running": s.is_running,
                "is_complete": s.is_complete,
                "scanned": s.scanned,
                "total_estimate": s.total_estimate,
                "current_folder": s.current_folder,
                "error": s.error,
            }
        )

    def _serve_library_warnings(self) -> None:
        """GET /api/library/warnings（重复车牌 / 无 NFO 汇总）"""
        stats = self.app.library_stats or {}
        self._send_json(
            {
                "duplicate_carids": stats.get("duplicate_carids", []),
                "folders_without_nfo": stats.get("folders_without_nfo", []),
                "folders_no_carid": stats.get("folders_no_carid", []),
                "errors": stats.get("errors", []),
            }
        )

    def _trigger_rescan(self) -> None:
        """POST /api/library/rescan（409 if 已在跑）"""
        if self.app.library_root is None:
            return self._send_json(
                {"error": "未配置 LIBRARY_ROOT"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        if self.app.start_rescan():
            return self._send_json({"ok": True})
        return self._send_json(
            {"error": "扫描已在进行中"}, HTTPStatus.CONFLICT
        )

    def _serve_local_cover(self, query: str) -> None:
        """GET /api/local-cover?folder=...&name=poster.jpg

        未指定 name 时按 poster.* / folder.* / cover.* 顺序自动挑选。
        """
        from library_scanner import COVER_NAMES, FANART_NAMES

        qs = urllib.parse.parse_qs(query)
        folder = qs.get("folder", [""])[0]
        name = (qs.get("name", [""])[0] or "").lower()

        if not folder:
            return self._send_json({"error": "缺少 folder"}, HTTPStatus.BAD_REQUEST)

        folder_path = Path(folder)
        if not self.app.is_within_library(folder_path):
            return self._send_json({"error": "路径越界"}, HTTPStatus.FORBIDDEN)

        cover_path: Optional[Path] = None
        if name:
            if name not in (COVER_NAMES | FANART_NAMES):
                return self._send_json(
                    {"error": "非允许的文件名"}, HTTPStatus.FORBIDDEN
                )
            p = folder_path / name
            if p.is_file():
                cover_path = p
        else:
            # 自动挑选：poster > folder > cover
            for n in COVER_NAMES:
                p = folder_path / n
                if p.is_file():
                    cover_path = p
                    break

        if cover_path is None:
            return self._send_json(
                {"error": "封面不存在"}, HTTPStatus.NOT_FOUND
            )

        suffix = cover_path.suffix.lower()
        content_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
        }.get(suffix, "application/octet-stream")

        try:
            body = cover_path.read_bytes()
        except OSError as e:
            return self._send_json(
                {"error": f"读取失败：{e}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _open_folder(self) -> None:
        """POST /api/open-folder（用资源管理器打开本地目录）"""
        try:
            payload = self._read_json()
        except json.JSONDecodeError:
            return self._send_json(
                {"error": "请求体不是合法 JSON"}, HTTPStatus.BAD_REQUEST
            )

        folder = (payload or {}).get("folder", "")
        if not isinstance(folder, str) or not folder:
            return self._send_json({"error": "缺少 folder"}, HTTPStatus.BAD_REQUEST)

        folder_path = Path(folder)
        if not self.app.is_within_library(folder_path):
            return self._send_json({"error": "路径越界"}, HTTPStatus.FORBIDDEN)
        if not folder_path.exists() or not folder_path.is_dir():
            return self._send_json(
                {"error": "文件夹不存在"}, HTTPStatus.NOT_FOUND
            )

        try:
            self.app.open_in_explorer(folder_path)
        except Exception as e:  # noqa: BLE001
            return self._send_json(
                {"error": f"打开失败：{e}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        self._send_json({"ok": True})


class GalleryServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="影片画廊本地服务器：卡片浏览 + 勾选抓取磁力链接"
    )
    parser.add_argument(
        "--data",
        default=str(ROOT / "output" / "javlibrary_movies.json"),
        help="影片数据文件（JSON 或 CSV，默认 output/javlibrary_movies.json）",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "output"),
        help="结果输出目录（默认 output/）",
    )
    parser.add_argument(
        "--library-root",
        default=os.getenv("LIBRARY_ROOT", "").strip() or None,
        help="本地影片库根目录（默认从 .env 的 LIBRARY_ROOT 读取，未配置则禁用本地库功能）",
    )
    parser.add_argument(
        "--library-index",
        default=str(ROOT / "output" / "library_index.json"),
        help="本地库索引路径（默认 output/library_index.json）",
    )
    parser.add_argument(
        "--no-rescan-on-startup",
        action="store_true",
        help="启动时不自动扫描本地库（仅当索引缺失或 root 不一致时才会扫描）",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="监听地址（默认 0.0.0.0，允许局域网访问）",
    )
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument(
        "--image-proxy",
        choices=["auto", "on", "off"],
        default="auto",
        help="封面是否经服务端代理拉取（auto：配置了代理时启用）",
    )
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    args = parse_args(argv)

    # 本地库根目录处理
    library_root: Optional[Path] = None
    if args.library_root:
        library_root = Path(args.library_root).resolve()
        if not library_root.exists():
            logger.error(f"LIBRARY_ROOT 不存在：{library_root}")
            return 1
        if not library_root.is_dir():
            logger.error(f"LIBRARY_ROOT 不是目录：{library_root}")
            return 1

    try:
        app = GalleryApp(
            data_path=Path(args.data).resolve(),
            output_dir=Path(args.output_dir).resolve(),
            image_proxy_mode=args.image_proxy,
            library_root=library_root,
            library_index_path=Path(args.library_index).resolve(),
        )
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"加载数据失败：{e}")
        logger.error("请先运行 uv run javlibrary_scrapling.py 生成 output/javlibrary_movies.json")
        return 1

    server = GalleryServer((args.host, args.port), GalleryHandler)
    server.app = app  # type: ignore[attr-defined]

    local_url = f"http://127.0.0.1:{args.port}"
    display_url = (
        f"http://{local_ip_address()}:{args.port}"
        if args.host == "0.0.0.0"
        else f"http://{args.host}:{args.port}"
    )
    logger.info(f"影片画廊已启动：{local_url}")
    if args.host == "0.0.0.0":
        logger.info(f"局域网访问地址：{display_url}")
    logger.info(
        f"共 {len(app.movies)} 部影片，磁力抓取代理：{'开启' if app.proxy else '关闭'}，"
        f"封面代理：{'开启' if app.image_proxy else '关闭'}"
    )
    if library_root:
        if len(app.library_index) > 0:
            logger.info(
                f"本地库已就绪：{len(app.library_index)} 部（{library_root}），"
                f"上次扫描 {app.library_scanned_at}"
            )
        else:
            logger.info(f"本地库已配置但索引为空：{library_root}，可在页面上点击「刷新库」")
        # 启动时若 root 不一致则强制重建
        if (
            not args.no_rescan_on_startup
            and len(app.library_index) == 0
            and library_root.exists()
        ):
            logger.info("启动时触发首次后台扫描…")
            app.start_rescan()
    logger.info("按 Ctrl+C 停止服务")

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(local_url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("正在停止服务…")
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
