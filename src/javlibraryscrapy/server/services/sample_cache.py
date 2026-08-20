"""本地 sample_*.jpg 数量缓存。

``MOSTWANTED_LIBRARY_ROOT`` 通常在 NFS 上（``\\\\192.168.0.47\\团队文件-我的地盘\\``），
``folder.glob("sample_*.jpg")`` 一次要几百毫秒～几秒；60 部 wanted 列表
串行扫一遍可能耗时几十秒**。这里把 ``code -> (count, folder_mtime)``
缓存在内存里，配合：

- ``wanted_refresh._save_per_movie_folder`` 落盘时显式回写（避免下次扫描）
- ``mtime`` 校验：外部手动增删样本导致 mtime 变化 → 重新扫描
- 线程安全：``RLock`` + ``ThreadPoolExecutor`` 并发 glob
- 失效策略：folder 不存在 / 解析失败 → 缓存为 ``(0, 0.0)`` 避免反复重试

设计取舍：
- 不写磁盘：重启清空。重启后第一次请求会重新扫 NFS，但数据量小（几个
  ms ～ 几 s）且只一次；想持久化得引入 pickle + NFS 写，竞争更大。
- 不用 LRU：wanted 集 40～几百个 code，全内存足够
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger("gallery.sample_cache")

__all__ = ["SampleCountCache", "get_sample_cache"]


def _find_movie_folder(mw_root: Path, carid: str) -> Optional[Path]:
    """``mw_root`` 下第一个 ``<CARID> <title>/`` 文件夹（大小写不敏感）。

    复制自 ``routes/wanted.py``：那边是路由层 helper，这里 service 层
    不想反向依赖 routes（会形成循环），复制 10 行更干净。
    """
    if not mw_root.exists() or not mw_root.is_dir():
        return None
    prefix = carid.upper() + " "
    try:
        for entry in mw_root.iterdir():
            if entry.is_dir() and entry.name.upper().startswith(prefix):
                return entry
    except OSError as e:
        logger.warning(f"无法枚举 {mw_root}: {e}")
    return None

# 缓存项：(count, folder_mtime)
_CacheEntry = Tuple[int, float]


class SampleCountCache:
    """``code -> (sample_count, folder_mtime)`` 内存缓存。"""

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

    # ---- 配置 ----
    def set_root(self, mw_root: Optional[Path]) -> None:
        """切换根目录并清空缓存（旧 root 的 entry 已无意义）。"""
        with self._lock:
            self._root = Path(mw_root) if mw_root else None
            self._cache.clear()

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
    def put(self, code: str, count: int) -> None:
        """外部落盘后显式写入（避免下次 glob）。"""
        code = (code or "").upper()
        if not self._root:
            return
        mtime = self._folder_mtime(code)
        with self._lock:
            self._cache[code] = (count, mtime)

    def invalidate(self, code: str) -> None:
        code = (code or "").upper()
        with self._lock:
            self._cache.pop(code, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    # ---- 内部 ----
    def _validate(self, code: str, entry: _CacheEntry) -> int:
        """mtime 校验：NFS ``stat()`` 也慢（200ms 量级），60 条串行 = 12s+。
        只对 ``count > 0`` 的 entry 做校验 —— count=0 意味着本地无 folder
        （含「不存在」与「folder 存在但无 sample」），NFS stat 一次也只是
        命中或 OSError，不会比现在的 cache 慢多少。

        真正的失效点：
        - ``_save_per_movie_folder`` 写完 → ``put()`` 自动刷新 count + mtime
        - 用户手动删样本 → mtime 变 → 下次 _validate 检测到失效重扫
        """
        count, _cached_mtime = entry
        if count == 0:
            return 0
        cur_mtime = self._folder_mtime(code)
        if cur_mtime != entry[1]:
            with self._lock:
                self._cache.pop(code, None)
            return self._scan_and_store(code)
        return count

    def _scan_and_store(self, code: str) -> int:
        if not self._root:
            return 0
        folder = _find_movie_folder(self._root, code)
        if not folder:
            with self._lock:
                self._cache[code] = (0, 0.0)
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
        return n

    def _folder_mtime(self, code: str) -> float:
        if not self._root:
            return 0.0
        folder = _find_movie_folder(self._root, code)
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