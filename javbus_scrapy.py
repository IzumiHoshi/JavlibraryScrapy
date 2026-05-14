"""
使用 Scrapy 框架重写的 JAVBus 爬虫
功能：从 JAVBus 网站爬取视频信息并生成 NFO 文件
"""

import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.utils.log import configure_logging
import logging
from pathlib import Path
import os
from dotenv import load_dotenv
from filesave import write_xml, strip_text, split_text, rename
from car import javbuscar
from utils import split_poster_from_fanart
from PIL import Image
import io
import requests

load_dotenv()

configure_logging({'LOG_LEVEL': 'INFO'})
logger = logging.getLogger(__name__)


class JavbusSpider(scrapy.Spider):
    """JAVBus 爬虫"""
    
    name = 'javbus'
    allowed_domains = ['javbus.com']
    
    def __init__(self, car_list=None, root_dir=None, *args, **kwargs):
        """
        初始化爬虫
        
        Args:
            car_list: 要爬取的车牌列表，格式为 [(car_id, video_path), ...]
            root_dir: 根目录路径
        """
        super(JavbusSpider, self).__init__(*args, **kwargs)
        self.car_list = car_list or []
        self.root_dir = Path(root_dir) if root_dir else Path.cwd()
        self.javbus_url = os.getenv("JAVBUS_URL", "https://www.javbus.com/")
        if not self.javbus_url.endswith("/"):
            self.javbus_url += "/"
        self.movie_info_list = []
    
    def start_requests(self):
        """生成初始请求"""
        for car_id, video_path in self.car_list:
            url = f"{self.javbus_url}{car_id}"
            yield scrapy.Request(
                url,
                callback=self.parse,
                meta={'car_id': car_id, 'video_path': video_path},
                errback=self.errback_httpbin
            )
    
    def errback_httpbin(self, failure):
        """处理请求失败"""
        car_id = failure.request.meta.get('car_id', 'unknown')
        logger.error(f"请求失败 - 车牌: {car_id}, 错误: {failure.value}")
    
    def parse(self, response):
        """解析电影页面"""
        car_id = response.meta['car_id']
        video_path = response.meta['video_path']
        
        try:
            # 提取标题
            title = response.css("div.container > h3::text").get()
            if title:
                title = title.replace(car_id, "").strip()
                title = title.split("\\")[0].strip()
                title = title.split("/")[0].strip()
            else:
                title = ""
            
            # 提取发行日期
            release_date_text = response.css("div.info > p:nth-child(2)::text").get()
            release_date = release_date_text if release_date_text else ""
            
            # 提取导演
            director_text = response.css("div.info > p:nth-child(4)::text").get()
            director = director_text if director_text else ""
            
            # 提取制作商
            producer_text = response.css("div.info > p:nth-child(5)::text").get()
            producer = producer_text if producer_text else ""
            
            # 提取发行商
            publisher_text = response.css("div.info > p:nth-child(6)::text").get()
            publisher = publisher_text if publisher_text else ""
            
            # 提取类别
            category_text = response.css("div.info > p:nth-child(8)::text").get()
            category = category_text if category_text else ""
            
            # 提取演员
            actors_text = response.css("div.info > p:last-child::text").get()
            actors = actors_text if actors_text else ""
            
            # 下载封面图片
            cover_img_url = response.css("div.movie > div.screencap > a > img::attr(src)").get()
            cover_path = None
            
            if cover_img_url:
                cover_path = self.download_cover(cover_img_url, car_id)
            
            logger.info(f"页面标题：{title}")
            logger.info(f"發行日期：{strip_text(release_date)}")
            logger.info(f"導演：{strip_text(director)}")
            logger.info(f"製作商：{strip_text(producer)}")
            logger.info(f"發行商：{strip_text(publisher)}")
            logger.info(f"類別：{split_text(category)}")
            logger.info(f"演員：{split_text(actors)}")
            
            movie_info = {
                "title": title,
                "carid": car_id,
                "cover": cover_path,
                "release_date": release_date,
                "director": director,
                "producer": producer,
                "publisher": publisher,
                "category": category,
                "actors": actors,
                "path": video_path
            }
            
            self.movie_info_list.append(movie_info)
            
            yield movie_info
            
        except Exception as e:
            logger.error(f"解析页面失败 - 车牌: {car_id}, 错误: {e}")
    
    def download_cover(self, img_url, car_id):
        """
        下载封面图片
        
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
            
            # 添加 headers 以模拟浏览器请求
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(img_url, headers=headers, timeout=10)
            response.raise_for_status()
            
            # 保存图片
            with open(temp_path, 'wb') as f:
                f.write(response.content)
            
            logger.info(f"已下载封面：{temp_path.name}")
            return temp_path
            
        except Exception as e:
            logger.error(f"下载封面失败 - 车牌: {car_id}, 错误: {e}")
            return None
    
    def closed(self, reason):
        """爬虫关闭时的处理"""
        logger.info(f"爬虫关闭，原因: {reason}")
        logger.info(f"共爬取 {len(self.movie_info_list)} 部电影")
        
        # 处理爬取的信息
        self.process_movie_info()
    
    def process_movie_info(self):
        """处理爬取的电影信息"""
        for info in self.movie_info_list:
            try:
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
                    cover_filename = save_dir / f"{filename_prefix}-fanart.png"
                    rename(cover, cover_filename)
                    split_poster_from_fanart(
                        cover_filename, 
                        save_dir / f"{filename_prefix}-poster.png"
                    )
                    
            except Exception as e:
                logger.error(f"处理电影信息失败 - 车牌: {info['carid']}, 错误: {e}")


class JavbusScraperRunner:
    """JAVBus 爬虫运行器"""
    
    def __init__(self):
        self.process = None
    
    def run(self, root_dir):
        """
        运行爬虫
        
        Args:
            root_dir: 视频目录路径
        """
        root_dir = Path(root_dir).resolve()
        
        # 查找车牌
        logger.info("开始查找车牌...")
        cars = javbuscar(root_dir)
        
        if not cars:
            logger.warning("未找到任何车牌")
            return
        
        logger.info(f"找到 {len(cars)} 个车牌")
        for car_id, path in cars:
            logger.info(f"车牌：{car_id}, 路径：{path}")
        
        # 配置爬虫
        custom_settings = {
            'ROBOTSTXT_OBEY': False,
            'CONCURRENT_REQUESTS': 1,
            'DOWNLOAD_DELAY': 2,
            'COOKIES_ENABLED': True,
            'USER_AGENT': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'LOG_LEVEL': 'INFO'
        }
        
        # 配置代理
        proxy_enabled = os.getenv("PROXY_ENABLED", "False").lower() == "true"
        if proxy_enabled:
            proxy_url = os.getenv("PROXY", "")
            logger.info(f'启用代理...{proxy_url}')
            custom_settings['PROXY'] = proxy_url
        
        # 创建爬虫进程
        self.process = CrawlerProcess(custom_settings)
        self.process.crawl(
            JavbusSpider,
            car_list=cars,
            root_dir=root_dir
        )
        
        # 运行爬虫
        self.process.start()


def main():
    """主函数"""
    root_dir = input("请输入视频目录路径：").strip()
    
    if not Path(root_dir).exists():
        logger.error(f"目录 {root_dir} 不存在")
        return
    
    runner = JavbusScraperRunner()
    runner.run(root_dir)
    
    input("请按 Enter 键继续...")


if __name__ == "__main__":
    main()
