"""极空间（zspace）NAS 集成：把 wanted 抓到的磁力提交到 NAS 下载。

包内封装 zspace_skill/nas/（vendored as :mod:`.zspace_nas`），复用其
RSA 登录 + cookie + token 续期逻辑。本模块只做两件事：

1. 从 :class:`~javlibraryscrapy.server.services.zspace_config.ZSpaceConfig`
   读配置（不是 .env）—— 用户通过网页 UI 改完立即生效。
2. 暴露 ``submit_magnet`` / ``list_downloads`` / ``get_download_codes`` 高层方法。

注意
----
- 配置变更检测：每次调用前 hash 一下 ``(host, user, password, device_id)``，
  变了就 aclose 旧 NasClient + 重 build 新实例（vendored 客户端从 module-level
  env 读配置，重 build 是唯一干净的切换方式）。
- ``/downloader/share/add`` 的 body schema 在 zspace_skill 仓库里被标注"待测"，
  本模块按推断（``url`` / ``downloadDir`` / ``type=magnet``）提交；NAS 真返回
  错误时把原始响应透传出去，方便上层定位字段名问题。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import httpx

from .zspace_config import ZSpaceConfig

logger = logging.getLogger("gallery.zspace")


# 任务名里抽 car id 的正则（独立维护避免循环依赖）。
# 兼容多种 car ID 格式：
#   ABF-340 / IPZZ-907 / SNOS-334     → 普通格式
#   T28-001                            → 字母+数字前缀
#   20ID-020                           → 数字+字母前缀
#   SNOS.334.1080p                     → 点分隔（容忍）
#
# 限定后缀 3~4 位数字：实测 wanted JSON 全部是 3 位数字。
# 收紧这一点避免误匹配（如 "madoubt.com 858935.xyz NGOD-352" 里
# "COM-85893" 假阳性 —— 5 位数字截断造成）。
_CARID_IN_NAME_RE = re.compile(
    r"([A-Z]{1,6}\d*|\d{2}[A-Z]+)[-_. ]?(\d{3,4})",
    re.IGNORECASE,
)

# NAS 任务状态字符串 / 数字码 → 我们的语义（兼容多种 NAS 厂家命名）。
# 极空间实测：status 是 int（0=active, 13=seeding/completed）+ 布尔 isFinished
_DOWNLOADING_STATES = frozenset({
    "downloading", "active", "preparing", "metadata",
    "queued", "waiting", "checking",
})
_COMPLETED_STATES = frozenset({
    "completed", "complete", "finished", "seeding", "done", "uploaded",
})
# 极空间实测的 int status 码：0=active，13=seeding/completed。其它值按
# isFinished bool / 进度兜底。
_DOWNLOADING_STATUS_CODES = frozenset({0, 1, 2, 3, 4, 5})  # active 类
_COMPLETED_STATUS_CODES = frozenset({11, 12, 13, 14, 15, 16, 17})  # seeding / completed 类

# 缓存有效期：NAS list API 单次 500ms~2s，wanted 页每次 load() 都拉太重。
# 30s 足够「在卡片上看到下载进度变化」，又不会把 NAS 摸死。
_DOWNLOAD_CODES_CACHE_TTL = 30.0


class ZSpaceError(RuntimeError):
    """调用 NAS 出错时抛出（包装 RuntimeError 让路由能区分）。"""


def _config_signature(cfg: ZSpaceConfig) -> tuple:
    """配置指纹：4 个会影响 NasClient 行为的字段。"""
    return (cfg.host, cfg.user, cfg.password, cfg.device_id)


class ZSpaceClient:
    """极空间 NAS 客户端（包装 vendored ``zspace_nas.NasClient``）。

    参数
    ----
    get_config : Callable[[], ZSpaceConfig]
        返回当前配置的 callable（每次访问 ``_ensure_client`` 时拉最新值）。
        通常传 ``app.state.zspace_config_store.get``。
    """

    def __init__(self, get_config: Callable[[], ZSpaceConfig]) -> None:
        self._get_config = get_config
        self._nas: Any = None
        self._sig: Optional[tuple] = None
        self._lock = asyncio.Lock()
        # 下载任务代码集缓存（30s），让 wanted 页频繁 load() 不会把 NAS 摸死
        self._codes_cache: Optional[Tuple[float, Set[str], Set[str], Dict[str, float]]] = None

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _ensure_env(self, cfg: ZSpaceConfig) -> None:
        """把 cfg 的字段写到 ``os.environ``，供 vendored nas 包读取。

        vendored ``nas/proto.py`` 在 import 时按 ``NAS_HOST`` 算 ``NAS_BASE``，
        所以必须先设 env 再 import。模块被 Python 缓存后 ``NAS_BASE`` 不变 -
        因此配置变更时必须 aclose 旧 client + 重 build（见 ``_ensure_client``）。

        vendored patch (2026-08-23)：version/device/_l 也设 env vars，让
        ``proto.common_query`` 输出 web UI 实际用的版本号和 PC 设备标识。
        """
        if cfg.host:
            os.environ["NAS_HOST"] = cfg.host
        if cfg.user:
            os.environ["NAS_USER"] = cfg.user
        if cfg.password:
            os.environ["NAS_PASSWORD"] = cfg.password
        if cfg.device_id:
            os.environ["NAS_DEVICE_ID"] = cfg.device_id
        # vendored patch：vendor 的 NAS_BASE 在 import 时算，但那时 host 还没设。
        # 这里补设 + reload proto 模块（如果已 import），让 NAS_BASE 用真实 host。
        if cfg.host:
            os.environ["NAS_BASE"] = f"http://{cfg.host}:5055"
            try:
                import importlib
                import sys
                from . import zspace_nas
                if "javlibraryscrapy.server.services.zspace_nas.proto" in sys.modules:
                    importlib.reload(sys.modules["javlibraryscrapy.server.services.zspace_nas.proto"])
                importlib.reload(zspace_nas)
            except Exception:
                pass
        # vendor 原值与真实 web UI 不一致；先设默认值，第一次 _ensure_client 时
        # 由 login 响应回填准确 version
        os.environ.setdefault("NAS_VERSION", "2.3.2025112601")
        os.environ.setdefault("NAS_DEVICE", "PC")
        os.environ.setdefault("NAS_LANG", "zh_cn")

    async def _ensure_client(self) -> Any:
        """懒加载 + 配置变更检测：cfg 变了就 aclose + 重建。"""
        cfg = self._get_config()
        sig = _config_signature(cfg)
        if self._nas is not None and sig == self._sig:
            return self._nas
        async with self._lock:
            # 二次检查：可能其他协程已经重建过了
            if self._nas is not None and sig == self._sig:
                return self._nas
            if self._nas is not None:
                # 配置变了 → 关闭旧 client（drop httpx pool + cookies）
                try:
                    await self._nas.aclose()
                except Exception:  # noqa: BLE001
                    pass
                self._nas = None
            self._ensure_env(cfg)
            # 必须在 _ensure_env() 之后 import（proto.py 在 import 时算 NAS_BASE）
            from .zspace_nas import NasClient
            self._nas = NasClient()
            # login 拿到 data.version 后回填 NAS_VERSION，让后续 common_query 用真实版本
            if not self._nas._logged_in:
                await self._nas.login()
                # data.version 是 int (NAS 注册时间戳)，env var 必须是 str
                ver = str(self._nas._cookies.get("version", "") or os.environ.get("NAS_VERSION", "2.3.2025112601"))
                os.environ["NAS_VERSION"] = ver
            self._sig = sig
        return self._nas

    # ------------------------------------------------------------------ #
    # 公开 API
    # ------------------------------------------------------------------ #
    async def submit_magnet(self, magnet_url: str, download_dir: str) -> Dict[str, Any]:
        """提交单个磁力到极空间下载器。

        返回 ``nas.post()`` 的原始 dict（NAS 业务码在 ``code`` 字段）。
        抛 :class:`ZSpaceError` 表示登录/网络层失败；业务码非 200 不抛，
        由调用方根据 ``code`` 判断。

        vendored patch (2026-08-23)：web UI 实际端点是 ``/downloader/add/link``，
        body 字段是 ``uri`` + ``dir``（不是 ``url``/``downloadDir``），
        公共参数（version/device_id/device/_l/token/nasid）必须**放在 body** 里
        而不是 query string——vendor 注释里猜的 ``/downloader/share/add`` + 字段名
        是错的，会被 NAS 业务层拒为 N202003。
        """
        nas = await self._ensure_client()
        # 触发 login（如果还没），登录后 _cookies 里有 token/nas_id/version
        cookies = dict(nas._cookies)
        token = cookies.get("token", "")
        nasid = cookies.get("nas_id", "")
        version = str(cookies.get("version", "") or os.environ.get("NAS_VERSION", "2.3.2025112601"))
        device_id = nas._device_id
        body = {
            "uri": magnet_url,
            "dir": download_dir,
            "plat": "web",
            "version": version,
            "device_id": device_id,
            "device": cookies.get("device", "PC"),
            "_l": cookies.get("_l", "zh_cn"),
            "token": token,
            "nasid": nasid,
        }
        base = os.environ.get('NAS_BASE') or 'http://' + os.environ.get('NAS_HOST', '') + ':5055'
        url = f"{base}/downloader/add/link"
        try:
            r = await nas._client.post(url, data=body, cookies=cookies)
            try:
                return r.json()
            except Exception:
                return {"_status": r.status_code, "_raw": r.text[:300]}
        except RuntimeError as e:
            raise ZSpaceError(str(e)) from e
        except (httpx.HTTPError, ValueError) as e:
            raise ZSpaceError(f"{type(e).__name__}: {e}") from e

    async def list_downloads(self) -> Dict[str, Any]:
        """列出当前 NAS 下载任务（POST ``/downloader/list`` body ``{}``）。

        vendored patch (2026-08-23)：list 端点也能跑通，但需要 cookies 里带
        ``nas_id``（=qc_name）。Vendored client 之前 cookies 没存这个字段。
        """
        nas = await self._ensure_client()
        try:
            return await nas.post("/downloader/list", {})
        except RuntimeError as e:
            raise ZSpaceError(str(e)) from e
        except (httpx.HTTPError, ValueError) as e:
            raise ZSpaceError(f"{type(e).__name__}: {e}") from e

    # ------------------------------------------------------------------ #
    # 下载代码集：wanted 页用，给每张卡片标 NAS 下载状态
    # ------------------------------------------------------------------ #
    async def get_download_codes(
        self, *, force_refresh: bool = False
    ) -> Tuple[Set[str], Set[str], Dict[str, float]]:
        """返回 ``(downloading_codes, completed_codes, downloading_progress)``。

        - 从 ``/downloader/list`` 拿原始任务列表
        - 用正则从 ``task.name`` 抽 car id（兼容大小写、与 - 分隔符无关）
        - 按 ``status`` 字符串 + ``progress`` 字段联合判定 downloading / completed
        - 30s 内存缓存；force_refresh=True  无视缓存（给用户手动刷新按钮用）
        - ``downloading_progress``：downloading 集合里每车的进度（0-100 浮点）。
          解析失败 → 进度 0（前端显示进度条但不显示文字）。
          completed 集合的车不放进来（completed 进度总是 100，前端不需要）。

        出错时（NAS 离线 / 登录失效 / list 失败）抛 :class:`ZSpaceError`，
        路由层映射成 502 + 空集兜底；前端拿到空集就不显示 NAS 徽章，跟
        「未配置 zspace」一致。
        """
        now = time.monotonic()
        if (
            not force_refresh
            and self._codes_cache is not None
            and (now - self._codes_cache[0]) < _DOWNLOAD_CODES_CACHE_TTL
        ):
            return self._codes_cache[1], self._codes_cache[2], self._codes_cache[3]

        raw = await self.list_downloads()
        downloading, completed, downloading_progress = _parse_download_codes(raw)
        self._codes_cache = (now, downloading, completed, downloading_progress)
        logger.debug(
            f"NAS 下载代码集刷新：downloading={len(downloading)}, "
            f"completed={len(completed)}"
        )
        return downloading, completed, downloading_progress

    def invalidate_download_codes_cache(self) -> None:
        """主动失效缓存。

        提交新磁力成功后调一下，让 wanted 页下次 load() 立刻看到新下载任务。
        """
        self._codes_cache = None


def _parse_download_codes(
    raw: Dict[str, Any],
) -> Tuple[Set[str], Set[str], Dict[str, float]]:
    """从 ``/downloader/list`` 的响应里抽 car id，按状态分两组 + 下载进度。

    返回 ``(downloading, completed, downloading_progress)``：
    - ``downloading``: 还在下的车（含 isFinished=false 但 progress 已 99% 的）
    - ``completed``: 已完成/做种的车
    - ``downloading_progress``: downloading 集合里每车 → 进度 0-100（completed 不进）

    兼容性处理（实测覆盖极空间 + 其它 qBittorrent 系）：
    - tasks 路径：``data.tasks`` / ``data.list`` / ``data.items`` / 顶层 ``list``
    - 任务名：``name`` / ``title`` / ``fileName``
    - 状态字段：``status``（int 或 str）/ ``state``（str）/ ``isFinished``（bool）
    - 进度：``progress`` / ``percent`` / ``completeSize``/``totalSize``

    判定优先级：isFinished bool > status 字符串 > status 数字码 > progress
    """
    # 尝试多个常见路径定位 task 列表
    candidates: List[Any] = []
    if isinstance(raw, dict):
        data = raw.get("data") or raw
        for key in ("tasks", "list", "items"):
            v = data.get(key) if isinstance(data, dict) else None
            if isinstance(v, list):
                candidates = v
                break
        if not candidates and isinstance(data, list):
            candidates = data

    downloading: Set[str] = set()
    completed: Set[str] = set()
    downloading_progress: Dict[str, float] = {}
    for task in candidates:
        if not isinstance(task, dict):
            continue
        # 抽 car id：name / title / fileName 任一字段里有就行
        name = ""
        for key in ("name", "title", "fileName", "filename"):
            v = task.get(key)
            if isinstance(v, str) and v.strip():
                name = v
                break
        carid = _extract_carid(name)
        if not carid:
            continue

        progress = _extract_progress(task)
        # 状态判定（按优先级）
        # 1) isFinished bool（极空间实测）—— 最直接可靠
        is_finished = task.get("isFinished")
        if isinstance(is_finished, bool):
            if is_finished:
                completed.add(carid)
            else:
                # isFinished=false → 还在下载；可能 progress=99.7% 但还没标记完成
                downloading.add(carid)
                downloading_progress[carid] = max(0.0, min(100.0, progress))
            continue

        # 2) status / state 字符串
        status_raw = None
        for key in ("status", "state"):
            v = task.get(key)
            if isinstance(v, str):
                status_raw = v
                break
        if status_raw is not None:
            status_lower = status_raw.strip().lower()
            if status_lower in _COMPLETED_STATES:
                completed.add(carid)
            elif status_lower in _DOWNLOADING_STATES:
                downloading.add(carid)
                downloading_progress[carid] = max(0.0, min(100.0, progress))
            # 其它状态字符串（未知）继续看数字码
            else:
                # 尝试把 status 当 int 解析
                try:
                    code = int(status_raw)
                    if code in _COMPLETED_STATUS_CODES:
                        completed.add(carid)
                    elif code in _DOWNLOADING_STATUS_CODES:
                        downloading.add(carid)
                        downloading_progress[carid] = max(0.0, min(100.0, progress))
                except (TypeError, ValueError):
                    pass
            continue

        # 3) status int 字段（极空间：0=active, 13=completed/seeding）
        for key in ("status", "state"):
            v = task.get(key)
            if isinstance(v, int):
                if v in _COMPLETED_STATUS_CODES:
                    completed.add(carid)
                elif v in _DOWNLOADING_STATUS_CODES:
                    downloading.add(carid)
                    downloading_progress[carid] = max(0.0, min(100.0, progress))
                # 未知码继续看进度
                else:
                    if progress >= 100:
                        completed.add(carid)
                    elif progress > 0:
                        downloading.add(carid)
                        downloading_progress[carid] = max(0.0, min(100.0, progress))
                break
        else:
            # 4) 纯进度兜底
            if progress >= 100:
                completed.add(carid)
            elif progress > 0:
                downloading.add(carid)
                downloading_progress[carid] = max(0.0, min(100.0, progress))

    return downloading, completed, downloading_progress


def _extract_carid(name: str) -> Optional[str]:
    """从任务名里抽 car id（统一大写）。无匹配返回 None。

    例：
        "ABF-340-C.torrent"              → "ABF-340"
        "[HD] IPZZ-907 [无码破解]"        → "IPZZ-907"
        "SNOS.334.1080p"                 → "SNOS-334"（容忍 . 分隔符）
        "madoubt.com 858935.xyz NGOD-352" → "NGOD-352"（取**最右**的候选——
                                          真正的 car id 习惯在文件名的末尾，
                                          前面往往是网站名 / 路径 / 标签）

    取最右匹配：真正 car id 在文件名末尾是行业惯例（命名规则 `<站名><path><CARID>`），
    而中间的网站 / 路径数字（"madoubt.com 858935"）经常包含看起来像 car id 的字串。
    """
    if not name:
        return None
    matches = list(_CARID_IN_NAME_RE.finditer(name.upper().replace(".", "-")))
    if not matches:
        return None
    # 取最右（文件名末位）的匹配 + 同位置时取最长
    best = max(matches, key=lambda m: (m.start(), len(m.group(1)) + len(m.group(2))))
    return f"{best.group(1).upper()}-{best.group(2)}"


def _extract_progress(task: Dict[str, Any]) -> float:
    """从 task 里抽进度（0~100 数字）。无进度字段返回 -1。

    支持：
    - ``progress`` / ``percent`` / ``pct`` —— 直接是百分比
    - ``completeSize``/``totalSize`` / ``size``/``totalSize`` —— 字节数算比
    """
    for key in ("progress", "percent", "pct"):
        v = task.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    # 兜底：completeSize / totalSize（极空间实测字段名）
    complete = task.get("completeSize")
    total = task.get("totalSize") or task.get("total_size")
    if isinstance(complete, (int, float)) and isinstance(total, (int, float)) and total > 0:
        return float(complete) / float(total) * 100.0
    # 兜底 2：size / totalSize
    size = task.get("size")
    total = task.get("totalSize") or task.get("total_size")
    if isinstance(size, (int, float)) and isinstance(total, (int, float)) and total > 0:
        return float(size) / float(total) * 100.0
    return -1.0

    async def aclose(self) -> None:
        """关闭 vendored httpx 客户端（lifespan 退出时调用）。"""
        if self._nas is not None:
            try:
                await self._nas.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._nas = None