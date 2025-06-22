from seleniumbase import BaseCase
import os
import re
from xml.sax.saxutils import escape
from car import javbuscar
from pathlib import Path

BaseCase.main(__name__, __file__)


def strip_text(text):
    if ":" in text:
        return text.split(":")[1].strip()
    return text


def split_text(text):
    if " " in text:
        return [item.strip() for item in text.split(" ")]
    return [text.strip()]


class JavbusScraper(BaseCase):

    root_dir = ""

    @classmethod
    def setUpPath(cls, root_dir: str):
        cls.root_dir = root_dir

    def setUp(self):
        self.headless = True
        super().setUp()

    def write_xml(self, nfo_filename: str, content: dict):
        """将内容写入指定的 NFO 文件"""
        title = content.get("title", "")
        carid = content.get("carid", "")
        director = content.get("director", "")
        release_date = content.get("release_date", "")
        producer = content.get("producer", "")
        category = content.get("category", "")
        actors = content.get("actors", "")
        if not title or not carid:
            print("标题或车牌不能为空，无法生成 NFO 文件。")
            return
        # 解析字段
        nfo_title = title.strip()
        nfo_originaltitle = nfo_title
        nfo_director = strip_text(director)
        nfo_year = ""
        nfo_premiered = ""
        date_str = strip_text(release_date)
        # 尝试提取年份
        match = re.match(r"(\d{4})[-/](\d{2})[-/](\d{2})", date_str)
        if match:
            nfo_year = match.group(1)
            nfo_premiered = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        else:
            nfo_year = ""
            nfo_premiered = date_str
        nfo_release = nfo_premiered
        nfo_studio = strip_text(producer)
        nfo_id = carid
        nfo_num = carid
        nfo_genres = split_text(category)
        nfo_tags = nfo_genres
        nfo_actors = split_text(actors)
        nfo_plot = ""
        nfo_mpaa = "NC-17"
        nfo_customrating = "NC-17"
        nfo_countrycode = "JP"
        nfo_country = "日本"

        # 构建 nfo xml 内容
        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            "<movie>",
            f"  <plot>{escape(nfo_plot)}</plot>",
            f"  <title>{escape(nfo_title)}</title>",
            f"  <originaltitle>{escape(nfo_originaltitle)}</originaltitle>",
            f"  <director>{escape(nfo_director)}</director>",
            f"  <year>{escape(nfo_year)}</year>",
            f"  <mpaa>{nfo_mpaa}</mpaa>",
            f"  <customrating>{nfo_customrating}</customrating>",
            f"  <countrycode>{nfo_countrycode}</countrycode>",
            f"  <premiered>{escape(nfo_premiered)}</premiered>",
            f"  <release>{escape(nfo_release)}</release>",
            f"  <country>{escape(nfo_country)}</country>",
            f"  <studio>{escape(nfo_studio)}</studio>",
            f"  <id>{escape(nfo_id)}</id>",
            f"  <num>{escape(nfo_num)}</num>",
        ]
        # 类别
        for g in nfo_genres:
            xml_lines.append(f"  <genre>{escape(g)}</genre>")
        for t in nfo_tags:
            xml_lines.append(f"  <tag>{escape(t)}</tag>")
        # 演员
        for actor in nfo_actors:
            xml_lines.append("  <actor>")
            xml_lines.append(f"    <name>{escape(actor)}</name>")
            xml_lines.append("    <type>Actor</type>")
            xml_lines.append("  </actor>")
        xml_lines.append("</movie>")

        nfo_content = "\n".join(xml_lines)
        # nfo_filename = os.path.join(
        #     os.path.dirname(__file__), f"{carid} {nfo_title}.nfo"
        # )
        with open(nfo_filename, "w", encoding="utf-8") as f:
            f.write(nfo_content)
        print(f"已写入 nfo 文件：{nfo_filename}")

    def rename(self, old_name: Path, new_name: Path):
        """重命名文件"""
        if not old_name.exists():
            print(f"文件 {old_name} 不存在，无法重命名。")
            return
        if old_name == new_name:
            print(f"文件 {old_name} 已经是目标名称，无需重命名。")
            return
        if new_name.exists():
            print(f"目标文件 {new_name} 已存在，无法重命名。")
            return
        old_name.rename(new_name)
        print(f"已将 {old_name.name} 重命名为 {new_name.name}")

    def download(self, selector: str, output_path: Path):
        """通过 seleniumbase 对页面图片元素截图保存，绕过反扒"""
        # 查找页面上对应的图片元素并截图保存
        # 只处理封面图片，假设页面已打开且图片已加载
        # selector = "div.movie > div.screencap > a > img"
        if output_path.exists():
            print(f"文件 {output_path.name} 已存在，跳过下载。")
            return output_path
        try:
            self.save_element_as_image_file(selector, str(output_path))
            print(f"已截图保存图片：{output_path.name}")
            return output_path
        except Exception as e:
            print(f"截图保存图片失败：{e}")
            return None

    def fetch(self, carid: str, temp_path: str):
        url = "https://www.buscdn.ink/{carid}".format(carid=carid)
        self.open(url)
        title = self.get_text("div.container > h3")
        # 去掉车牌部分
        title = title.replace(carid, "").strip()
        # 去掉\/,只保留\/前的部分
        title = title.split("\\")[0].strip()
        title = title.split("/")[0].strip()
        cover = self.download(
            "div.movie > div.screencap > a > img", temp_path / f"{carid}.png"
        )  # 下载封面图片
        # cover = self.get_attribute("div.movie > div.screencap > a > img", "src")
        release_date = self.get_text("div.info > p:nth-child(2)")
        director = self.get_text("div.info > p:nth-child(4)")
        producer = self.get_text("div.info > p:nth-child(5)")
        publisher = self.get_text("div.info > p:nth-child(6)")
        category = self.get_text("div.info > p:nth-child(8)")
        try:
            actors = self.get_text("div.info > p:last-child", timeout=5)
        except Exception as e:
            print(f"获取演员信息失败：{e}")
            actors = ""

        print("页面标题：", title)
        print("封面：", cover)
        print("發行日期：", strip_text(release_date))
        print("導演：", strip_text(director))
        print("製作商：", strip_text(producer))
        print("發行商：", strip_text(publisher))
        print("類別：", split_text(category))
        print("演員：", split_text(actors))
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
        print("开始查找车牌...")
        root_dir = input("请输入视频目录路径：").strip()
        root_dir = Path(root_dir).resolve()  # 确保路径是绝对路径
        cars = javbuscar(root_dir)  # 替换为实际的视频目录路径
        carinfo = []
        for carid, path in cars:
            print(f"车牌：{carid}, 路径：{path}")
            try:
                info = self.fetch(carid, root_dir)
                if not info:
                    print(f"未找到车牌 {carid} 的信息。")
                    continue
            except Exception as e:
                print(f"发生错误：{e} : {carid}")
                continue
            # 处理获取到的信息
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
                self.rename(video_path, new_video_path)

            nfo_filename = save_dir / f"{filename_prefix}.nfo"
            self.write_xml(nfo_filename, info)

            cover = info.get("cover", "")
            if cover:
                cover_filename = save_dir / f"{filename_prefix}-fanart.png"
                self.rename(cover, cover_filename)
                # print(f"已下载封面：{cover_filename}")

        input("请按 Enter 键继续...")  # 等待用户输入以查看结果
