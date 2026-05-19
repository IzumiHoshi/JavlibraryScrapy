#!/usr/bin/env python
"""
快速测试脚本 - 测试爬虫的基本功能（仅爬取第一页）
"""

import asyncio
from pathlib import Path
from javlibrary_scrapling import JAVLibrarySpider
import logging

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_spider():
    """测试爬虫"""
    logger.info("开始测试 JAVLibrary 爬虫...")

    # 创建爬虫实例（可选：配置代理）
    spider = JAVLibrarySpider(
        output_dir=Path(__file__).parent / "output",
        proxy=None,  # 改为代理 URL 以启用代理，如 "http://127.0.0.1:7890"
    )

    logger.info("爬取第一页（测试）...")
    # 仅爬取第一页进行测试
    await spider.crawl(max_pages=1)

    # 保存结果
    spider.save_to_json("test_movies.json")
    spider.save_to_csv("test_movies.csv")

    # 打印摘要
    spider.print_summary()

    logger.info("测试完成！")


if __name__ == "__main__":
    asyncio.run(test_spider())
