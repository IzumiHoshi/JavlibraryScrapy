"""Pydantic 模型：保留原 ``gallery_server.py`` 的 JSON shape。

``model_config.extra = "allow"`` 用于响应：原服务有时会附加动态字段
（如 ``active_job``、``javbus_url`` 等），不能拒绝未知字段。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---- 共用基类：允许额外字段（保持与原服务 JSON shape 兼容） ----
class _CompatModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ---- 影片列表项 ----
class MovieItem(_CompatModel):
    code: str
    title: str
    id: str
    cover_url: str
    # 由 /api/movies 端点附加：
    cover: Optional[str] = None  # 经代理后的相对 URL 或原 URL
    javbus_url: Optional[str] = None
    local_exists: Optional[bool] = None
    library_folder: Optional[str] = None


class MoviesResponse(_CompatModel):
    movies: List[Dict[str, Any]]
    source: str
    output_dir: str
    active_job: Optional[str] = None
    library_configured: bool = False


# ---- 抓取任务 ----
class ScrapeStartRequest(_CompatModel):
    codes: List[str] = Field(..., description="要抓取的车牌列表")


class ScrapeStartResponse(_CompatModel):
    job_id: str
    total: int
    skipped: List[str] = Field(default_factory=list)


class ScrapeItem(_CompatModel):
    code: str
    status: Literal["pending", "running", "ok", "no_magnet", "failed", "local_skip"]
    title: str = ""
    magnet: Optional[str] = None
    release_date: Optional[str] = None
    actors: Optional[str] = None
    javbus_url: Optional[str] = None
    local_exists: Optional[bool] = None
    library_folder: Optional[str] = None


class ScrapeJobSnapshot(_CompatModel):
    id: str
    status: Literal["running", "done", "error"]
    error: Optional[str] = None
    started_at: str
    total: int
    finished: int
    current: Optional[str] = None
    succeeded: int
    items: List[ScrapeItem]
    logs: List[str]
    outputs: Dict[str, str] = Field(default_factory=dict)


# ---- 本地库 ----
class LibraryMovieSummary(_CompatModel):
    carid: str
    folder: str
    title: str
    actors: List[str] = Field(default_factory=list)
    release_date: str = ""
    has_nfo: bool = False
    has_poster: bool = False
    has_fanart: bool = False
    has_video: bool = False
    video_count: int = 0
    total_size_bytes: int = 0
    modified: str = ""


class LibraryListResponse(_CompatModel):
    configured: bool
    root: Optional[str] = None
    scanned_at: Optional[str] = None
    total: int
    page: int
    size: int
    q: str = ""
    month: str = ""
    actor: str = ""
    sort: str = "released"
    months: List[Dict[str, Any]] = Field(default_factory=list)
    movies: List[LibraryMovieSummary]


class LibraryMovieDetail(_CompatModel):
    """完整影片条目（含 videos[]）。"""

    carid: str
    folder: str
    title: str = ""
    actors: List[str] = Field(default_factory=list)
    release_date: str = ""
    has_nfo: bool = False
    has_poster: bool = False
    has_fanart: bool = False
    has_video: bool = False
    video_count: int = 0
    total_size_bytes: int = 0
    modified: str = ""
    videos: List[str] = Field(default_factory=list)


class LibraryStatusResponse(_CompatModel):
    configured: bool
    root: Optional[str] = None
    movies_count: int
    scanned_at: Optional[str] = None
    is_running: bool
    is_complete: bool = False
    scanned: int = 0
    total_estimate: int = 0
    current_folder: str = ""
    error: Optional[str] = None


class LibraryWarningsResponse(_CompatModel):
    duplicate_carids: List[str] = Field(default_factory=list)
    folders_without_nfo: List[str] = Field(default_factory=list)
    folders_no_carid: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# ---- 单部刷新队列 ----
class RescanJobSnapshot(_CompatModel):
    carid: str
    folder: str
    status: Literal["queued", "running", "done", "error"]
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    title: str = ""
    logs: List[str] = Field(default_factory=list)
    position: Optional[int] = None  # 仅 queued 项有


class RescanQueueStatus(_CompatModel):
    active: bool
    current: Optional[RescanJobSnapshot] = None
    queued: List[RescanJobSnapshot] = Field(default_factory=list)
    total: int


# ---- 杂项 ----
class OpenFolderRequest(_CompatModel):
    folder: str


class ErrorResponse(_CompatModel):
    error: str


# ---- Wanted（JAVLibrary Most Wanted）----
class WantedMovieSummary(_CompatModel):
    """Wanted 列表条目（卡片用，不含 videos[] 等大字段）。

    字段名沿用 JSON 里 ``_bucket`` / ``_status`` / ``_seen_at`` 等带下划线的私有命名
    （避免与前端关键字冲突），Pydantic 通过 ``alias`` 映射。
    """
    model_config = ConfigDict(populate_by_name=True)

    code: str
    title: str
    cover_url: str = ""
    id: str = ""
    release_date: str = ""
    actors: str = ""
    producer: str = ""
    publisher: str = ""
    category: str = ""
    bucket: str = Field(default="unknown", alias="_bucket")
    status: str = Field(default="pending", alias="_status")
    seen_at: str = Field(default="", alias="_seen_at")
    updated_at: str = Field(default="", alias="_updated_at")
    missing_in_remote: bool = False


class WantedMonthInfo(_CompatModel):
    """月份汇总（前端导航条用）。"""
    month: str           # "YYYY-MM" 或 "unknown"
    count: int


class WantedListResponse(_CompatModel):
    months: List[WantedMonthInfo]
    items: List[WantedMovieSummary]
    total: int
    page: int
    size: int
    month: str = ""      # 当前筛选的月份
    missing_in_remote_count: int = 0


class WantedRefreshStatus(_CompatModel):
    id: str
    status: str                    # running | done
    phase: str                     # fetch_wanted | merge | fetch_javbus | save | done | error
    error: Optional[str] = None
    started_at: str
    finished_at: Optional[str] = None
    wanted_total: int = 0
    wanted_pages_done: int = 0
    wanted_added: int = 0
    wanted_updated: int = 0
    wanted_marked_missing: int = 0
    javbus_total: int = 0
    javbus_done: int = 0
    javbus_failed: int = 0
    current_code: Optional[str] = None
    logs: List[str] = []


class WantedRefreshStartResponse(_CompatModel):
    job_id: str
    is_already_running: bool = False