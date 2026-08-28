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
from scrapling.fetchers import FetcherSession

# AsyncDynamicSession → FetcherSession 适配层
#
# JAVLibrary (c99i.com 镜像) 详情页是纯 server-rendered HTML，无 JS 渲染、无
# Cloudflare challenge。curl_cffi 后端的 FetcherSession 1.8s/页就能拿到完整
# 内容，不需要拉起 Chromium。调用方代码（fetch_page / get_page_count）只
# 用到 session.fetch() 这一个方法，做个 async shim 即可保留全部既有签名。
#
# 适配层选 FetcherSession 而非裸 Fetcher：保持 HTTP 连接复用、cookie 跨请
# 求延续。绕开 curl_cffi 0.16.0 "Cookie 在 jar 里但不发送" 的已知 bug——
# 实测 JAVLibrary 无 verify 流程，根本不依赖 cookie 跨请求，所以这个 bug 不
# 影响本爬虫。JAVBus 仍走 AsyncDynamicSession（必须浏览器过 verify）。
class _AsyncFetcherSessionShim:
    """把同步 FetcherSession 包成 ``await session.fetch(url, headers=...)``。

    与原 AsyncDynamicSession 接口兼容的唯一目的是：本文件 fetch_page /
    get_page_count 不用改一行；crawl() 的 async with 语法兼容；外层
    asyncio.run(main()) 也不动。

    scrapling 的 FetcherSession 在 __enter__ 前是配置壳（无 get/post），
    __enter__ 后才返回真正的 _SyncSessionLogic（带 get/post）。shim 在
    __aenter__ 时构造并 enter 内部 session，让外部 async with 行为保持
    跟 AsyncDynamicSession 一致。
    """

    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self._sess: Optional[object] = None

    async def __aenter__(self):
        # FetcherSession() 是 context manager；__enter__ 返回 _SyncSessionLogic
        self._sess = FetcherSession(**self._kwargs).__enter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._sess is not None:
            self._sess.__exit__(exc_type, exc_val, exc_tb)
            self._sess = None

    async def fetch(self, url: str, headers: Optional[dict] = None, **kwargs):
        # 同步调用放线程池，避免阻塞事件循环
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._sess.get(url, headers=headers or {}, **kwargs),
        )

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class JAVLibrarySpider:
    """JAVLibrary 爬虫 - 爬取最想要的影片列表"""

    def __init__(
        self,
        base_url: str = "https://www.c99i.com/cn/vl_mostwanted.php",
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
        self, session: _AsyncFetcherSessionShim, page: int = 1
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

    async def get_page_count(self, session: _AsyncFetcherSessionShim) -> int:
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
            # 使用 curl_cffi 后端的 FetcherSession：JAVLibrary 镜像无 JS / 无 CF，
            # 不需要 Chromium。impersonate='chrome' 让 TLS 指纹看着像真浏览器。
            async with _AsyncFetcherSessionShim(
                impersonate="chrome",
                stealthy_headers=True,  # 生成真实浏览器 headers（顺带把 Referer 设成 Google）
                proxy=self.proxy,
                timeout=90,  # 网络盘偶尔慢
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
    # 代理：JAVLibrary 镜像独立开关（PROXY_JAVLIBRARY_ENABLED + PROXY）
    proxy_enabled = os.getenv("PROXY_JAVLIBRARY_ENABLED", "False").lower() == "true"
    proxy = os.getenv("PROXY", None) if proxy_enabled else None

    if proxy:
        logger.info(f"使用代理：{proxy}")
    else:
        logger.info("未配置代理")

    # 输出目录优先级：MOSTWANTED_INDEX（直接控制 JSON/CSV 落点）
    # > MOSTWANTED_LIBRARY_ROOT（库根目录，JSON/CSV 与影片文件夹同根）
    # > 项目内 output/（保持旧行为）。
    mw_index = os.getenv("MOSTWANTED_INDEX", "").strip()
    mw_root = os.getenv("MOSTWANTED_LIBRARY_ROOT", "").strip()
    if mw_index:
        mw_index_path = Path(mw_index)
        output_dir = mw_index_path.parent
        json_filename = mw_index_path.name
    elif mw_root:
        output_dir = Path(mw_root)
        json_filename = "javlibrary_movies.json"
    else:
        output_dir = Path(__file__).parent / "output"
        json_filename = "javlibrary_movies.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    if mw_index:
        logger.info(f"MOSTWANTED_INDEX={mw_index}，输出落到 {output_dir / json_filename}")
    elif mw_root:
        logger.info(f"MOSTWANTED_LIBRARY_ROOT={mw_root}，输出落到 {output_dir}")
    else:
        logger.info(f"输出目录：{output_dir}")

    # 入口 URL：默认 c99i.com 镜像；切换镜像或换回 javlibrary.com 原站时改 .env 的 JAVLIBRARY_URL
    javlibrary_url = os.getenv(
        "JAVLIBRARY_URL", "https://www.c99i.com/cn/vl_mostwanted.php"
    )

    # 创建爬虫实例
    spider = JAVLibrarySpider(
        output_dir=output_dir,
        base_url=javlibrary_url,
        proxy=proxy,
    )

    # 爬取（可以指定最多页数，如：max_pages=2）
    await spider.crawl(max_pages=2)

    # 保存结果（文件名取 MOSTWANTED_INDEX 的 basename 或默认）
    spider.save_to_json(json_filename)
    spider.save_to_csv("javlibrary_movies.csv")

    # 打印摘要
    spider.print_summary()


if __name__ == "__main__":
    asyncio.run(main())
