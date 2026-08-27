"""
整理单部已下载的 wanted 影片：从 wanted 目录复制到本地库，移动 NAS 下载的视频文件。

典型工作流：
1. 用户在 /wanted 看到某个车牌带「✅ 已下载」徽章（NAS 任务完成）
2. 点「整理」按钮 → POST /api/wanted/{code}/organize
3. 后端：
   a. 从 wanted 目录（``<MOSTWANTED_LIBRARY_ROOT>/<CARID> <title>/``）拿 title / release_date
   b. 把整个 wanted 文件夹复制到 ``<LIBRARY_ROOT>/<YYYY-MM>/<CARID> <title>/``
      （含 movie.nfo / poster.jpg / fanart.jpg / sample_NNN.jpg）
   c. 在 ``<ZSPACE_DOWNLOAD_PATH>`` 下找含车牌的下载项（文件或文件夹）
      - 文件：直接 rename 移到目标目录
      - 文件夹：把里面的视频移到目标，然后删掉源文件夹
   d. 触发 library scanner 把新目录加入索引
4. 前端刷新单卡片 → 状态变 `📁 已整理`（紫色徽章），右上角"本地已有"亮起

设计要点：
- 同车牌多份下载：选**最大**的视频文件（避免下载到不完整的 partial 文件）
- 目标目录已存在：跳过（不覆盖用户数据），返回 ok=False + reason=already_organized
- 视频命名：``<CARID> <title>.<ext>``（与 MovieExporter / library_scanner 一致）
- 文件夹检测：候选是 dir 且含视频文件 → 当作"下载的是文件夹"处理
"""
from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from javlibraryscrapy.library.scanner import (
    COVER_NAMES,
    FANART_NAMES,
    VIDEO_EXTENSIONS,
)
from javlibraryscrapy.utils.filesave import rename, write_xml

logger = logging.getLogger("library_organizer")


_VIDEO_EXTS = VIDEO_EXTENSIONS

# 在 NAS 目录里匹配车牌的辅助正则（实际匹配走 _name_matches 子串）
_CARID_RE = re.compile(r"([A-Z]{1,6}\d*|\d{2}[A-Z]+)[-_. ]?(\d{3,4})", re.IGNORECASE)


class OrganizeError(RuntimeError):
    """整理过程中可恢复的错误（前端 toast 提示，不堆栈）。"""


def find_wanted_folder(mw_root: Path, code: str) -> Optional[Path]:
    """在 mw_root 下找第一个 ``<CARID> <title>/`` 文件夹（大小写不敏感）。

    与 ``services.wanted._find_movie_folder`` 的策略一致 —— 仅取第一个匹配。
    """
    if not mw_root.exists() or not mw_root.is_dir():
        return None
    code_u = code.upper()
    prefix = code_u + " "
    try:
        for entry in mw_root.iterdir():
            if entry.is_dir() and entry.name.upper().startswith(prefix):
                return entry
    except OSError as e:
        logger.warning(f"无法枚举 {mw_root}: {e}")
    return None


def parse_nfo(nfo_path: Path) -> Tuple[str, str]:
    """读 movie.nfo 拿 title + release_date。失败时返回 ("", "")。

    NFO 是 Kodi / Plex 标准的 XML，根节点是 ``<movie>``，title / premiered 在子节点。
    用简单的正则避免依赖 ElementTree（ElementTree 对未知字段太敏感）。
    """
    if not nfo_path.exists():
        return "", ""
    try:
        text = nfo_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning(f"读 NFO 失败 {nfo_path}: {e}")
        return "", ""
    title = ""
    m = re.search(r"<title>([^<]+)</title>", text)
    if m:
        title = m.group(1).strip()
    release_date = ""
    m = re.search(r"<premiered>([^<]+)</premiered>", text)
    if not m:
        m = re.search(r"<releasedate>([^<]+)</releasedate>", text)
    if m:
        release_date = m.group(1).strip()
    return title, release_date


def month_bucket(release_date: str, fallback_now: bool = True) -> str:
    """``2026-08-15`` → ``"2026-08"``。空 release_date 时用当前月份（fallback）。"""
    if release_date:
        m = re.match(r"(\d{4})[-/](\d{2})", release_date)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    if fallback_now:
        return datetime.now().strftime("%Y-%m")
    return "unknown"


def find_nas_download(
    nas_root: Path,
    code: str,
) -> Tuple[Optional[Path], Optional[Path]]:
    """在 NAS 下载目录里找含车牌的下载项。

    返回 ``(item_path, parent_path)``：
    - ``item_path`` 是匹配的目录或文件路径
    - ``parent_path`` 是它所在的父目录（用于日志）

    选择策略：
    - 文件和目录都参与匹配（不区分）
    - 同车牌多份候选 → 选**最大**的视频文件（避免下载到 partial / metadata-only 文件）
    - 大小相同时按路径名字典序（稳定）
    """
    if not nas_root.exists() or not nas_root.is_dir():
        return None, None

    code_u = code.upper()
    candidates: List[Tuple[Path, int]] = []  # (path, size_bytes)
    try:
        for entry in nas_root.iterdir():
            if not _name_matches(entry.name, code_u):
                continue
            size = _entry_size(entry)
            candidates.append((entry, size))
    except OSError as e:
        logger.warning(f"无法枚举 NAS 目录 {nas_root}: {e}")
        return None, None

    if not candidates:
        return None, None

    # 选最大；同大小按名字典序
    candidates.sort(key=lambda p_s: (-p_s[1], p_s[0].name.lower()))
    best, _ = candidates[0]
    return best, nas_root


def _name_matches(name: str, code_u: str) -> bool:
    """任务名 / 文件名 是否含车牌（前缀匹配，容错 ./-/_ 分隔符）。"""
    upper = name.upper().replace(".", "-")
    return code_u in upper.split() or code_u in upper.replace(" ", "-")


def _entry_size(entry: Path) -> int:
    """获取文件 / 目录的"体积"（目录递归求和）。"""
    if entry.is_file():
        try:
            return entry.stat().st_size
        except OSError:
            return 0
    if entry.is_dir():
        total = 0
        try:
            for p in entry.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total
    return 0


def _collect_videos(src: Path) -> List[Path]:
    """从 ``src``（文件或目录）里收集视频文件列表。"""
    if src.is_file() and src.suffix.lower() in _VIDEO_EXTS:
        return [src]
    if src.is_dir():
        out: List[Path] = []
        for p in src.iterdir():
            if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
                out.append(p)
        return out
    return []


def _copy_wanted_files(src_dir: Path, dst_dir: Path) -> Dict[str, int]:
    """把 wanted 目录里的元数据文件复制到 dst_dir。

    复制：movie.nfo / poster.{jpg,png} / fanart.{jpg,png} / sample_NNN.jpg
    不复制：视频文件（视频来自 NAS，不在 wanted 目录里）

    返回 ``{copied: N, skipped: M, errors: K}`` 供前端 toast 展示。
    """
    out = {"copied": 0, "skipped": 0, "errors": 0}
    dst_dir.mkdir(parents=True, exist_ok=True)

    wanted_patterns: List[str] = []
    wanted_patterns.extend(COVER_NAMES)      # poster / folder / cover
    wanted_patterns.extend(FANART_NAMES)     # fanart
    wanted_patterns.append("movie.nfo")      # nfo
    wanted_patterns.append("*.nfo")         # <carid>.nfo 这种兜底
    wanted_patterns.extend(["sample_*.jpg", "sample_*.png"])  # samples

    seen: set[str] = set()  # dedupe（movie.nfo 同时匹配 "movie.nfo" 和 "*.nfo"）
    for pattern in wanted_patterns:
        for src_file in src_dir.glob(pattern):
            if not src_file.is_file() or src_file.name in seen:
                continue
            seen.add(src_file.name)
            dst_file = dst_dir / src_file.name
            if dst_file.exists():
                # 已存在（之前整理过）→ 跳过，不覆盖
                out["skipped"] += 1
                continue
            try:
                shutil.copy2(src_file, dst_file)
                out["copied"] += 1
            except OSError as e:
                logger.warning(f"复制 {src_file.name} -> {dst_file} 失败：{e}")
                out["errors"] += 1
    return out


def _move_videos(
    videos: List[Path],
    code: str,
    title: str,
    dst_dir: Path,
) -> Dict[str, Any]:
    """把视频文件移到 ``dst_dir`` 并重命名为 ``<CARID> <title>.<ext>``。

    同名文件已存在 → 跳过（保留旧的，可能是用户的其他版本）。
    返回：
      {
        "moved":     [{"src": "...", "dst": "..."}, ...],
        "skipped":   [{"src": "...", "reason": "exists"}, ...],
        "errors":    [{"src": "...", "error": "..."}, ...],
      }
    """
    out: Dict[str, Any] = {"moved": [], "skipped": [], "errors": []}
    dst_dir.mkdir(parents=True, exist_ok=True)
    for src in videos:
        ext = src.suffix.lower()
        # 文件名清洗：去掉非法字符（Windows 不允许 <>:"/\|?*）
        safe_title = re.sub(r'[<>:"/\\|?*]', "_", title).strip()
        dst_name = f"{code} {safe_title}{ext}"
        dst = dst_dir / dst_name
        try:
            if dst.exists():
                out["skipped"].append({"src": str(src), "reason": "exists"})
                continue
            # 跨盘移动（NAS -> 本地盘）：先 copy 再 unlink，避免 rename 跨盘失败
            try:
                rename(src, dst)  # 同盘用 rename（快）
            except OSError:
                shutil.copy2(src, dst)
                try:
                    src.unlink()
                except OSError:
                    pass  # copy 成功，源文件删不掉也不致命
            out["moved"].append({"src": str(src), "dst": str(dst)})
        except OSError as e:
            out["errors"].append({"src": str(src), "error": str(e)})
    return out


def _cleanup_empty_dir(path: Path) -> bool:
    """如果目录空（或仅含隐藏文件），删掉。返回是否成功删除。"""
    if not path.exists() or not path.is_dir():
        return False
    try:
        # 是否有非隐藏文件
        for p in path.iterdir():
            if not p.name.startswith("."):
                return False
        # 全是隐藏文件 -> 删整个目录
        shutil.rmtree(path)
        return True
    except OSError as e:
        logger.warning(f"清理空目录 {path} 失败：{e}")
        return False


def ensure_nfo(
    wanted_dir: Path,
    code: str,
    javbus_url: str,
    javbus_proxy: Optional[str] = None,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """确保 wanted 目录里有 ``movie.nfo``，没有就实时从 JavBus 抓一份。

    历史 wanted refresh（MovieExporter 迁移 #10 之前）经常漏写 movie.nfo，
    导致后续 organize 时目标目录也缺 NFO（Plex/Kodi 读不到 metadata）。

    实测「先抓 NFO → 再 organize」的模式：
    - 已有 NFO → 立即返回 (True, None) 零开销
    - 没 NFO → 跑一次 JavBus 详情页（带 timeout），只取 info dict 不下载封面/sample
    - 写回 wanted 目录 ``movie.nfo``（也顺手补，让后续 refresh 不必重抓）
    - 抓失败 → 返回 (False, error_str)，让上层决定怎么处理

    返回 ``(nfo_ok, info_or_error)``：
    - ``nfo_ok=True`` + ``info_or_error=None`` → 已有 NFO，无需抓
    - ``nfo_ok=True`` + ``info_or_error=dict`` → 没 NFO 但刚补上了，dict 是 JavBus info
    - ``nfo_ok=False`` + ``info_or_error=str`` → 抓失败，错误信息
    """
    nfo_path = wanted_dir / "movie.nfo"
    if nfo_path.exists():
        return True, None

    logger.info(f"[{code}] wanted 目录缺 movie.nfo，从 JavBus 实时补一份")

    try:
        from javlibraryscrapy.scraping.javbus import JavbusSpider
    except ImportError as e:
        return False, f"导入 JavbusSpider 失败：{e}"

    # JavbusSpider.parse() 内部会顺手调 download_cover 写到 root_dir/<carid>.png（cover temp）。
    # 我们只需要 NFO，不想污染 wanted_dir → 临时把 root_dir 指向一个 tempdir，
    # 抓完后清理掉 cover temp。
    import asyncio
    import concurrent.futures
    import shutil as _shutil
    import tempfile
    from scrapling.fetchers import AsyncDynamicSession

    with tempfile.TemporaryDirectory(prefix="javlibraryscrapy_ensure_nfo_") as tmp_root:
        spider = JavbusSpider(root_dir=Path(tmp_root))
        spider.proxy_enabled = javbus_proxy is not None
        spider.proxy = javbus_proxy

    async def _fetch() -> Optional[Dict[str, Any]]:
        async with AsyncDynamicSession(
            load_dom=spider.load_dom,
            network_idle=spider.network_idle,
            disable_resources=spider.disable_resources,
            proxy=spider.proxy,
            headless=spider.headless,
            timeout=spider.timeout,
        ) as session:
            page = await session.fetch(f"{javbus_url.rstrip('/')}/{code}")
            info = await spider.parse(page)
            return info if isinstance(info, dict) else None

    try:
        # 在独立线程跑 asyncio.run（uvicorn 事件循环下不能直接 asyncio.run）
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(lambda: asyncio.run(_fetch()))
            info = future.result(timeout=60)
    except concurrent.futures.TimeoutError:
        return False, "JavBus 抓取超时（>60s）"
    except Exception as e:  # noqa: BLE001
        return False, f"JavBus 抓取失败：{e}"

    if not info or not info.get("title") or not info.get("carid"):
        return False, "JavBus 抓取结果缺标题/车牌"

    # 写 NFO 到 wanted 目录（不写其它文件 —— 调用方自己处理 cover/sample）
    try:
        wanted_dir.mkdir(parents=True, exist_ok=True)
        write_xml(nfo_path, info)
        logger.info(f"[{code}] 已补写 movie.nfo 到 {wanted_dir}")
    except OSError as e:
        return False, f"写 NFO 失败：{e}"

    return True, info


def organize_movie(
    code: str,
    mw_root: Path,
    lib_root: Path,
    nas_download_path: Path,
    *,
    on_library_change: Optional[Callable[[], None]] = None,
    dry_run: bool = False,
    javbus_url: Optional[str] = None,
    javbus_proxy: Optional[str] = None,
) -> Dict[str, Any]:
    """整理单部已下载 wanted 影片。

    参数
    ----
    code : 车牌（大小写不敏感）
    mw_root : wanted 库根目录（``MOSTWANTED_LIBRARY_ROOT``）
    lib_root : 本地影片库根目录（``LIBRARY_ROOT``）
    nas_download_path : NAS 下载目录（``ZSPACE_DOWNLOAD_PATH`` 的本地挂载路径）
    on_library_change : 整理成功后回调（通常是触发 library scanner）
    dry_run : True 时只读不写，返回每一步「将要做什么」但不实际执行
             （copy / move / rmtree / mkdir 全部跳过）；返回 dict 额外带
             ``dry_run: True`` + ``plan: [str, ...]`` 列出所有计划动作。

    返回 dict（直接 jsonify）：
        {
            "ok": bool,
            "code": str,
            "skipped": "already_organized" | None,
            "wanted_folder": str | None,
            "dest_folder": str | None,
            "month": "2026-08",
            "files_copied": int,        # dry_run 时 = 预计复制数
            "files_skipped": int,
            "files_errored": int,
            "videos_moved": int,        # dry_run 时 = 预计移动数
            "videos_skipped": int,
            "nas_source": str | None,
            "nas_source_removed": bool, # dry_run 时固定 False
            "error": str | None,
            "warning": str | None,
            "dry_run": bool,
            "plan": [str, ...],         # dry_run=True 时才有
        }
    """
    plan: List[str] = []
    def _add_plan(msg: str) -> None:
        plan.append(msg)
        logger.info(f"[DRY-RUN] {msg}")
    code_norm = (code or "").strip().upper()
    if not code_norm:
        return {"ok": False, "code": code_norm, "error": "空车牌", "dry_run": dry_run}

    # 1) 找 wanted 目录
    wanted_dir = find_wanted_folder(Path(mw_root), code_norm)
    if wanted_dir is None:
        return {
            "ok": False,
            "code": code_norm,
            "error": f"wanted 目录里没找到 {code_norm} 文件夹",
            "dry_run": dry_run,
        }

    # 2) 读 NFO 拿 title + release_date。
    # 如果 wanted 目录里没有 movie.nfo（历史 wanted refresh 漏写），
    # 实时从 JavBus 抓一份补上 —— 否则目标目录会缺 NFO，Plex/Kodi 读不到 metadata。
    nfo_path = wanted_dir / "movie.nfo"
    title, release_date = parse_nfo(nfo_path)
    if not title:
        # 兜底：先尝试用目录名当 title，尝试 ensure_nfo 抓 JavBus
        dir_name = wanted_dir.name
        fallback_title = dir_name[len(code_norm) + 1:].strip() if dir_name.upper().startswith(code_norm) else dir_name
        if javbus_url:
            # 不是 dry_run 才真抓；dry_run 也照样抓（用户预览时也想看到 NFO 来源信息）
            ok, payload = ensure_nfo(
                wanted_dir, code_norm, javbus_url, javbus_proxy
            )
            if ok and isinstance(payload, dict):
                # 抓到了 → 重读 NFO
                title, release_date = parse_nfo(nfo_path)
            elif not ok:
                # 抓失败 → 用目录名兜底，但 result 里写 warning 让前端知道
                # 后续调用方把这个 warning 透传给前端 toast
                logger.warning(f"[{code_norm}] ensure_nfo 失败：{payload}")
                title = fallback_title
        else:
            # 没配 javbus_url（NFO 缺失但又不能联网）→ 兜底
            title = fallback_title

    # 最后兜底：title 还是空（NFO 缺、JavBus 也没抓到、目录名又解析失败）
    if not title:
        return {
            "ok": False,
            "code": code_norm,
            "error": f"无法确定 {code_norm} 的标题（缺 movie.nfo 且 JavBus 也抓不到）",
            "dry_run": dry_run,
        }

    month = month_bucket(release_date, fallback_now=True)

    # 3) 目标目录
    safe_title = re.sub(r'[<>:"/\\|?*]', "_", title).strip()
    dest_dir = Path(lib_root) / month / f"{code_norm} {safe_title}"

    if dest_dir.exists():
        return {
            "ok": False,
            "skipped": "already_organized",
            "code": code_norm,
            "dest_folder": str(dest_dir),
            "error": f"目标目录已存在（{dest_dir.name}），跳过",
            "dry_run": dry_run,
        }

    if dry_run:
        # dry_run 路径：只列计划，不实际写盘 / 不触发回调
        _add_plan(f"[mkdir] {dest_dir}")
        # 列出 wanted 元数据文件将复制哪些（用 set 去重：movie.nfo 同时匹配
        # "movie.nfo" 和 "*.nfo" 两个 pattern，必须 dedupe 避免 plan 里出现两次）
        planned_files: set[str] = set()
        wanted_patterns: List[str] = []
        wanted_patterns.extend(COVER_NAMES)
        wanted_patterns.extend(FANART_NAMES)
        wanted_patterns.append("movie.nfo")
        wanted_patterns.append("*.nfo")
        wanted_patterns.extend(["sample_*.jpg", "sample_*.png"])
        for pattern in wanted_patterns:
            for src_file in wanted_dir.glob(pattern):
                if src_file.is_file() and src_file.name not in planned_files:
                    planned_files.add(src_file.name)
                    _add_plan(f"[copy] {src_file.name} -> {dest_dir / src_file.name}")
        # NAS 下载
        nas_item, _ = find_nas_download(Path(nas_download_path), code_norm)
        if nas_item is None:
            _add_plan(f"[scan] 未在 NAS 下载目录找到 {code_norm} 的下载文件")
        else:
            src_was_dir = nas_item.is_dir()
            if src_was_dir:
                _add_plan(f"[move-folder] {nas_item} -> {dest_dir}")
            videos = _collect_videos(nas_item)
            for v in videos:
                ext = v.suffix.lower()
                dst_name = f"{code_norm} {safe_title}{ext}"
                _add_plan(f"[move+rename] {v.name} -> {dest_dir / dst_name}")
            if src_was_dir:
                _add_plan(f"[rmtree] {nas_item}")
        return {
            "ok": True,
            "code": code_norm,
            "wanted_folder": str(wanted_dir),
            "dest_folder": str(dest_dir),
            "month": month,
            "files_copied": len(planned_files),
            "files_skipped": 0,
            "files_errored": 0,
            "videos_moved": 0,  # 仅预览，不算实际
            "videos_skipped": 0,
            "nas_source": str(nas_item) if nas_item else None,
            "nas_source_removed": False,
            "error": None,
            "dry_run": True,
            "plan": plan,
        }

    # 4) 复制 wanted 元数据文件
    files_stats = _copy_wanted_files(wanted_dir, dest_dir)
    logger.info(
        f"[{code_norm}] 元数据复制完成：{files_stats['copied']} 个新文件，"
        f"{files_stats['skipped']} 个已存在跳过"
    )

    # 5) 找 NAS 上的下载项
    nas_item, nas_parent = find_nas_download(
        Path(nas_download_path), code_norm
    )
    if nas_item is None:
        return {
            "ok": True,  # 元数据已复制成功（只是没找到视频）
            "code": code_norm,
            "wanted_folder": str(wanted_dir),
            "dest_folder": str(dest_dir),
            "month": month,
            "files_copied": files_stats["copied"],
            "files_skipped": files_stats["skipped"],
            "files_errored": files_stats["errors"],
            "videos_moved": 0,
            "videos_skipped": 0,
            "nas_source": None,
            "nas_source_removed": False,
            "warning": f"未在 NAS 下载目录找到 {code_norm} 的下载文件",
            "dry_run": dry_run,
        }

    # 6) 收集视频文件 + 移动 + 重命名
    src_was_dir = nas_item.is_dir()
    videos = _collect_videos(nas_item)
    move_stats = _move_videos(videos, code_norm, title, dest_dir)
    logger.info(
        f"[{code_norm}] 视频移动：{len(move_stats['moved'])} 个移动，"
        f"{len(move_stats['skipped'])} 个跳过，{len(move_stats['errors'])} 个失败"
    )

    # 7) 清理源（如果是文件夹，把剩余非视频内容一起删；如果空了也删）
    source_removed = False
    if src_was_dir:
        # 移动完后尝试清理源文件夹
        # 1) 移除我们移走的视频文件（如果还在）
        for v in videos:
            if v.exists():
                try:
                    v.unlink()
                except OSError:
                    pass
        # 2) 删除空目录
        source_removed = _cleanup_empty_dir(nas_item)
        if not source_removed:
            # 还有残留内容 -> 整体删除（用户说"如果下载的是一个文件夹...应该将这个文件夹删除"）
            try:
                shutil.rmtree(nas_item)
                source_removed = True
                logger.info(f"[{code_norm}] 删除 NAS 源文件夹：{nas_item}")
            except OSError as e:
                logger.warning(f"删除 NAS 源文件夹失败 {nas_item}：{e}")

    # 8) 触发 library scanner 更新索引（让前端的"本地已有"徽章立刻亮起）
    if on_library_change is not None:
        try:
            on_library_change()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"library 索引更新回调失败：{e}")

    return {
        "ok": True,
        "code": code_norm,
        "wanted_folder": str(wanted_dir),
        "dest_folder": str(dest_dir),
        "month": month,
        "files_copied": files_stats["copied"],
        "files_skipped": files_stats["skipped"],
        "files_errored": files_stats["errors"],
        "videos_moved": len(move_stats["moved"]),
        "videos_skipped": len(move_stats["skipped"]),
        "videos_errored": len(move_stats["errors"]),
        "nas_source": str(nas_item),
        "nas_source_removed": source_removed,
        "error": None,
        "dry_run": dry_run,
    }


def organize_movie_sync(*args, **kwargs) -> Dict[str, Any]:
    """同步便捷封装（路由层用 to_thread 包调用，避免阻塞事件循环）。"""
    return organize_movie(*args, **kwargs)