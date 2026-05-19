"""
使用 Scrapling 框架爬取 JAVLibrary 网站
Scrapling: https://github.com/D4Vinci/Scrapling

功能：
- 从 JAVLibrary 爬取最想要的影片列表
- 提取每部影片的 ID、标题和封面
- 支持代理和 Cloudflare 机器人验证处理
- 支持多页爬取
"""

import logging
import asyncio
import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv
from scrapling.fetchers import AsyncDynamicSession

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class JAVLibrarySpider:
    """JAVLibrary 爬虫 - 爬取最想要的影片列表"""

    def __init__(
        self,
        base_url: str = "https://www.javlibrary.com/cn/vl_mostwanted.php",
        output_dir: Optional[Path] = None,
        proxy: Optional[str] = None,
    ):
        """
        初始化爬虫

        Args:
            base_url: 目标网址
            output_dir: 输出目录
            proxy: 代理 URL（可选）
        """
        self.base_url = base_url
        self.output_dir = Path(output_dir) if output_dir else Path.cwd() / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.proxy = proxy
        self.movies = []

    async def fetch_page(
        self, session: AsyncDynamicSession, page: int = 1
    ) -> str:
        """
        获取单个页面

        Args:
            session: Scrapling 会话对象
            page: 页码

        Returns:
            页面 HTML 内容
        """
        if page == 1:
            url = self.base_url
        else:
            url = f"{self.base_url}?page={page}"

        logger.info(f"正在抓取第 {page} 页：{url}")

        try:
            # 使用完整的请求头
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Referer": "https://www.javlibrary.com/cn/",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
            
            response = await session.fetch(url, headers=headers)
            
            # 检查响应状态
            if hasattr(response, 'status_code') and response.status_code >= 400:
                logger.error(f"请求失败，状态码：{response.status_code}")
                return None
            
            return response

        except Exception as e:
            logger.error(f"获取第 {page} 页失败：{e}")
            return None

    def parse_movies_from_html(self, html_content) -> List[Dict[str, Any]]:
        """
        从 HTML 解析影片信息

        Args:
            html_content: Scrapling Response 对象

        Returns:
            影片信息列表
        """
        movies = []

        try:
            # 查找所有影片容器
            video_items = html_content.css("div.video")

            if not video_items:
                logger.warning("未找到影片信息")
                return movies

            logger.info(f"找到 {len(video_items)} 部影片")

            for item in video_items:
                try:
                    # 提取影片 ID（从 id 属性中）
                    # id 属性格式：vid_javmefjl5q
                    vid_attr = item.css("::attr(id)").get()
                    if vid_attr:
                        # 移除 'vid_' 前缀获得真实 ID
                        movie_id = vid_attr.replace("vid_", "")
                    else:
                        movie_id = ""

                    # 提取影片代码（从 div.id 中）
                    code = item.css("div.id::text").get()
                    if code:
                        code = code.strip()

                    # 提取标题（从 title 属性）
                    title = item.css("a::attr(title)").get()
                    if title:
                        title = title.strip()

                    # 提取封面图片 URL
                    cover_url = item.css("img::attr(src)").get()
                    if cover_url and not cover_url.startswith("http"):
                        # 处理相对路径
                        if cover_url.startswith("./"):
                            # 本地保存的文件，需要从源站点获取
                            # 尝试从 onerror 属性获取备用 URL
                            onerror_attr = item.css("img::attr(onerror)").get()
                            if onerror_attr:
                                # 从 onerror 中提取 URL
                                # 格式：ThumbError(this, 'https://t2.pixhost.to/thumbs/7623/721821470_t677565.jpg');
                                import re

                                url_match = re.search(r"'(https://[^']+)'", onerror_attr)
                                if url_match:
                                    cover_url = url_match.group(1)
                        else:
                            cover_url = "https://www.javlibrary.com" + cover_url

                    movie_info = {
                        "id": movie_id,
                        "code": code,
                        "title": title,
                        "cover_url": cover_url,
                    }

                    movies.append(movie_info)
                    logger.info(
                        f"  解析影片：{code} - {title[:50]}... - ID: {movie_id}"
                    )

                except Exception as e:
                    logger.warning(f"解析单个影片失败：{e}")
                    continue

        except Exception as e:
            logger.error(f"解析页面失败：{e}")

        return movies

    async def get_page_count(self, session: AsyncDynamicSession) -> int:
        """
        获取总页数

        Args:
            session: Scrapling 会话对象

        Returns:
            总页数
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.javlibrary.com/cn/",
            }
            
            response = await session.fetch(self.base_url, headers=headers)
            
            # 查找最后一页链接
            last_page_link = response.css('a.page.last::attr(href)').get()

            if last_page_link:
                # 从 URL 中提取页码
                import re

                match = re.search(r"page=(\d+)", last_page_link)
                if match:
                    return int(match.group(1))

            # 如果找不到，查找最大页码
            page_links = response.css("a.page::attr(href)").getall()
            if page_links:
                import re

                max_page = 1
                for link in page_links:
                    match = re.search(r"page=(\d+)", link)
                    if match:
                        page_num = int(match.group(1))
                        max_page = max(max_page, page_num)
                return max_page

            return 1
        except Exception as e:
            logger.error(f"获取页数失败：{e}")
            return 1

    async def crawl(self, max_pages: Optional[int] = None):
        """
        爬取多个页面

        Args:
            max_pages: 最多爬取页数（None 表示爬取全部）
        """
        try:
            # 使用 AsyncDynamicSession 处理动态内容和 Cloudflare
            async with AsyncDynamicSession(
                load_dom=True,  # 加载 DOM
                network_idle=True,  # 等待网络空闲
                disable_resources=False,  # 允许加载资源以正确加载页面
                proxy=self.proxy,  # 使用代理
                headless=True,  # 无头浏览器
                timeout=90000,  # 90 秒超时（处理 Cloudflare 需要时间）
                stealth_mode=True,  # 隐身模式，避免被检测为机器人
            ) as session:
                logger.info("开始爬取 JAVLibrary...")

                # 首先获取总页数
                logger.info("正在获取总页数...")
                total_pages = await self.get_page_count(session)
                logger.info(f"总共 {total_pages} 页")

                # 如果指定了最多页数，取最小值
                if max_pages:
                    total_pages = min(max_pages, total_pages)

                # 爬取每一页
                for page in range(1, total_pages + 1):
                    try:
                        html_content = await self.fetch_page(session, page)

                        if html_content:
                            # 解析页面获取影片信息
                            page_movies = self.parse_movies_from_html(html_content)
                            self.movies.extend(page_movies)

                            logger.info(f"第 {page} 页完成，共提取 {len(page_movies)} 部影片")

                        # 添加延迟避免被封 IP
                        await asyncio.sleep(3)

                    except Exception as e:
                        logger.error(f"处理第 {page} 页失败：{e}")
                        continue

                logger.info(f"爬取完成，共获取 {len(self.movies)} 部影片")

        except Exception as e:
            logger.error(f"爬取过程失败：{e}")

    def save_to_json(self, filename: str = "movies.json"):
        """
        将影片信息保存为 JSON 文件

        Args:
            filename: 输出文件名
        """
        output_path = self.output_dir / filename
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(self.movies, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存到 {output_path}")
        except Exception as e:
            logger.error(f"保存 JSON 失败：{e}")

    def save_to_csv(self, filename: str = "movies.csv"):
        """
        将影片信息保存为 CSV 文件

        Args:
            filename: 输出文件名
        """
        import csv

        output_path = self.output_dir / filename
        try:
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["code", "title", "id", "cover_url"])
                writer.writeheader()
                writer.writerows(self.movies)
            logger.info(f"已保存到 {output_path}")
        except Exception as e:
            logger.error(f"保存 CSV 失败：{e}")

    def print_summary(self):
        """打印抓取摘要"""
        logger.info("\n" + "=" * 60)
        logger.info("抓取摘要")
        logger.info("=" * 60)
        logger.info(f"总影片数：{len(self.movies)}")

        if self.movies:
            logger.info("\n前 5 部影片：")
            for i, movie in enumerate(self.movies[:5], 1):
                logger.info(f"  {i}. [{movie['code']}] {movie['title']}")
                logger.info(f"     ID: {movie['id']}")
                logger.info(f"     封面: {movie['cover_url']}")

        logger.info("=" * 60 + "\n")


async def main():
    """主函数"""
    # 从环境变量读取配置
    proxy_enabled = os.getenv("PROXY_ENABLED", "False").lower() == "true"
    proxy = os.getenv("PROXY", None) if proxy_enabled else None

    if proxy:
        logger.info(f"使用代理：{proxy}")
    else:
        logger.info("未配置代理")

    # 创建爬虫实例
    spider = JAVLibrarySpider(
        output_dir=Path(__file__).parent / "output",
        proxy=proxy,
    )

    # 爬取（可以指定最多页数，如：max_pages=2）
    await spider.crawl(max_pages=None)

    # 保存结果
    spider.save_to_json("javlibrary_movies.json")
    spider.save_to_csv("javlibrary_movies.csv")

    # 打印摘要
    spider.print_summary()


if __name__ == "__main__":
    asyncio.run(main())
