"""服务状态：合并原 ``GalleryApp`` 中的状态与单部刷新队列管理。

提供：
- ``load_movies`` — 从 JSON/CSV 加载影片列表
- ``normalize_path_for_compare`` — 用于比较本地库 root 是否一致
- ``GalleryState`` — 全局服务状态（movies / 当前任务 / 本地库索引 / 封面代理配置）
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from javlibraryscrapy.library.scanner import (
    LibraryIndex,
    ScanProgress,
    load_index,
    save_index,
    scan_library,
    scan_movie_folder,
)

from .jobs import ScrapeJob

logger = logging.getLogger("gallery.library")

# 车牌正则（与原服务一致）：用于 URL 拼接安全
# - CARID_RE：宽松版，只做"非空 + 安全字符"校验（用于 URL 拼接、JSON 已有条目读取）
# - STRICT_CARID_RE：严格版，要求标准的 JAV 格式 "字母-数字"（如 IPZZ-907）
CARID_RE = re.compile(r"[A-Z0-9_-]{2,32}")
STRICT_CARID_RE = re.compile(r"^[A-Z]+[-_][0-9]+$")
MAX_CODES_PER_JOB = 300


def normalize_carid(code: str) -> Optional[str]:
    """把用户输入的车牌规范化成 ``字母[-_]数字`` 标准格式。

    处理：
      - ``ipzz907`` / ``IPZZ907`` （无分隔符）→ ``IPZZ-907``
      - ``IPZZ_907`` → ``IPZZ-907``（下下划统一为减号）
      - ``ipzz-907`` → ``IPZZ-907``（大写化）

    拆分规则：找到字母与数字的边界，在边界处插入 ``-``。
    这覆盖了绝大多数 JAV 车牌（``[A-Z]+-[0-9]+``），
    对非 JAV 风格的输入（纯字母、纯数字、带奇怪后缀）返回 None。

    返回 None 时调用方应回 400 拒绝。
    """
    if not code:
        return None
    s = code.strip().upper()
    if not s:
        return None
    # 已有分隔符（- 或 _）：把 _ 统一成 -，然后走严格校验
    if "-" in s or "_" in s:
        s = s.replace("_", "-")
        return s if STRICT_CARID_RE.match(s) else None
    # 无分隔符：在字母/数字边界插入 -（找第一个数字出现的位置）
    m = re.search(r"\d", s)
    if not m:
        return None  # 纯字母
    i = m.start()
    if i == 0:
        return None  # 纯数字
    if i == len(s):
        return None  # 不可能，但防御一下
    candidate = s[:i] + "-" + s[i:]
    return candidate if STRICT_CARID_RE.match(candidate) else None


def normalize_path_for_compare(p: str) -> str:
    """规范化路径用于等价比较，处理 Windows 上的常见差异：

    - 大小写不敏感
    - 正反斜杠统一
    - 展开映射盘为 UNC
    - 去掉尾部分隔符
    """
    if not p:
        return ""
    try:
        return os.path.normcase(os.path.normpath(os.path.realpath(p))).rstrip("\\/")
    except OSError:
        return os.path.normcase(os.path.normpath(p)).replace("/", "\\").rstrip("\\/")


def load_movies(data_path: Path) -> List[Dict[str, str]]:
    """从 JSON 或 CSV 加载影片列表，按 code 去重（保留首次出现的顺序）。"""
    if not data_path.exists():
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
# 全局状态
# --------------------------------------------------------------------------- #
class GalleryState:
    """服务器共享状态：影片数据、当前任务、封面代理配置、本地库索引。

    ``output_dir`` 仅承载临时数据（日志、cover cache、scratch），所有持久化文件
    （javlibrary_movies.json、library_index.json、magnets.json）都通过 Settings
    显式路径传入，不要再塞进 ``output_dir``。zspace 配置走 .env，不落盘。
    """

    def __init__(
        self,
        data_path: Path,
        output_dir: Path,
        image_proxy_mode: str,
        proxy: Optional[str],
        proxy_javbus_enabled: bool,
        user_agent: str,
        verify_ssl: bool,
        download_timeout: int,
        javbus_url: str,
        library_root: Optional[Path] = None,
        library_index_path: Optional[Path] = None,
        magnets_index: Optional[Path] = None,
    ):
        self.data_path = data_path
        self.output_dir = output_dir
        self.movies = load_movies(data_path)

        # 代理：javbus 详情抓取 + 磁力抓取始终使用 PROXY；
        # 封面 auto 模式额外受 ``proxy_javbus_enabled`` 控制（开关 + URL 都得有值）。
        # JAVLibrary 镜像抓取走独立的 proxy_javlibrary_enabled，不影响这里。
        self.proxy = proxy
        self.cover_proxy = proxy if proxy_javbus_enabled else None
        # auto：proxy_javbus_enabled=true 且配了 PROXY 才走服务端代理拉图
        if image_proxy_mode == "auto":
            self.image_proxy = bool(self.cover_proxy)
        else:
            self.image_proxy = image_proxy_mode == "on"
            if self.image_proxy:
                self.cover_proxy = proxy
        self.user_agent = user_agent
        self.verify_ssl = verify_ssl
        self.download_timeout = download_timeout
        # javbus_url：规范化为"带尾斜杠"形式，便于拼接 ``<url><code>``。
        # settings 的 validator 只去尾斜杠，这里补回；调用方无需关心是否带 ``/``。
        self.javbus_url = javbus_url.rstrip("/") + "/"

        self.cover_cache_dir = output_dir / ".cover_cache"
        self.job: Optional[ScrapeJob] = None
        self._lock = threading.Lock()
        # 磁力抓取结果路径（默认 fallback 到 output_dir/magnets.json）
        self.magnets_index: Path = (
            Path(magnets_index) if magnets_index is not None
            else output_dir / "magnets.json"
        )

        # ---- 本地影片库 ----
        self.library_root: Optional[Path] = library_root
        self.library_index_path: Path = library_index_path or (
            output_dir / "library_index.json"
        )
        self.library_index: LibraryIndex = LibraryIndex.empty()
        self.library_stats: Dict[str, Any] = {}
        self.library_scanned_at: Optional[str] = None
        self.scan_state: ScanProgress = ScanProgress()
        self._scan_lock = threading.Lock()
        # 注：旧的单部刷新队列（RescanQueue）已被 backfill 取代。
        # backfill 由 library_backfill.LibraryBackfillService 提供。
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
        stored_root = data.get("root") or ""
        current_root = str(self.library_root)
        if stored_root and normalize_path_for_compare(stored_root) != normalize_path_for_compare(
            current_root
        ):
            logger.warning(
                f"索引 root 与当前配置不一致：\n"
                f"  索引里：{stored_root}\n"
                f"  当前：  {current_root}\n"
                f"标记为待重建。点击页面「刷新库」可强制重建。"
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
        """触发后台扫描。返回 True 表示已启动，False 表示已在运行。"""
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
            logger.info(f"开始后台扫描 {self.library_root} …")
            movies, stats = scan_library(self.library_root, progress=self.scan_state)
            canonical_root = Path(normalize_path_for_compare(str(self.library_root)))
            save_index(movies, stats, self.library_index_path, canonical_root)
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

    # ---- 单部刷新已被 backfill 取代（见 library_backfill.py） ----
    # 旧版的 ``enqueue_rescan_movie`` / ``get_rescan_status`` /
    # ``_refresh_index_after_rescan`` 已删除；前端卡片按钮改走
    # ``POST /api/library/{carid}/backfill``。

    def update_library_index_for_folder(self, folder: Path) -> None:
        """立即把单个影片目录 upsert 到 in-memory library_index。

        用于：整理功能（POST /api/wanted/{code}/organize）完成后，
        不等 scanner 跑完（scanner 全全 5 分钟），先把刚整理的目录加进去，
        让前端的「本地已有」徽章立刻亮起。
        scanner 之后还会跑一次全库扫，做最终一致性兜底。
        """
        try:
            entry = scan_movie_folder(folder)
            if entry is None:
                logger.warning(f"刷新后重扫失败：{folder}")
                return
            self.library_index.upsert(entry)
            logger.info(f"索引已更新：{entry.carid}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"刷新后更新索引异常：{e}")

    def is_within_library(self, path: Path) -> bool:
        if not self.library_root:
            return False
        try:
            target = path.resolve()
            root = self.library_root.resolve()
            return str(target).startswith(str(root))
        except OSError:
            return False

    # ---- 任务 ---------------------------------------------------------- #
    def start_job(self, codes: List[str], run_target) -> ScrapeJob:
        """``run_target`` 通常是 ``start_scrape_job``（注入以避免循环依赖）。"""
        with self._lock:
            if self.job is not None and self.job.status == "running":
                raise RuntimeError("已有抓取任务正在运行，请等待完成")
            job = ScrapeJob(_gen_job_id(), codes)
            self.job = job
        run_target(job)
        return job

    def get_job(self, job_id: str) -> Optional[ScrapeJob]:
        job = self.job
        return job if job is not None and job.id == job_id else None


def _gen_job_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]