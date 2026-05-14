"""
使用 Scrapling 框架重写的 JAVBus 爬虫
Scrapling: https://github.com/D4Vinci/Scrapling
功能：从 JAVBus 网站爬取视频信息并生成 NFO 文件
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
import os
from dotenv import load_dotenv
from scrapling.fetchers import AsyncDynamicSession
import asyncio
import requests

from utils.filesave import write_xml, strip_text, split_text, rename
from utils.car import javbuscar
from utils.fanart import split_poster_from_fanart

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class JavbusSpider:
    """
    使用 Scrapling AsyncDynamicSession 的 JAVBus 爬虫

    示例：
        spider = JavbusSpider(root_dir=Path('D:\\Videos'))
        asyncio.run(spider.crawl_and_process(car_list))
    """

    def __init__(self, root_dir: Optional[Path] = None):
        """
        初始化爬虫

        Args:
            root_dir: 根目录路径
        """
        self.root_dir = Path(root_dir) if root_dir else Path.cwd()
        self.javbus_url = os.getenv("JAVBUS_URL", "https://www.javbus.com/")
        if not self.javbus_url.endswith("/"):
            self.javbus_url += "/"

        self.movie_info_list = []
        self.proxy_enabled = os.getenv("PROXY_ENABLED", "False").lower() == "true"
        self.proxy = os.getenv("PROXY", None) if self.proxy_enabled else None

    async def parse(self, response) -> Dict[str, Any]:
        """
        解析电影页面

        Args:
            response: Scrapling Response 对象（继承自 Selector）

        Returns:
            电影信息字典
        """
        try:
            # 从 URL 提取车牌 ID
            car_id = response.url.rstrip("/").split("/")[-1]

            # # 保存 HTML 到文件用于 debug
            # debug_file = self.root_dir / f"{car_id}_debug.html"
            # try:
            #     # 获取实际的 HTML 内容
            #     if hasattr(response, 'html'):
            #         # Response 对象可能有 html 属性
            #         html_content = response.html
            #     elif hasattr(response, 'body'):
            #         # 或者有 body 属性（字节形式）
            #         html_content = response.body.decode('utf-8', errors='ignore') if isinstance(response.body, bytes) else response.body
            #     else:
            #         # 最后尝试直接转换为字符串
            #         html_content = response.text if hasattr(response, 'text') else str(response.get())

            #     with open(debug_file, 'w', encoding='utf-8') as f:
            #         f.write(html_content)
            #     logger.info(f"已保存调试 HTML 文件：{debug_file}")
            # except Exception as debug_err:
            #     logger.warning(f"无法保存调试文件：{debug_err}")

            # 提取页面标题
            title = response.css("div.container > h3::text").get()
            if title:
                title = title.replace(car_id, "").strip()
                title = title.split("\\")[0].strip()
                title = title.split("/")[0].strip()
            else:
                title = ""

            # 在 info div 中提取元数据
            info_section = response.css("div.info")

            # 提取发行日期
            release_date = ""
            for p in info_section.css("p"):
                header_text = p.css("span.header::text").get()
                if header_text and "發行日期" in header_text:
                    # 获取 span.header 后面的文本节点
                    all_text = "".join(p.css("::text").getall())
                    release_date = all_text.replace("發行日期:", "").strip()
                    break

            # 提取制作商（在 <a> 标签中）
            producer = ""
            for p in info_section.css("p"):
                header_text = p.css("span.header::text").get()
                if header_text and "製作商" in header_text:
                    producer = p.css("a::text").get() or ""
                    break

            # 提取发行商
            publisher = ""
            for p in info_section.css("p"):
                header_text = p.css("span.header::text").get()
                if header_text and "發行商" in header_text:
                    publisher = p.css("a::text").get() or ""
                    break

            # 提取类别 - 从 <a> 标签获取所有类别
            category_links = info_section.css("span.genre > label > a::text").getall()
            # 过滤掉空值和清理数据
            category_links = [c.strip() for c in category_links if c.strip()]
            category = " / ".join(category_links) if category_links else ""

            # 提取演员 - 查找 star-name 或直接在 genre 中查找
            actor_links = info_section.css("span.genre > a::text").getall()
            actors = " / ".join(actor_links) if actor_links else ""

            # 获取封面图片 URL，处理相对路径
            cover_img_url = response.css(
                "div.movie > div.screencap > a > img::attr(src)"
            ).get()
            cover_path = None

            if cover_img_url:
                # 如果是相对 URL，转换为绝对 URL
                if not cover_img_url.startswith("http"):
                    if cover_img_url.startswith("/"):
                        base_url = "https://www.javbus.com"
                        cover_img_url = base_url + cover_img_url
                    else:
                        base_url = "https://www.javbus.com/"
                        cover_img_url = base_url + cover_img_url

                cover_path = await self.download_cover(cover_img_url, car_id)

            logger.info(f"页面标题：{title}")
            logger.info(f"發行日期：{strip_text(release_date)}")
            logger.info(f"製作商：{strip_text(producer)}")
            logger.info(f"發行商：{strip_text(publisher)}")
            logger.info(f"類別：{split_text(category)}")
            logger.info(f"演員：{split_text(actors)}")

            movie_info = {
                "title": title,
                "carid": car_id,
                "cover": cover_path,
                "release_date": release_date,
                "director": producer,  # 使用 producer 作为 director
                "producer": producer,
                "publisher": publisher,
                "category": category,
                "actors": actors,
            }

            return movie_info

        except Exception as e:
            logger.error(f"解析页面失败：{e}")
            return {}

    async def download_cover(self, img_url: str, car_id: str) -> Optional[Path]:
        """
        异步下载封面图片

        Args:
            img_url: 图片 URL
            car_id: 车牌 ID

        Returns:
            保存的图片路径或 None
        """
        try:
            temp_path = self.root_dir / f"{car_id}.png"

            if temp_path.exists():
                logger.info(f"文件 {temp_path.name} 已存在，跳过下载。")
                return temp_path

            # 添加完整的请求头，包含 Referer，否则 JAVBus 会返回 403
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": f"{self.javbus_url}{car_id}",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }

            # 使用 requests 进行同步下载，支持代理
            response = requests.get(
                img_url,
                headers=headers,
                timeout=10,
                proxies=(
                    {"http": self.proxy, "https": self.proxy} if self.proxy else None
                ),
                verify=False,
            )
            response.raise_for_status()

            # 保存图片
            with open(temp_path, "wb") as f:
                f.write(response.content)

            logger.info(f"已下载封面：{temp_path.name}")
            return temp_path

        except Exception as e:
            logger.error(f"下载封面失败 - URL: {img_url}, 错误: {e}")
            return None

    async def crawl_and_process(self, car_list: List[tuple]):
        """
        爬取并处理电影信息，使用会话保持浏览器连接

        Args:
            car_list: 车牌列表，格式为 [(car_id, video_path), ...]
        """
        # 使用 AsyncDynamicSession 保持浏览器会话
        async with AsyncDynamicSession(
            load_dom=True,
            network_idle=True,
            disable_resources=True,
            proxy=self.proxy,
            headless=True,
            timeout=30000,
        ) as session:
            for car_id, video_path in car_list:
                url = f"{self.javbus_url}{car_id}"
                logger.info(f"正在爬取：{url}")

                try:
                    # 使用会话获取页面
                    page = await session.fetch(url)

                    # 解析页面
                    movie_info = await self.parse(page)
                    movie_info["path"] = video_path

                    self.movie_info_list.append(movie_info)

                    # 处理电影信息
                    await self.process_movie(movie_info)

                except Exception as e:
                    logger.error(f"爬取失败 - 车牌: {car_id}, 错误: {e}")
                    continue

    async def process_movie(self, info: Dict[str, Any]):
        """
        处理单个电影的信息和文件

        Args:
            info: 电影信息字典
        """
        try:
            if not info.get("title") or not info.get("carid"):
                logger.warning("标题或车牌为空，跳过处理")
                return

            filename_prefix = f"{info['carid']} {info['title'].strip()}"
            save_dir = self.root_dir / filename_prefix
            save_dir.mkdir(parents=True, exist_ok=True)

            # 重命名视频文件
            video_path = Path(info["path"])
            if video_path.exists():
                new_video_name = f"{filename_prefix}{video_path.suffix}"
                new_video_path = save_dir / new_video_name
                rename(video_path, new_video_path)

            # 生成 NFO 文件
            nfo_filename = save_dir / f"{filename_prefix}.nfo"
            write_xml(nfo_filename, info)

            # 处理封面图片
            cover = info.get("cover", "")
            if cover:
                cover_filename = save_dir / f"fanart.png"
                rename(cover, cover_filename)
                split_poster_from_fanart(cover_filename, save_dir / f"poster.png")

            logger.info(f"完成处理：{filename_prefix}")

        except Exception as e:
            logger.error(
                f"处理电影失败 - 车牌: {info.get('carid', 'unknown')}, 错误: {e}"
            )


async def main():
    """主函数"""
    root_dir = input("请输入视频目录路径：").strip()
    root_dir = Path(root_dir).resolve()

    if not root_dir.exists():
        logger.error(f"目录 {root_dir} 不存在")
        return

    logger.info("开始查找车牌...")
    cars = javbuscar(root_dir)

    if not cars:
        logger.warning("未找到任何车牌")
        return

    logger.info(f"找到 {len(cars)} 个车牌")
    for car_id, path in cars:
        logger.info(f"车牌：{car_id}, 路径：{path}")

    # 创建爬虫实例
    spider = JavbusSpider(root_dir=root_dir)

    # 配置 User-Agent 和代理
    proxy_enabled = os.getenv("PROXY_ENABLED", "False").lower() == "true"
    if proxy_enabled:
        proxy_url = os.getenv("PROXY", "")
        logger.info(f"启用代理...{proxy_url}")
        # Scrapling 会自动处理代理

    # 爬取并处理
    await spider.crawl_and_process(cars)

    logger.info(f"共处理 {len(spider.movie_info_list)} 部电影")
    input("请按 Enter 键继续...")


if __name__ == "__main__":
    asyncio.run(main())
