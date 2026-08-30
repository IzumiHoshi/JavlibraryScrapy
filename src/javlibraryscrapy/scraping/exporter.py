"""统一的 JAVBus 削刮 + 本地库写入器。

把 `cli/workflow.py` step3、`cli/export_mostwanted.py`、`server/services/wanted_refresh.py`
三套并行的削刮代码合并到 ``MovieExporter``。所有"按车号削刮并写本地库"的场景都走这个类。

输出布局（每部影片，默认）：
    <output_root>/<CARID> <title>/
        ├── <CARID> <title>.<ext>    # 视频（move_video=True 时）
        ├── movie.nfo                # NFO（Kodi/Plex 兼容）
        ├── poster.jpg               # JAVLibrary 缩略图
        ├── fanart.jpg               # JAVBus 原图
        └── sample_NNN.jpg           # JAVBus sample waterfall（NN 从 001 起）

输出布局（``bucket_by_month=True`` 时）：
    <output_root>/<YYYY-MM>/<CARID> <title>/
        └── ... 同上 ...
  月份桶从 ``release_date``（"YYYY-MM-DD"）前缀推；解析失败归 ``unknown``。

集中输出（collect_magnets=True 时）：
    <output_root>/magnets.json       # schema_version=2
    <output_root>/magnets_links.txt  # 一行一条 magnet（仅 status=ok）

调用方差异靠 5 个开关表达：
    - move_video:        workflow=True，其它=False
    - download_samples:  wanted_refresh=False，其它=True
    - collect_magnets:   全 True
    - magnets_index:     默认 <output_root>/magnets.json
    - bucket_by_month:   workflow=True（CLI 端到端流水），其它=False
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from urllib3.exceptions import InsecureRequestWarning

from javlibraryscrapy.scraping.javbus import JavbusSpider
from javlibraryscrapy.utils.filesave import write_xml

# 走代理时显式 ``verify=False``（MITM 自签 CA），urllib3 会刷屏；
# 统一静默，与 cli/gallery.py 入口行为一致。
warnings.filterwarnings("ignore", category=InsecureRequestWarning)

logger = logging.getLogger("javlibraryscrapy.exporter")

# JAVLibrary 缩略图 Referer（部分 CDN 需要这个头才返回 200）
_JAVLIBRARY_REFERER = "https://www.javlibrary.com/cn/"

# magnet 状态枚举
_MAGNET_OK = "ok"
_MAGNET_NO_MAGNET = "no_magnet"
_MAGNET_FAILED = "failed"

# 月份桶键格式：YYYY-MM。``release_date`` 通常是 ``"2023-07-22"`` 这种，
# 取前 7 位即可。无法解析（空串 / 非标准格式）→ ``unknown``。
_BUCKET_RE = re.compile(r"^(\d{4})-(\d{2})")


def bucket_for_release_date(release_date: Optional[str]) -> str:
    """把 ``"2023-07-22"`` 变成 ``"2023-07"``；无法解析返回 ``"unknown"``。

    跟 ``wanted_refresh._bucket_for_release_date`` 同语义；提到模块顶层
    供 ``MovieExporter`` 复用，避免 ``cli/workflow.py`` 跟 ``server/services``
    互相 import。
    """
    if not release_date:
        return "unknown"
    m = _BUCKET_RE.match(release_date.strip())
    return f"{m.group(1)}-{m.group(2)}" if m else "unknown"


# 从 ``download_samples`` 落地的临时文件名 ``<CARID>_sample_NNN.jpg`` 反推 idx。
# 调用方不能用 ``enumerate(downloaded, start=1)``：download_samples 内部失败时
# 返回 list 不连续（缺 idx 的位置没有 path），列表索引会跟原 URL idx 错位。
_SAMPLE_IDX_RE = re.compile(r"_sample_(\d+)\.jpg$")


class MovieExporter(JavbusSpider):
    """统一的 JAVBus 削刮 + 本地库写入器。

    用法：
        exporter = MovieExporter(
            output_root=Path("Z:/JAV/MostWanted"),
            move_video=False,
            download_samples=True,
            collect_magnets=True,
            magnets_index=Path("Z:/JAV/MostWanted/magnets.json"),
            javlibrary_proxy="http://127.0.0.1:10808",
        )
        await exporter.export_movies(
            car_list=[("ABF-340", ""), ("MIAB-001", "")],
            cover_urls={
                "ABF-340": "https://.../cover.jpg",
                "MIAB-001": "https://.../cover.jpg",
            },
        )

    继承自 :class:`JavbusSpider`，沿用其：
        - javbus_url / javbus_base_url
        - proxy（PROXY_JAVBUS_ENABLED + PROXY）
        - parse() / _extract_magnet_link() / download_cover() / download_samples()
        - crawl_and_process()（AsyncDynamicSession 生命周期管理）
    """

    def __init__(
        self,
        output_root: Path,
        *,
        move_video: bool = False,
        download_samples: bool = True,
        collect_magnets: bool = True,
        magnets_index: Optional[Path] = None,
        javlibrary_proxy: Optional[str] = None,
        bucket_by_month: bool = False,
        overwrite_nfo: bool = True,
    ):
        # 把 output_root 同时设到 root_dir：JavbusSpider.download_cover
        # 会把临时 ``<CARID>.png`` 落到这里，方便我们后续 rename 到 fanart.jpg。
        super().__init__(root_dir=output_root)
        self.output_root = Path(output_root)
        self.move_video = move_video
        # 注意：不能直接 ``self.download_samples = download_samples``，否则会把
        # 父类的 ``download_samples`` 方法遮蔽。存到内部不同名字。
        self._download_samples_enabled = download_samples
        self.collect_magnets = collect_magnets
        self.magnets_index = (
            Path(magnets_index) if magnets_index is not None
            else self.output_root / "magnets.json"
        )
        # 默认沿用 JAVBus proxy（向后兼容：旧 MostWantedExporter 把同一份 proxy
        # 给 JAVLibrary 缩略图用）
        self.javlibrary_proxy = javlibrary_proxy if javlibrary_proxy is not None else self.proxy
        # 是否按 release_date 推月份桶布局：
        #   False（默认）：<output_root>/<CARID> <title>/   ← wanted / export_mostwanted
        #   True：<output_root>/<YYYY-MM>/<CARID> <title>/   ← workflow 端到端
        self.bucket_by_month = bucket_by_month
        # NFO 覆写开关：默认 True（保持 export_mostwanted / workflow 旧行为）；
        # ``library.backfill`` 显式传 False，避免重抓已存在的 NFO
        # （与"补齐缺失，不动已有"的语义一致；cover/samples/fanart 的幂等
        # 由 ``_place_fanart`` / ``_download_javlibrary_cover`` /
        # ``_move_samples_to_target`` 的 .exists() 检查兜底，与本开关无关）。
        self.overwrite_nfo = overwrite_nfo

        # 每次 export_movies 调用前重置
        self._magnet_results: List[Dict[str, Any]] = []
        self._cover_urls: Dict[str, str] = {}
        self._attempted_codes: set[str] = set()
        self._written_codes: set[str] = set()
        self._failed_codes: set[str] = set()

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    async def export_movies(
        self,
        car_list: List[Tuple[str, str]],
        *,
        cover_urls: Optional[Dict[str, str]] = None,
        on_progress: Optional[Callable[[str, str], None]] = None,
        session: Optional["AsyncDynamicSession"] = None,
    ) -> Dict[str, Any]:
        """批量削刮并写入本地库。

        Args:
            car_list: ``[(car_id, video_path_or_empty), ...]``
                - video_path 非空且 move_video=True → 从源路径移进子目录
                - video_path 为空 → 不移动（视频已在库里）
            cover_urls: ``{car_id: javlibrary_cover_url, ...}``
                用于下载 poster.jpg；缺 key 的车跳过 poster 下载。
            on_progress: 可选回调 ``(car_id, status)``；status ∈
                ``{"ok", "failed"}``，在每部 process_movie 结束后触发。
            session: 可选外部 AsyncDynamicSession（让 caller 决定生命周期；
                传入时复用同一 session，避免每部重建 Chromium 上下文）。
                通常用法：caller 在 ``async with AsyncDynamicSession(...) as s:``
                上下文里循环调本方法，每次复用 ``s``。

        Returns:
            ``{"total": int, "written": int, "failed": int,
               "skipped": int, "magnets_collected": int}``

        注意：
            - ``skipped`` 始终为 0（当前实现总是覆写 NFO；幂等靠 ``if exists: skip``
              在 image 下载端实现，NFO 写入仍走全量）
            - ``written`` = process_movie 成功写完 NFO 的车数
            - ``failed`` = 网络/解析失败 + process_movie 异常 的车数
        """
        # 重置本轮状态
        self._magnet_results = []
        self._cover_urls = dict(cover_urls or {})
        self._attempted_codes = set()
        self._written_codes = set()
        self._failed_codes = set()

        total = len(car_list)

        # 复用父类 crawl_and_process：传入 session 时复用（caller 管生命周期），
        # 不传则 crawl_and_process 内部自己 ``async with`` 新建。
        try:
            await self.crawl_and_process(car_list, session=session)
        except Exception as e:  # noqa: BLE001
            # 整个流程崩了（极少见；网络断 / session 异常）；已处理的算入统计
            logger.error(f"export_movies 主流程异常：{e}")

        # ---- 写集中 magnet 索引 ----
        if self.collect_magnets:
            self._write_magnets_index()

        # ---- 清理 output_root 下残留的临时 <CARID>.png ----
        self._cleanup_temp_pngs()

        # ---- 统计 ----
        # 未被抓的车（网络/解析失败）：从未进入 process_movie
        unparsed = total - len(self._attempted_codes)

        stats = {
            "total": total,
            "written": len(self._written_codes),
            "skipped": 0,
            "failed": unparsed + len(self._failed_codes),
            "magnets_collected": sum(
                1 for r in self._magnet_results
                if r.get("status") == _MAGNET_OK
            ),
        }

        # 触发进度回调（best-effort；回调异常不抛）
        if on_progress is not None:
            try:
                for code in self._attempted_codes:
                    status = "failed" if code in self._failed_codes else "ok"
                    on_progress(code, status)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"on_progress 回调异常：{e}")

        logger.info(
            f"export_movies 完成：total={stats['total']}，"
            f"written={stats['written']}，failed={stats['failed']}，"
            f"magnets_ok={stats['magnets_collected']}"
        )
        return stats

    # ------------------------------------------------------------------ #
    # 覆写 JavbusSpider.process_movie
    # ------------------------------------------------------------------ #
    async def process_movie(self, info: Dict[str, Any]) -> None:
        """统一处理每部影片：建子目录 → 移视频 → 写 NFO → 落封面 → 下 poster → 下 samples → 收 magnet。"""
        carid = (info.get("carid") or "").strip()
        title = (info.get("title") or "").strip()
        self._attempted_codes.add(carid)
        if not carid or not title:
            logger.warning(f"标题或车牌为空，跳过：carid={carid!r}, title={title!r}")
            self._failed_codes.add(carid)
            return

        try:
            if self.bucket_by_month:
                # 按 release_date 推月份桶：<output_root>/<YYYY-MM>/<CARID> <title>/
                # 解析失败归 ``unknown``，跟 wanted_refresh 的 _bucket_for_release_date 同语义
                bucket = bucket_for_release_date(info.get("release_date") or "")
                save_dir = self.output_root / bucket / f"{carid} {title}"
            else:
                save_dir = self.output_root / f"{carid} {title}"
            save_dir.mkdir(parents=True, exist_ok=True)

            # 1. 移动视频（可选）
            if self.move_video:
                self._move_video(info, carid, title, save_dir)

            # 2. 写 NFO（统一名 movie.nfo）
            # ``overwrite_nfo=False`` 时若 movie.nfo 已存在则跳过（不主动覆写）；
            # cover / fanart / samples 的"存在则跳过"在各自 helper 里。
            nfo_path = save_dir / "movie.nfo"
            if self.overwrite_nfo or not nfo_path.exists():
                write_xml(nfo_path, info)

            # 3. fanart.jpg —— JavbusSpider.download_cover 落地的临时 <carid>.png
            self._place_fanart(info, carid, save_dir)

            # 4. poster.jpg —— 从 JAVLibrary cover_url 下载
            cover_url = self._cover_urls.get(carid)
            if cover_url:
                self._download_javlibrary_cover(cover_url, save_dir / "poster.jpg")
            else:
                # cover_url 缺失（JAVLibrary wanted 没这车 / wanted JSON 没记录）：
                # 用 JAVBus 的 fanart 兜底复制一份到 poster.jpg，让 GUI 至少能显示图。
                # 注意：fanart 是横版，poster 是竖版（5:7），这是妥协方案；
                # 真值还得从 JAVLibrary 抓 — 把缺失车号加进 wanted 让 cover_url 有值。
                self._fallback_poster_from_fanart(save_dir)

            # 5. samples —— JAVBus sample waterfall
            if self._download_samples_enabled and info.get("samples"):
                self._move_samples_to_target(info["samples"], carid, save_dir)

            # 6. 收集 magnet
            if self.collect_magnets:
                self._magnet_results.append({
                    "code": carid,
                    "title": title,
                    "magnet": (info.get("magnet") or "").strip() or None,
                    "status": self._magnet_status(info),
                    "release_date": (info.get("release_date") or "").strip(),
                    "actors": (info.get("actors") or "").strip(),
                    "javbus_url": f"{self.javbus_url}{carid}",
                })

            self._written_codes.add(carid)
            logger.info(f"完成处理：{carid} {title}")

        except Exception as e:  # noqa: BLE001
            logger.error(f"处理电影失败 - 车牌: {carid}, 错误: {e}")
            self._failed_codes.add(carid)

    # ------------------------------------------------------------------ #
    # 步骤 helpers
    # ------------------------------------------------------------------ #
    def _move_video(
        self, info: Dict[str, Any], carid: str, title: str, save_dir: Path
    ) -> None:
        """move_video=True 时把 info['path'] 处的视频移进子目录并改名。

        ``info["path"]`` 为空字符串（wanted/单部刷新场景）时直接返回，
        避免 ``Path("")`` 被解释成 cwd。
        """
        path_str = (info.get("path") or "").strip()
        if not path_str:
            return
        video_src = Path(path_str)
        if not video_src.is_file():
            return
        video_dst = save_dir / f"{carid} {title}{video_src.suffix}"
        try:
            shutil.move(str(video_src), str(video_dst))
            logger.info(f"已移动视频：{video_src.name} → {video_dst.name}")
        except OSError as e:
            logger.warning(f"移动视频失败 {video_src.name}: {e}")

    def _place_fanart(
        self, info: Dict[str, Any], carid: str, save_dir: Path
    ) -> None:
        """把 JavbusSpider.download_cover 落地的临时 ``<CARID>.png`` rename 到 fanart.jpg。"""
        cover_raw = info.get("cover")
        if not cover_raw:
            return
        cover_path = Path(cover_raw) if not isinstance(cover_raw, Path) else cover_raw
        if not cover_path.exists():
            return
        fanart_dst = save_dir / "fanart.jpg"
        if cover_path.resolve() == fanart_dst.resolve():
            return  # 已经在目标位置
        if fanart_dst.exists():
            # 已有 fanart.jpg → 删临时文件，不覆盖
            try:
                cover_path.unlink()
            except OSError:
                pass
            return
        try:
            cover_path.rename(fanart_dst)
        except OSError as e:
            logger.warning(f"移动 cover → fanart.jpg 失败 {carid}: {e}")

    def _download_javlibrary_cover(
        self, url: str, dest: Path, timeout: int = 10
    ) -> bool:
        """下载 JAVLibrary 缩略图作为 poster.jpg（同步 requests）。"""
        if not url:
            return False
        if dest.exists():
            logger.debug(f"poster.jpg 已存在，跳过：{dest.name}")
            return True
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": _JAVLIBRARY_REFERER,
        }
        proxy = self.javlibrary_proxy
        try:
            r = requests.get(
                url,
                headers=headers,
                timeout=timeout,
                proxies=({"http": proxy, "https": proxy} if proxy else None),
                verify=False if proxy else True,
            )
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            logger.info(f"已下载 poster.jpg：{dest.name}")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"下载 poster.jpg 失败 {url[:80]}: {e}")
            return False

    def _fallback_poster_from_fanart(self, save_dir: Path) -> bool:
        """``cover_url`` 缺失时：从已落地的 fanart.jpg 裁切右半边作 poster.jpg。

        JAVBus 的 fanart 是横版大图，海报（poster）叠在 fanart **右半边**
        （5:7 竖版）—— 利用 :func:`utils.fanart.split_poster_from_fanart`
        精确裁切，比"直接复制"更接近真竖版海报形状。

        注意：这是 wanted JSON 没 cover_url 时的妥协方案；真值还得把车号
        加进 wanted 让 ``cover_url`` 有值，从 JAVLibrary 抓正版 poster。
        """
        from javlibraryscrapy.utils.fanart import split_poster_from_fanart

        poster = save_dir / "poster.jpg"
        if poster.exists():
            return False  # 已有 poster，不动
        fanart = save_dir / "fanart.jpg"
        if not fanart.exists():
            return False  # fanart 也没有，无图可裁
        try:
            split_poster_from_fanart(fanart, poster)
            if poster.exists():
                logger.info(
                    f"已从 fanart 右半边裁切生成 poster.jpg：{poster.name}"
                )
                return True
            return False  # split_poster_from_fanart 内部异常，没生成 poster
        except Exception as e:  # noqa: BLE001
            logger.warning(f"fanart 兜底裁切 poster.jpg 失败：{e}")
            return False

    def _move_samples_to_target(
        self, sample_urls: List[str], car_id: str, save_dir: Path,
        max_workers: int = 6,
    ) -> int:
        """调用父类 download_samples（落到 ``<root_dir>/<CARID>_sample_NNN.jpg``），
        然后 rename 到 ``<save_dir>/sample_NNN.jpg``。

        返回成功移动的 sample 数。

        注意：idx 从 ``src.name`` 反推，不用 ``enumerate``。download_samples 内部
        单张失败时 list 不连续，列表索引会跟原 URL idx 错位 → 文件名错位 + 缺失。

        性能：默认 ``max_workers=6`` 并发下载 sample（每张 ~1-2 秒，20 张串行
        约 30 秒 → 并发 6 张约 5-7 秒）。受 NAS 带宽限制，并发数不宜过大。
        """
        if not sample_urls:
            return 0
        # 并发下载到临时位置：避免对 JavbusSpider 父类接口做破坏性改动
        downloaded: List[Path] = []
        if max_workers <= 1:
            # 单线程 fallback（与原 JavbusSpider.download_samples 行为一致）
            try:
                downloaded = self.download_samples(sample_urls, car_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"download_samples 调用失败 {car_id}: {e}")
                downloaded = []
        else:
            downloaded = self._download_samples_concurrent(
                sample_urls, car_id, max_workers=max_workers
            )

        moved = 0
        for src in downloaded:
            m = _SAMPLE_IDX_RE.search(src.name)
            if not m:
                continue
            idx = int(m.group(1))
            dest = save_dir / f"sample_{idx:03d}.jpg"
            if dest.exists():
                # 已有 → 删临时，不覆盖
                try:
                    src.unlink()
                except OSError:
                    pass
                continue
            try:
                if src.exists():
                    src.rename(dest)
                    moved += 1
            except OSError as e:  # noqa: BLE001
                logger.warning(f"移动 sample {idx} 失败 {car_id}: {e}")

        # 清理可能残留的 <CARID>_sample_*.jpg（被跳过或下载失败的）
        for leftover in self.root_dir.glob(f"{car_id}_sample_*.jpg"):
            try:
                leftover.unlink()
            except OSError:
                pass

        return moved

    def _download_samples_concurrent(
        self,
        sample_urls: List[str],
        car_id: str,
        *,
        max_workers: int = 6,
        per_request_timeout: int = 30,
    ) -> List[Path]:
        """并发下载 sample 到 ``<root_dir>/<CARID>_sample_NNN.jpg``。

        与父类 :meth:`JavbusSpider.download_samples` 行为一致（文件名格式相同），
        但用 ThreadPoolExecutor 并发请求。失败的单张不影响整体。

        Returns:
            成功下载的临时路径列表（顺序与 sample_urls 一致 —— 失败的 idx
            会缺失，但 NNN 文件名仍正确）。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        proxy = self.proxy
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "image/jpeg,image/*,*/*;q=0.8",
            "Referer": self.javbus_url.rstrip("/") + "/",
        }
        proxies = ({"http": proxy, "https": proxy} if proxy else None)
        verify = False if proxy else True

        def _fetch(idx_url: Tuple[int, str]):
            idx, url = idx_url
            tmp = self.root_dir / f"{car_id}_sample_{idx:03d}.jpg"
            try:
                r = requests.get(
                    url,
                    headers=headers,
                    timeout=per_request_timeout,
                    proxies=proxies,
                    verify=verify,
                )
                r.raise_for_status()
                tmp.write_bytes(r.content)
                return tmp
            except Exception as e:  # noqa: BLE001
                logger.warning(f"下载 sample {idx} 失败 {car_id}: {e}")
                return None

        results: List[Optional[Path]] = [None] * len(sample_urls)
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {
                    ex.submit(_fetch, (i + 1, url)): i
                    for i, url in enumerate(sample_urls)
                }
                for fut in as_completed(futures):
                    i = futures[fut]
                    try:
                        results[i] = fut.result()
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"sample future 异常 {car_id} idx={i+1}: {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"sample 并发下载失败 {car_id}: {e}")
        return [p for p in results if p is not None]

        if moved:
            logger.info(f"{car_id} samples 落地 {moved}/{len(sample_urls)} 张")
        return moved

    # ------------------------------------------------------------------ #
    # magnet 收集 + 索引落盘
    # ------------------------------------------------------------------ #
    @staticmethod
    def _magnet_status(info: Dict[str, Any]) -> str:
        """根据 info 推导 magnet 状态。

        - 有 magnet 文本 → ok
        - 解析成功但无 magnet → no_magnet（页面就没给，部分片源常见）
        - 其它 → failed
        """
        magnet = (info.get("magnet") or "").strip()
        if magnet:
            return _MAGNET_OK
        if info.get("title"):
            return _MAGNET_NO_MAGNET
        return _MAGNET_FAILED

    def _write_magnets_index(self) -> None:
        """写 magnets.json (schema v2) + magnets_links.txt。

        **合并策略**：读旧 file → 按 ``code`` 去重（新抓的覆盖旧的）→ 写回。
        避免覆盖式写入丢历史磁力记录。
        """
        if not self._magnet_results:
            return
        self.magnets_index.parent.mkdir(parents=True, exist_ok=True)

        # 读旧 items（按 code 索引）；失败时按空处理
        existing: list[dict] = []
        if self.magnets_index.exists():
            try:
                with open(self.magnets_index, encoding="utf-8") as f:
                    old = json.load(f)
                if isinstance(old, dict):
                    existing = old.get("items") or []
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"读旧 magnets.json 失败，按空处理：{e}")
                existing = []

        # 合并：按 code 去重，新抓的覆盖旧的（magnet + release_date 可能更新）
        merged: dict[str, dict] = {
            it.get("code"): it
            for it in existing
            if isinstance(it, dict) and it.get("code")
        }
        for it in self._magnet_results:
            code = it.get("code")
            if code:
                merged[code] = it
        items = list(merged.values())

        payload = {
            "schema_version": 2,
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
            "items": items,
        }
        try:
            with open(self.magnets_index, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except OSError as e:  # noqa: BLE001
            logger.error(f"写 magnets.json 失败：{e}")
            return

        # magnets_links.txt：仅 status=ok 的 magnet；空时写空文件
        links_path = self.magnets_index.parent / "magnets_links.txt"
        # 用合并后的 items（保留历史 ok 记录）
        links = [
            r["magnet"] for r in items
            if r.get("status") == _MAGNET_OK and r.get("magnet")
        ]
        try:
            with open(links_path, "w", encoding="utf-8") as f:
                if links:
                    f.write("\n".join(links) + "\n")
        except OSError as e:  # noqa: BLE001
            logger.error(f"写 magnets_links.txt 失败：{e}")
            return

        logger.info(
            f"已写入 {self.magnets_index}（合并后 {len(items)} 条，"
            f"本次新增/覆盖 {len(self._magnet_results)} 条，"
            f"其中 ok={len(links)} 条磁力）"
        )

    # ------------------------------------------------------------------ #
    # 临时文件清理
    # ------------------------------------------------------------------ #
    def _cleanup_temp_pngs(self) -> None:
        """清掉 output_root 下残留的 ``<CARID>.png`` 临时封面。"""
        for png in self.output_root.glob("*.png"):
            try:
                png.unlink()
            except OSError:
                pass
