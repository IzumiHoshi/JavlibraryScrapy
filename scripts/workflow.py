"""
工作流：从下载路径获取视频文件，调用 JAVBus 爬虫，输出到指定目录

流程：
1. 扫描下载目录中的视频文件
2. 提取车牌代码
3. 爬取 JAVBus 元数据
4. 生成 NFO 文件和封面图片到输出目录
"""

import argparse
import asyncio
import logging
import os
import shutil
from pathlib import Path
import sys
from pathlib import Path

# Add project root to path for imports
_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
from javbus_scrapling import JavbusSpider
from utils.car import javbuscar
from utils.filesave import write_xml
from utils.fanart import split_poster_from_fanart

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class WorkflowSpider(JavbusSpider):
    """支持输出目录的 JAVBus 爬虫"""

    def __init__(self, root_dir: Path, output_dir: Path):
        """
        初始化工作流爬虫

        Args:
            root_dir: 原始视频所在目录
            output_dir: 输出目录（元数据将写到此目录）
        """
        super().__init__(root_dir)
        self.output_dir = output_dir

    async def process_movie(self, info: dict):
        """处理电影信息，输出到指定目录"""
        try:
            if not info.get("title") or not info.get("carid"):
                logger.warning("标题或车牌为空，跳过处理")
                return

            car_id = info["carid"]
            title = info["title"].strip()
            filename_prefix = f"{car_id} {title}"
            save_dir = self.output_dir / filename_prefix
            save_dir.mkdir(parents=True, exist_ok=True)

            # 生成 NFO 文件
            nfo_filename = save_dir / f"{filename_prefix}.nfo"
            info_for_nfo = {
                **info,
                "path": save_dir / f"{filename_prefix}.mp4",  # 假路径，仅用于 NFO 生成
            }
            write_xml(nfo_filename, info_for_nfo)

            # 处理封面图片
            cover = info.get("cover", "")
            if cover:
                cover_path = Path(cover)
                if cover_path.exists():
                    fanart_path = save_dir / "fanart.png"
                    shutil.copy(cover_path, fanart_path)
                    split_poster_from_fanart(fanart_path, save_dir / "poster.png")

            logger.info(f"完成处理：{filename_prefix}")

        except Exception as e:
            logger.error(f"处理电影失败 - 车牌: {info.get('carid', 'unknown')}, 错误: {e}")


async def workflow(download_path: Path, output_path: Path, preview: bool = False):
    """
    执行工作流

    Args:
        download_path: 下载目录（包含原始视频）
        output_path: 输出目录（存放 NFO 和封面）
        preview: 预览模式，不执行爬取仅显示找到的文件
    """
    if not download_path.exists():
        logger.error(f"下载目录不存在: {download_path}")
        return

    output_path.mkdir(parents=True, exist_ok=True)

    logger.info(f"下载目录: {download_path}")
    logger.info(f"输出目录: {output_path}")

    # 扫描车牌
    cars = javbuscar(download_path)
    if not cars:
        logger.warning("未找到任何车牌")
        return

    logger.info(f"找到 {len(cars)} 个车牌")
    for car_id, path in cars:
        logger.info(f"  {car_id}: {path}")

    if preview:
        logger.info("预览模式，跳过爬取")
        return

    # 执行爬取
    spider = WorkflowSpider(root_dir=download_path, output_dir=output_path)
    await spider.crawl_and_process(cars)
    logger.info(f"工作流完成，共处理 {len(spider.movie_info_list)} 部电影")


def main():
    parser = argparse.ArgumentParser(description="JAVBus 爬虫工作流")
    parser.add_argument("download_path", type=Path, help="下载目录（原始视频所在）")
    parser.add_argument("output_path", type=Path, help="输出目录（存放 NFO 和封面）")
    parser.add_argument("--preview", action="store_true", help="预览模式，仅显示找到的文件")
    args = parser.parse_args()

    asyncio.run(workflow(args.download_path, args.output_path, args.preview))


if __name__ == "__main__":
    main()