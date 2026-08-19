"""磁力抓取任务执行体。

从原 ``gallery_server.run_scrape_job`` 提取，保持原行为：
- 后台线程中 ``asyncio.run`` 跑 ``MagnetSpider.crawl_and_process``
- 结束后把结果写入 ``output/magnets.json`` 与 ``output/magnets_links.txt``
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

from javbus_scrapling import JavbusSpider
from library_scanner import LibraryIndex

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
    output_dir: Path,
    javbus_url: str,
    library_index: Optional[LibraryIndex] = None,
) -> Dict[str, str]:
    """把抓取结果写入 magnets.json（schema_version: 2）与 magnets_links.txt。"""
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
                output_dir,
                os.getenv("JAVBUS_URL", "https://www.javbus.com/"),
                library_index=library_index,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"写入结果文件失败：{e}")
            job.error = job.error or f"写入结果文件失败：{e}"
        logging.getLogger().removeHandler(handler)