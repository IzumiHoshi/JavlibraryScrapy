from seleniumbase import BaseCase
import os
import re
from xml.sax.saxutils import escape
from car import javbuscar


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

    def rename(self, output_path: str, old_name: str, new_name: str):
        """重命名文件"""
        if not os.path.exists(output_path):
            print(f"文件 {output_path} 不存在，无法重命名。")
            return
        new_path = os.path.join(os.path.dirname(output_path), new_name)
        os.rename(output_path, new_path)
        print(f"已将 {output_path} 重命名为 {new_path}")

    def test_get_movie_info(self):
        print("开始查找车牌...")
        root_dir = input("请输入视频目录路径：").strip()
        carinfo = javbuscar(root_dir)  # 替换为实际的视频目录路径
        for carid, path in carinfo:
            url = "https://www.javbus.com/{carid}".format(carid=carid)
            self.open(url)
            title = self.get_text("div.container > h3")
            cover = self.get_attribute("div.movie > div.screencap > a > img", "src")
            release_date = self.get_text("div.info > p:nth-child(2)")
            director = self.get_text("div.info > p:nth-child(4)")
            producer = self.get_text("div.info > p:nth-child(5)")
            publisher = self.get_text("div.info > p:nth-child(6)")
            category = self.get_text("div.info > p:nth-child(8)")
            actors = self.get_text("div.info > p:last-child")

            print("页面标题：", title.replace(carid, "").strip())
            print("封面：", cover)
            print("發行日期：", strip_text(release_date))
            print("導演：", strip_text(director))
            print("製作商：", strip_text(producer))
            print("發行商：", strip_text(publisher))
            print("類別：", split_text(category))
            print("演員：", split_text(actors))

            carinfo.append(
                {
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
            )

        for info in carinfo:
            nfo_filename = os.path.join(
                root_dir,
                f"{info['carid']} {info['title'].strip()}.nfo",
            )
            self.write_xml(nfo_filename, info)

        # input("请按 Enter 键继续...")  # 等待用户输入以查看结果
