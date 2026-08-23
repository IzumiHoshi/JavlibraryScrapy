"""磁力抓取任务执行体。

从原 ``gallery_server.run_scrape_job`` 提取，保持原行为：
- 后台线程中 ``asyncio.run`` 跑 ``MagnetSpider.crawl_and_process``
- 结束后把结果写入 ``<magnets_index>`` 与派生 ``<magnets_index 父目录>/magnets_links.txt``
  （路径由 Settings.magnets_index / .env 的 ``MAGNETS_INDEX`` 决定；默认 ``output/magnets.json``）
- 临时挂一个 ``JobLogHandler`` 到 root logger
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from javlibraryscrapy.library.scanner import LibraryIndex
from javlibraryscrapy.scraping.javbus import JavbusSpider

from .jobs import JobLogHandler, ScrapeJob

logger = logging.getLogger("gallery.runner")


class MagnetSpider(JavbusSpider):
    """只取磁力链接的 JavbusSpider 子类。"""

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
    magnets_index: Path,
    javbus_url: str,
    library_index: Optional[LibraryIndex] = None,
) -> Dict[str, str]:
    """把抓取结果写入 magnets.json（schema_version: 2）与同目录 magnets_links.txt。

    ``magnets_index`` 直接给 JSON 路径（来自 Settings.magnets_index）；链接文件
    路径由其父目录 + 派生 basename 决定：``<parent>/magnets_links.txt``（与
    magnets.json 同名但后缀换成 ``_links.txt``）。两者必须在同一目录以便前端/用户
    在同一处拿全。
    """
    magnets_index = Path(magnets_index)
    magnets_index.parent.mkdir(parents=True, exist_ok=True)
    results = job.results()  # 仅本次任务实际抓取的 codes

    def annotate(r: Dict[str, Any]) -> Dict[str, Any]:
        match = library_index.find_match(r["code"]) if library_index else None
        return {
            **r,
            "local_exists": match is not None,
            "library_folder": match.folder if match else None,
        }

    items: List[Dict[str, Any]] = [annotate(r) for r in results]
    # 加入 wanted 缓存的磁力（V2 增强）：状态 ok + 有 magnet，直接 merge 进结果集
    # 去重：code 已在 results 里的话跳过（避免重复写）
    seen_codes = {item["code"] for item in items}
    for r in job.extra_cached:
        if r["code"] in seen_codes:
            continue
        items.append(annotate(r))
        seen_codes.add(r["code"])

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

    json_path = magnets_index
    payload = {
        "schema_version": 2,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # 磁力链接文件：含真正抓到的 magnet + wanted 缓存的 magnet；跳过 local_skip/失败/无磁力
    # 同目录派生：把 ``magnets.json`` 换成 ``magnets_links.txt``，跟随 magnets_index 同名
    # 但命名空间保持 magnets_xxx.txt/_links.txt 形式（用户期望的固定 basename）。
    links_basename = "magnets_links.txt"
    links_path = magnets_index.parent / links_basename
    links = [r["magnet"] for r in results if r.get("magnet")]
    for r in job.extra_cached:
        if r.get("magnet"):
            links.append(r["magnet"])
    with open(links_path, "w", encoding="utf-8") as f:
        f.write("\n".join(links))
        if links:
            f.write("\n")

    logger.info(
        f"已写入 {json_path}（{len(items)} 条，本地跳过 {len(job.skipped)} 条，"
        f"wanted 缓存 {len(job.extra_cached)} 条）"
        f"与 {links_path}（{len(links)} 条磁力）"
    )
    return {"json": str(json_path), "links": str(links_path)}


def create_magnet_spider(
    job: ScrapeJob, root_dir: Path, proxy: Optional[str]
) -> MagnetSpider:
    """创建磁力爬虫，并显式应用从项目 .env 读取的代理。

    ``root_dir`` 仍用作 JavbusSpider 的 root_dir（MagnetSpider 仅做磁力解析、不落
    cover，所以该参数实际不影响输出；保留签名以便服务层复用）。
    """
    spider = MagnetSpider(job=job, root_dir=root_dir)
    spider.proxy_enabled = proxy is not None
    spider.proxy = proxy
    return spider


def run_scrape_job(
    job: ScrapeJob,
    magnets_index: Path,
    proxy: Optional[str],
    library_index: Optional[LibraryIndex] = None,
    scratch_dir: Optional[Path] = None,
) -> None:
    """在后台线程中执行抓取（内部自建事件循环）。

    - ``magnets_index``：结果 JSON 的写入路径（来自 Settings.magnets_index）；
      ``magnets_links.txt`` 与之同目录派生。
    - ``scratch_dir``：JavbusSpider 的 root_dir（磁力模式下不落 cover，传一个
      临时目录即可，默认用 ``magnets_index.parent`` 复用父目录）。
    """
    handler = JobLogHandler(job)
    logging.getLogger().addHandler(handler)
    try:
        root_dir = scratch_dir if scratch_dir is not None else Path(magnets_index).parent
        spider = create_magnet_spider(job, root_dir, proxy)
        logger.info(f"磁力抓取代理：{'已启用' if proxy else '未启用'}")
        car_list = [(code, "") for code in job.codes]
        logger.info(f"开始抓取 {len(car_list)} 个车牌的磁力链接")
        asyncio.run(spider.crawl_and_process(car_list))
        job.status = "done"
    except Exception as e:  # noqa: BLE001
        logger.error(f"抓取任务失败：{e}")
        job.status = "error"
        job.error = str(e)
    finally:
        job.finalize()
        try:
            job.outputs = write_job_outputs(
                job,
                magnets_index,
                os.getenv("JAVBUS_URL", "https://www.javbus.com/"),
                library_index=library_index,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"写入结果文件失败：{e}")
            job.error = job.error or f"写入结果文件失败：{e}"
        logging.getLogger().removeHandler(handler)