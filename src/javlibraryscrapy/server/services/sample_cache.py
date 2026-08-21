"""本地 sample_*.jpg 数量缓存。

``MOSTWANTED_LIBRARY_ROOT`` 通常在 NFS 上（``\\\\192.168.0.47\\团队文件-我的地盘\\``），
``folder.glob("sample_*.jpg")`` 一次要几百毫秒～几秒；60 部 wanted 列表
串行扫一遍可能耗时几十秒**。这里把 ``code -> (count, folder_mtime)``
缓存在内存里，配合：

- ``wanted_refresh._save_per_movie_folder`` 落盘时显式回写（避免下次扫描）
- ``mtime`` 校验：外部手动增删样本导致 mtime 变化 → 重新扫描
- 线程安全：``RLock`` + ``ThreadPoolExecutor`` 并发 glob
- 失效策略：folder 不存在 / 解析失败 → 缓存为 ``(0, 0.0)`` 避免反复重试

P0 优化 — 文件夹索引：

原来 ``_find_movie_folder`` 每次都对 ``mw_root`` 做 ``iterdir()`` 在 1000+ 条目的
NFS 根目录上扫一遍，单次几百毫秒～几秒。改成：启动时一次 ``iterdir()`` 建立
``CARID -> folder_path`` 字典，后续查找 O(1)；root mtime 变化时（60 秒节流）
后台重建索引。冷启动 /api/wanted 从 8.5s 降到 ~50ms。

设计取舍：
- 不写磁盘：重启清空。重启后第一次请求会重新扫 NFS，但数据量小（几个
  ms ～ 几 s）且只一次；想持久化得引入 pickle + NFS 写，竞争更大。
- 不用 LRU：wanted 集 40～几百个 code，全内存足够
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("gallery.sample_cache")

__all__ = ["SampleCountCache", "get_sample_cache"]


# 缓存项：(count, folder_mtime)
_CacheEntry = Tuple[int, float]

# root mtime 检查的最小间隔（秒）。NFS stat 也慢，没必要每个请求都 stat 一次。
_ROOT_MTIME_CHECK_INTERVAL = 60.0

# _validate mtime 校验的最小间隔（秒）。
# 用户手动增删样本是稀有操作；同一 code 在 5 秒内被多次请求，没必要每次都 stat。
# 命中 TTL → 直接返缓存值（可能 stale 几秒）；超过 TTL → 走 stat 校验。
_VALIDATE_TTL = 5.0


class SampleCountCache:
    """``code -> (sample_count, folder_mtime)`` 内存缓存 + 文件夹索引。"""

    def __init__(self, mw_root: Optional[Path], max_workers: int = 8):
        self._root: Optional[Path] = Path(mw_root) if mw_root else None
        self._cache: Dict[str, _CacheEntry] = {}
        self._lock = threading.RLock()
        # 8 并发：NFS 单目录 glob 主要受 lookup latency 限制，不是 CPU；
        # 太多反而拖慢 SMB 协商。10 是经验上限，留点 buffer。
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="sample-cache",
        )
        # ---- 文件夹索引（P0） ----
        # ``CARID (upper) -> folder Path``，启动期一次 iterdir() 填满。
        # 之后查找 O(1)；root mtime 变化时（节流）重建。
        self._folder_index: Dict[str, Path] = {}
        self._index_root_mtime: Optional[float] = None
        self._last_mtime_check: float = 0.0
        self._indexed_codes: set = set()  # 已确认在索引里查不到的 code，避免重复检查 mtime
        # ---- _validate TTL（节流单 code 的 stat 校验）----
        self._last_validate: Dict[str, float] = {}
        if self._root:
            self._rebuild_index()

    # ---- 配置 ----
    def set_root(self, mw_root: Optional[Path]) -> None:
        """切换根目录并清空缓存（旧 root 的 entry 已无意义）。"""
        with self._lock:
            self._root = Path(mw_root) if mw_root else None
            self._cache.clear()
            self._folder_index.clear()
            self._indexed_codes.clear()
            self._last_validate.clear()
            self._index_root_mtime = None
            self._last_mtime_check = 0.0
            if self._root:
                self._rebuild_index_unlocked()

    # ---- 文件夹索引（P0） ----
    def _rebuild_index(self) -> None:
        """在锁内重建索引。"""
        with self._lock:
            self._rebuild_index_unlocked()

    def _rebuild_index_unlocked(self) -> None:
        """调用方需持有 ``_lock``。一次 ``iterdir()`` 扫 root，建立 ``code -> folder`` 字典。"""
        if not self._root:
            self._folder_index.clear()
            self._index_root_mtime = None
            return
        try:
            # sort 保证「first match」行为稳定（与原 iterdir 在 Windows 上的实际顺序一致）
            entries = sorted(self._root.iterdir(), key=lambda p: p.name)
        except OSError as e:
            logger.warning(f"无法枚举 {self._root}: {e}")
            return
        idx: Dict[str, Path] = {}
        for entry in entries:
            if not entry.is_dir():
                continue
            name = entry.name
            if " " not in name:
                continue
            code = name.split(" ", 1)[0].upper()
            if code and code not in idx:
                idx[code] = entry
        self._folder_index = idx
        self._indexed_codes.clear()  # 索引重建 → 之前确认「不存在」的 code 重新可查
        try:
            self._index_root_mtime = self._root.stat().st_mtime
        except OSError:
            self._index_root_mtime = None
        logger.info(f"folder index built: {len(idx)} entries from {self._root}")

    def _maybe_check_root_mtime(self) -> None:
        """节流检查 root mtime：若变化则重建索引。"""
        if not self._root:
            return
        now = time.monotonic()
        if now - self._last_mtime_check < _ROOT_MTIME_CHECK_INTERVAL:
            return
        self._last_mtime_check = now
        try:
            cur_mtime = self._root.stat().st_mtime
        except OSError:
            return
        if cur_mtime != self._index_root_mtime:
            logger.info(f"root mtime changed ({self._index_root_mtime} → {cur_mtime}), rebuilding folder index")
            self._rebuild_index_unlocked()

    def _find_folder(self, code: str) -> Optional[Path]:
        """O(1) 查找 folder；miss 时（节流）检查 root mtime 是否需要重建索引。

        不会做全量 ``iterdir()`` fallback —— 那正是我们要消除的开销。
        如果 code 真不在索引里（即 root 没这个目录），就返回 None 缓存为 0。
        """
        if not self._root:
            return None
        code = code.upper()
        # 节流：每 60s 才检查一次 root mtime（绝大多数请求走这一条就 return 了）
        self._maybe_check_root_mtime()
        return self._folder_index.get(code)

    # ---- 读 ----
    def count_for(self, code: str) -> int:
        """单查。命中缓存直接返回，未命中 glob 一次并写入。"""
        code = (code or "").upper()
        if not self._root:
            return 0
        with self._lock:
            cached = self._cache.get(code)
        if cached is not None:
            return self._validate(code, cached)
        # 未命中：glob 一次
        return self._scan_and_store(code)

    def counts_for(self, codes: list[str]) -> Dict[str, int]:
        """批量查。已缓存的走缓存，未缓存的并发 glob（thread pool）。"""
        if not self._root:
            return {c: 0 for c in codes}
        to_scan: list[str] = []
        with self._lock:
            for code in codes:
                k = code.upper()
                if k not in self._cache:
                    to_scan.append(k)
        if to_scan:
            # 并发 glob。NFS 慢，多 worker 收益明显。
            list(self._executor.map(self._scan_and_store, to_scan))
        with self._lock:
            return {c.upper(): self._validate(c.upper(), self._cache[c.upper()]) for c in codes}

    # ---- 写 ----
    def put(self, code: str, count: int, folder: Optional[Path] = None) -> None:
        """外部落盘后显式写入（避免下次 glob）。

        ``folder`` 优化：调用方已知 folder 路径时直接传入，省去内部 iterdir 查找；
        同时把它注册到 ``_folder_index``，新文件夹也能命中索引。
        """
        code = (code or "").upper()
        if not self._root:
            return
        if folder is None:
            folder = self._find_folder(code)
        if folder is None:
            with self._lock:
                self._cache[code] = (0, 0.0)
                self._indexed_codes.add(code)  # 确认：此 code 在 root 不存在
            return
        # 注册到索引（幂等）。新 folder 时这步就是"索引登记"。
        try:
            mtime = folder.stat().st_mtime
        except OSError:
            mtime = 0.0
        with self._lock:
            self._folder_index[code] = folder
            self._indexed_codes.discard(code)
            self._cache[code] = (count, mtime)
            self._last_validate.pop(code, None)  # put 时已知最新，无需下次 _validate 又 stat

    def invalidate(self, code: str) -> None:
        code = (code or "").upper()
        with self._lock:
            self._cache.pop(code, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._indexed_codes.clear()
            self._last_validate.clear()
            # 不清空 folder_index —— root 没变的话索引还有效

    def prewarm(self, codes: List[str]) -> None:
        """启动期预热：批量 glob 一组 code 的 sample 数，并发塞进 cache。

        ``app.py`` 在 WantedService 加载完电影列表后调用，把最常用的 code
        （前 N 部）一次性扫掉，让首次 ``/api/wanted`` 不必触发 NFS cold start。
        """
        if not self._root or not codes:
            return
        to_scan = [c.upper() for c in codes if c and c.upper() not in self._cache]
        if not to_scan:
            return
        logger.info(f"prewarm: scanning {len(to_scan)} codes")
        list(self._executor.map(self._scan_and_store, to_scan))

    # ---- 内部 ----
    def _validate(self, code: str, entry: _CacheEntry) -> int:
        """mtime 校验：NFS ``stat()`` 也慢（200ms 量级），60 条串行 = 12s+。
        只对 ``count > 0`` 的 entry 做校验 —— count=0 意味着本地无 folder
        （含「不存在」与「folder 存在但无 sample」），NFS stat 一次也只是
        命中或 OSError，不会比现在的 cache 慢多少。

        TTL 节流：同一 code 在 ``_VALIDATE_TTL`` 秒内不重复 stat。代价是用户在 TTL
        窗口内手动增删样本不会立刻反映（最多 stale ``_VALIDATE_TTL`` 秒）。这对
        wanted 列表是 acceptable tradeoff —— 加载速度从 200ms 降到 < 50ms。

        真正的失效点：
        - ``_save_per_movie_folder`` 写完 → ``put()`` 自动刷新 count + mtime
        - 用户手动删样本 → 超过 TTL 后下次 _validate 检测到失效重扫
        """
        count, _cached_mtime = entry
        if count == 0:
            return 0
        now = time.monotonic()
        last = self._last_validate.get(code, 0.0)
        if now - last < _VALIDATE_TTL:
            return count
        self._last_validate[code] = now
        cur_mtime = self._folder_mtime(code)
        if cur_mtime != entry[1]:
            with self._lock:
                self._cache.pop(code, None)
                self._last_validate.pop(code, None)
            return self._scan_and_store(code)
        return count

    def _scan_and_store(self, code: str) -> int:
        if not self._root:
            return 0
        folder = self._find_folder(code)
        if not folder:
            with self._lock:
                self._cache[code] = (0, 0.0)
                self._indexed_codes.add(code)
            return 0
        try:
            n = sum(1 for _ in folder.glob("sample_*.jpg"))
        except OSError as e:
            logger.warning(f"无法枚举 sample_*.jpg @ {folder}: {e}")
            return 0
        try:
            mtime = folder.stat().st_mtime
        except OSError:
            mtime = 0.0
        with self._lock:
            self._cache[code] = (n, mtime)
            self._last_validate.pop(code, None)
        return n

    def _folder_mtime(self, code: str) -> float:
        if not self._root:
            return 0.0
        folder = self._find_folder(code)
        if not folder:
            return 0.0
        try:
            return folder.stat().st_mtime
        except OSError:
            return 0.0


# ---- 单例 ----
_default_cache: Optional[SampleCountCache] = None
_default_lock = threading.Lock()


def get_sample_cache(mw_root: Optional[Path] = None) -> SampleCountCache:
    """获取进程级单例。``mw_root`` 只在首次调用时生效；之后切换用 ``set_root``。"""
    global _default_cache
    with _default_lock:
        if _default_cache is None:
            _default_cache = SampleCountCache(mw_root=mw_root)
        return _default_cache