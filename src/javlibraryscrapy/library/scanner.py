"""
本地影片库扫描与索引。

扫描 Z:\\JAV（或配置指定的根目录），从每个含视频文件的目录中提取车牌与
NFO 元数据，构建可前缀匹配的索引；落盘到 output/library_index.json。

CLI 用法：
    uv run python -m javlibraryscrapy.cli.gallery  # 画廊服务（入口见 cli/gallery.py）
    uv run python -m javlibraryscrapy.library.scanner [--root Z:\\JAV] [--index output/library_index.json] [-v]

Import 用法：
    from javlibraryscrapy.library.scanner import (
        scan_library, save_index, load_index, LibraryIndex, MovieEntry, ScanStats,
    )
    movies, stats = scan_library(Path("Z:\\JAV"))
    save_index(movies, stats, Path("output/library_index.json"), Path("Z:\\JAV"))
    data = load_index(Path("output/library_index.json"))
    idx = LibraryIndex.from_dict(data)
    entry = idx.find_match("ABF-340")   # 双向前缀匹配
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any, Dict, List, Optional, Tuple

# 项目根目录：见 javlibraryscrapy/_paths.py
from javlibraryscrapy._paths import REPO_ROOT as ROOT  # noqa: E402

# 复用现有的车牌提取（注意：find_car_bus 返回的是单个车牌字符串，不是 list）
from javlibraryscrapy.utils.car import find_car_bus  # noqa: E402

logger = logging.getLogger("library_scanner")

# ---- 常量 ----
# Schema 版本历史：
# - v1：初版（has_nfo/poster/fanart/video + 视频大小等元数据）
# - v2：MovieEntry 加 ``sample_count``（sample_*.jpg 数量）—— 让 backfill 预估
#       能精确区分"缺 sample"与"完整"两种目录，避免把 sample_count=0 误判为
#       complete。schema bump 后旧 JSON 会被丢弃（load_index 返 None），
#       GalleryState 启动时自动触发首次全量重扫。
INDEX_SCHEMA_VERSION = 2

# 视频文件白名单（Q13 决策）
VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".avi", ".wmv", ".ts", ".iso", ".m2ts", ".flv",
})

# Kodi / Plex 约定的海报/封面文件名
COVER_NAMES = frozenset({
    "poster.jpg", "poster.png", "poster.jpeg",
    "folder.jpg", "folder.png",
    "cover.jpg", "cover.png", "cover.jpeg",
})
FANART_NAMES = frozenset({"fanart.jpg", "fanart.png", "fanart.jpeg"})


# ---- 数据类 ----
@dataclass
class MovieEntry:
    """单部影片的索引记录。"""
    carid: str
    folder: str
    title: str = ""
    actors: List[str] = field(default_factory=list)
    release_date: str = ""
    has_nfo: bool = False
    has_poster: bool = False
    has_fanart: bool = False
    has_video: bool = False
    video_count: int = 0
    total_size_bytes: int = 0
    modified: str = ""  # ISO 8601
    videos: List[str] = field(default_factory=list)  # 视频文件名清单（绝对路径）
    sample_count: int = 0  # sample_*.jpg 数量（backfill 预估用，schema v2+）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MovieEntry":
        # 忽略未知字段，保持前向兼容
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in valid})


@dataclass
class ScanStats:
    """扫描统计信息。"""
    total_folders_scanned: int = 0
    movies_indexed: int = 0
    duplicate_carids: List[str] = field(default_factory=list)  # 被舍弃的重复路径
    folders_without_nfo: List[str] = field(default_factory=list)
    folders_no_carid: List[str] = field(default_factory=list)  # 文件夹名无法解析为车牌
    folders_no_video: List[str] = field(default_factory=list)  # 影片目录但没视频文件（organize 复制元数据后常见）
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class ScanProgress:
    """实时扫描进度（线程间共享；扫描线程写、API 线程读）。"""
    scanned: int = 0
    total_estimate: int = 0
    current_folder: str = ""
    is_running: bool = False
    is_complete: bool = False
    error: Optional[str] = None


# ---- 工具函数 ----
# 优先尝试的子版本车牌正则（保留 -C / -U / -2 等后缀）：
# - find_car_bus 永远只返回主版本（ABF-340-C → ABF-340），但本地库需要保留子版本
# - 本地库的子版本仍是合法电影，应独立索引；同时双向前缀匹配仍能命中
_SUBVERSION_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+(?:-[A-Z0-9]+)+")


def _parse_carid(folder_name: str) -> Optional[str]:
    """从文件夹名提取车牌（大写）。无车牌返回 None。

    优先用 _SUBVERSION_RE 保留子版本后缀；否则回退到 find_car_bus。
    """
    upper = folder_name.upper()
    m = _SUBVERSION_RE.match(upper)
    if m:
        return m.group(0)
    carid = find_car_bus(upper, [])
    return carid.upper() if carid else None


def _strip_bom(s: str) -> str:
    """去掉 UTF-8 BOM。"""
    return s[1:] if s.startswith("\ufeff") else s


def _parse_nfo(nfo_path: Path) -> Tuple[str, List[str], str]:
    """
    读 NFO 文件，返回 (title, actors, release_date)。

    仅尝试 UTF-8（Q15 决策）。失败时返回 ("", [], "")，调用方走文件夹名兜底。
    """
    try:
        content = nfo_path.read_text(encoding="utf-8", errors="strict")
        content = _strip_bom(content)
    except UnicodeDecodeError:
        logger.debug(f"NFO 不是 UTF-8 编码：{nfo_path}")
        return "", [], ""
    except OSError as e:
        logger.debug(f"读 NFO 失败 {nfo_path}: {e}")
        return "", [], ""

    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        logger.warning(f"NFO 解析失败 {nfo_path}: {e}")
        return "", [], ""

    title = (root.findtext("title") or "").strip()
    release = (root.findtext("releasedate") or root.findtext("release") or "").strip()
    actors = [(a.findtext("name") or "").strip() for a in root.findall("actor")]
    actors = [a for a in actors if a]
    return title, actors, release


def scan_movie_folder(folder: Path) -> Optional[MovieEntry]:
    """
    扫描单个影片目录，返回 MovieEntry 或 None（文件夹名无法解析为车牌）。

    只在已经被外层 walk() 判定为"含视频文件"的目录上调用。

    用于刷新单个影片后增量更新索引。
    """
    carid = _parse_carid(folder.name)
    if not carid:
        return None

    entry = MovieEntry(carid=carid, folder=str(folder))

    # NFO
    nfo_path = folder / "movie.nfo"
    nfo_alt = folder / f"{carid}.nfo"
    chosen_nfo: Optional[Path] = None
    if nfo_path.exists():
        chosen_nfo = nfo_path
    elif nfo_alt.exists():
        chosen_nfo = nfo_alt

    if chosen_nfo:
        entry.has_nfo = True
        title, actors, release = _parse_nfo(chosen_nfo)
        entry.title = title
        entry.actors = actors
        entry.release_date = release

    # 单次 iterdir 同时识别视频 / poster / fanart / sample
    video_files: List[Path] = []
    sample_files: List[Path] = []
    latest_mtime: float = 0.0
    try:
        for p in folder.iterdir():
            if not p.is_file():
                continue
            lname = p.name.lower()
            if p.suffix.lower() in VIDEO_EXTENSIONS:
                video_files.append(p)
            if lname in COVER_NAMES:
                entry.has_poster = True
            elif lname in FANART_NAMES:
                entry.has_fanart = True
            # sample_NNN.jpg（与 library.backfill 的 _SAMPLE_RE 保持一致）
            if lname.startswith("sample_") and lname.endswith(".jpg"):
                sample_files.append(p)
    except OSError as e:
        logger.debug(f"无法列目录 {folder}: {e}")

    entry.sample_count = len(sample_files)

    if video_files:
        entry.has_video = True
        entry.video_count = len(video_files)
        try:
            entry.total_size_bytes = sum(p.stat().st_size for p in video_files)
        except OSError:
            entry.total_size_bytes = 0
        entry.videos = [str(p) for p in video_files]

    # modified 时间（NFO 与视频的最新 mtime）
    candidates: List[Path] = []
    if chosen_nfo:
        candidates.append(chosen_nfo)
    candidates.extend(video_files)
    try:
        for p in candidates:
            mt = p.stat().st_mtime
            if mt > latest_mtime:
                latest_mtime = mt
        if latest_mtime > 0:
            entry.modified = datetime.fromtimestamp(latest_mtime).isoformat()
    except OSError:
        pass

    return entry


# ---- 扫描 ----
def scan_library(
    root: Path,
    progress: Optional[ScanProgress] = None,
    cancel_event: Optional[Event] = None,
) -> Tuple[Dict[str, MovieEntry], ScanStats]:
    """
    递归扫描 root，返回 (movies_dict, stats)。

    策略（Q13 方案 a）：遇到任一含视频文件的目录即视为影片，**停止深入**该目录。
    """
    if progress:
        progress.is_running = True
        progress.error = None
        progress.scanned = 0
        progress.total_estimate = 0
        progress.is_complete = False

    stats = ScanStats()
    start = time.time()

    # Phase 1: 收集所有"含视频的目录"
    movie_dirs: List[Path] = []
    seen_dirs = 0

    # 用 os.walk 替代 Path.iterdir 递归：在 UNC / SMB 路径上 os.walk 利用 OS 层
    # 批量遍历，能比 Python 递归 iterdir 省 ~30% 时间（实测 140s → 100s 量级）。
    # 通过 ``dirs[:] = ...`` 就地修改实现"遇到影片目录停止深入"。
    #
    # 判定规则（与旧递归版一致）：
    #   - 含视频文件 → 影片目录（最常见）
    #   - 含 NFO + cover → 影片目录（organize 完还没搬视频的状态）
    #   - 含 cover + sample_*.jpg → 历史 wanted 整理目录
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            if cancel_event and cancel_event.is_set():
                break
            seen_dirs += 1

            # 跳过隐藏子目录（就地下次迭代时不深入）
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            has_video_here = any(
                f.lower().endswith(tuple(VIDEO_EXTENSIONS)) for f in filenames
            )
            has_nfo = "movie.nfo" in filenames
            has_cover = any(
                f in {"cover.jpg", "cover.png", "poster.jpg", "poster.png"}
                for f in filenames
            )
            has_samples = any(f.startswith("sample_") for f in filenames)

            if has_video_here or (has_nfo and has_cover) or (has_cover and has_samples):
                movie_dirs.append(Path(dirpath))
                # 不再深入该目录
                dirnames[:] = []
    except OSError as e:  # noqa: BLE001 - 任意 OS 错误兜底上报
        stats.errors.append(f"扫描根目录失败 {root}: {e}")

    if progress:
        progress.total_estimate = len(movie_dirs)

    # Phase 2: 解析每个影片目录，处理重复
    by_carid: Dict[str, MovieEntry] = {}

    for folder in movie_dirs:
        if cancel_event and cancel_event.is_set():
            break

        if progress:
            progress.scanned += 1
            progress.current_folder = str(folder)

        entry = scan_movie_folder(folder)
        if entry is None:
            stats.folders_no_carid.append(str(folder))
            continue

        # 走到这里说明 walk() 判定为影片目录（has_video 或 has_nfo+has_cover）
        # 没视频但有元数据 → 标记为「缺视频」便于 organize 后续补
        if not entry.has_video:
            stats.folders_no_video.append(str(folder))

        if not entry.has_nfo:
            stats.folders_without_nfo.append(str(folder))

        # 重复车牌处理（Q18）：保留 size 最大的
        if entry.carid in by_carid:
            existing = by_carid[entry.carid]
            if entry.total_size_bytes > existing.total_size_bytes:
                stats.duplicate_carids.append(existing.folder)
                by_carid[entry.carid] = entry
            else:
                stats.duplicate_carids.append(entry.folder)
        else:
            by_carid[entry.carid] = entry

    stats.total_folders_scanned = seen_dirs
    stats.movies_indexed = len(by_carid)
    stats.duration_seconds = time.time() - start

    if progress:
        progress.is_running = False
        progress.is_complete = True
        progress.current_folder = ""

    return by_carid, stats


# ---- 索引读写 ----
def save_index(
    movies: Dict[str, MovieEntry],
    stats: ScanStats,
    index_path: Path,
    root: Path,
    scanned_at: Optional[str] = None,
) -> None:
    """原子写入索引到磁盘（先写 .tmp 再 rename）。"""
    payload = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "scanned_at": scanned_at or datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "scan_duration_seconds": round(stats.duration_seconds, 2),
        "stats": {
            "total_folders_scanned": stats.total_folders_scanned,
            "movies_indexed": stats.movies_indexed,
            "duplicate_carids": stats.duplicate_carids,
            "folders_without_nfo": stats.folders_without_nfo,
            "folders_no_carid": stats.folders_no_carid,
            "errors": stats.errors,
        },
        "movies": {c: e.to_dict() for c, e in sorted(movies.items())},
    }

    index_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = index_path.with_suffix(index_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(index_path)
    logger.info(
        f"索引已写入 {index_path}（{len(movies)} 部，"
        f"耗时 {stats.duration_seconds:.1f}s）"
    )


def load_index(index_path: Path) -> Optional[Dict[str, Any]]:
    """
    加载索引。文件不存在 / 损坏 / 版本不匹配返回 None（调用方应触发重建）。
    """
    if not index_path.exists():
        return None
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"索引文件损坏 {index_path}: {e}")
        return None
    if not isinstance(data, dict):
        logger.warning(f"索引格式错误 {index_path}: 顶层不是 dict")
        return None
    if data.get("schema_version") != INDEX_SCHEMA_VERSION:
        logger.warning(
            f"索引版本不匹配 {index_path}（期望 {INDEX_SCHEMA_VERSION}，"
            f"实际 {data.get('schema_version')}），将重建"
        )
        return None
    return data


# ---- 索引包装 ----
class LibraryIndex:
    """包装 movies 字典，提供前缀匹配与列表查询。"""

    def __init__(self, movies: Dict[str, MovieEntry]):
        self._movies = movies

    @classmethod
    def empty(cls) -> "LibraryIndex":
        return cls({})

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LibraryIndex":
        raw = data.get("movies") or {}
        movies: Dict[str, MovieEntry] = {}
        for carid, entry_data in raw.items():
            try:
                movies[carid.upper()] = MovieEntry.from_dict(entry_data)
            except (TypeError, ValueError) as e:
                logger.warning(f"跳过无效索引条目 {carid}: {e}")
        return cls(movies)

    def __len__(self) -> int:
        return len(self._movies)

    def __contains__(self, carid: str) -> bool:
        return carid.upper() in self._movies

    def __iter__(self):
        """迭代 car id（dict-like），跟 ``keys()`` 等价。"""
        return iter(self._movies)

    def keys(self):
        """所有 car id 的快照（dict-like）。调用方不要修改。"""
        return self._movies.keys()

    def values(self):
        """所有 MovieEntry 的快照（dict-like）。调用方不要修改。"""
        return self._movies.values()

    def items(self):
        """所有 ``(carid, MovieEntry)`` 对的快照（dict-like）。调用方不要修改。"""
        return self._movies.items()

    def get(self, carid: str) -> Optional[MovieEntry]:
        return self._movies.get(carid.upper())

    def all_sorted(self) -> List[MovieEntry]:
        """按车牌字典序返回所有记录。"""
        return [self._movies[k] for k in sorted(self._movies)]

    def upsert(self, entry: MovieEntry) -> None:
        """插入或更新单个条目（用于单个影片刷新后增量更新索引）。"""
        self._movies[entry.carid.upper()] = entry

    def find_match(self, target_code: str) -> Optional[MovieEntry]:
        """
        双向前缀匹配（Q3 决策）：target 与 local 任一是另一方前缀即命中。

        优先精确匹配；其次按"更长的前缀"（更具体）优先。
        """
        if not target_code:
            return None
        t = target_code.strip().upper()
        if not t:
            return None
        if t in self._movies:
            return self._movies[t]

        best: Optional[MovieEntry] = None
        best_len = -1
        for local_code, entry in self._movies.items():
            if t.startswith(local_code) or local_code.startswith(t):
                if len(local_code) > best_len:
                    best = entry
                    best_len = len(local_code)
        return best


# ---- CLI ----
def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="扫描本地影片目录，生成 library_index.json",
    )
    p.add_argument(
        "--root",
        default="Z:\\JAV",
        help="影片根目录（默认 Z:\\JAV）",
    )
    p.add_argument(
        "--index",
        default=str(ROOT / "output" / "library_index.json"),
        help="索引输出路径（默认 output/library_index.json）",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示 DEBUG 级别日志",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    root = Path(args.root)
    index_path = Path(args.index).resolve()

    if not root.exists():
        logger.error(f"根目录不存在：{root}")
        return 1
    if not root.is_dir():
        logger.error(f"根路径不是目录：{root}")
        return 1

    logger.info(f"开始扫描 {root} …")
    movies, stats = scan_library(root)
    save_index(movies, stats, index_path, root)

    logger.info(
        f"扫描完成：扫 {stats.total_folders_scanned} 个目录，"
        f"索引 {stats.movies_indexed} 部，"
        f"重复 {len(stats.duplicate_carids)} 部，"
        f"无 NFO {len(stats.folders_without_nfo)} 部，"
        f"无法识别车牌 {len(stats.folders_no_carid)} 部，"
        f"错误 {len(stats.errors)} 个"
    )
    if stats.errors:
        for e in stats.errors[:5]:
            logger.warning(f"  · {e}")
        if len(stats.errors) > 5:
            logger.warning(f"  · ...还有 {len(stats.errors) - 5} 个错误")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())