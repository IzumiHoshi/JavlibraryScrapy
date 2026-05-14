from seleniumbase import BaseCase
from seleniumbase import config as sb_config
import re
from utils.car import javbuscar
from pathlib import Path
from utils.fanart import split_poster_from_fanart
import os
import logging
from dotenv import load_dotenv
from utils.filesave import write_xml, strip_text, split_text, rename
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

load_dotenv()

BaseCase.main(__name__, __file__)


class JavbusScraper(BaseCase):

    def setUp(self):
        sb_config.headless = os.getenv("HEADLESS", "True").lower() == "true"

        proxy_enabled = os.getenv("PROXY_ENABLED", "False").lower() == "true"
        if proxy_enabled:
            logging.info(f'启用代理...{os.getenv("PROXY", "")}')
            sb_config.proxy = os.getenv("PROXY", "")

        self.javbus_url = os.getenv("JAVBUS_URL", "https://www.javbus.com/")
        if not self.javbus_url.endswith("/"):
            self.javbus_url += "/"

        super().setUp()

    def download(self, selector: str, output_path: Path):
        """通过 seleniumbase 对页面图片元素截图保存，绕过反扒"""
        # 查找页面上对应的图片元素并截图保存
        # 只处理封面图片，假设页面已打开且图片已加载
        # selector = "div.movie > div.screencap > a > img"
        if output_path.exists():
            logging.info(f"文件 {output_path.name} 已存在，跳过下载。")
            return output_path
        try:
            self.save_element_as_image_file(selector, str(output_path))
            logging.info(f"已截图保存图片：{output_path.name}")
            return output_path
        except Exception as e:
            logging.error(f"截图保存图片失败：{e}")
            return None

    def fetch(self, carid: str, temp_path: str):
        url = f"{self.javbus_url}{carid}"
        self.open(url)
        title = self.get_text("div.container > h3")
        # 去掉车牌部分
        title = title.replace(carid, "").strip()
        # 去掉\/,只保留\/前的部分
        title = title.split("\\")[0].strip()
        title = title.split("/")[0].strip()
        # 下载封面图片
        cover = self.download(
            "div.movie > div.screencap > a > img", temp_path / f"{carid}.png"
        )
        # cover = self.get_attribute("div.movie > div.screencap > a > img", "src")
        release_date = self.get_text("div.info > p:nth-child(2)")
        director = self.get_text("div.info > p:nth-child(4)")
        producer = self.get_text("div.info > p:nth-child(5)")
        publisher = self.get_text("div.info > p:nth-child(6)")
        category = self.get_text("div.info > p:nth-child(8)")
        try:
            actors = self.get_text("div.info > p:last-child", timeout=5)
        except Exception as e:
            logging.warning(f"获取演员信息失败：{e}")
            actors = ""

        logging.info(f"页面标题：{title}")
        logging.info(f"封面：{cover}")
        logging.info(f"發行日期：{strip_text(release_date)}")
        logging.info(f"導演：{strip_text(director)}")
        logging.info(f"製作商：{strip_text(producer)}")
        logging.info(f"發行商：{strip_text(publisher)}")
        logging.info(f"類別：{split_text(category)}")
        logging.info(f"演員：{split_text(actors)}")
        return {
            "title": title,
            "carid": carid,
            "cover": cover,
            "release_date": release_date,
            "director": director,
            "producer": producer,
            "publisher": publisher,
            "category": category,
            "actors": actors,
        }

    def test_get_movie_info(self):
        logging.info("开始查找车牌...")
        root_dir = input("请输入视频目录路径：").strip()
        root_dir = Path(root_dir).resolve()  # 确保路径是绝对路径
        cars = javbuscar(root_dir)  # 替换为实际的视频目录路径
        carinfo = []
        for carid, path in cars:
            logging.info(f"车牌：{carid}, 路径：{path}")
            try:
                info = self.fetch(carid, root_dir)
                if not info:
                    logging.warning(f"未找到车牌 {carid} 的信息。")
                    continue
            except Exception as e:
                logging.error(f"发生错误：{e} : {carid}")
                continue
            # 处理获取到的信息·
            info["path"] = path  # 添加视频文件路径

            carinfo.append(info)

        for info in carinfo:
            filename_prefix = f"{info['carid']} {info['title'].strip()}"
            save_dir = root_dir / filename_prefix
            save_dir.mkdir(parents=True, exist_ok=True)

            # 重命名视频文件
            video_path = Path(info["path"])
            if video_path.exists():
                new_video_name = f"{filename_prefix}{video_path.suffix}"
                new_video_path = save_dir / new_video_name
                rename(video_path, new_video_path)

            nfo_filename = save_dir / f"{filename_prefix}.nfo"
            write_xml(nfo_filename, info)

            cover = info.get("cover", "")
            if cover:
                cover_filename = save_dir / f"{filename_prefix}-fanart.png"
                rename(cover, cover_filename)
                split_poster_from_fanart(
                    cover_filename, save_dir / f"{filename_prefix}-poster.png"
                )
                # logging.info(f"已下载封面：{cover_filename}")

        input("请按 Enter 键继续...")  # 等待用户输入以查看结果
