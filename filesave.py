import re
from xml.sax.saxutils import escape
from pathlib import Path


def strip_text(text):
    if ":" in text:
        return text.split(":")[1].strip()
    return text


def split_text(text):
    if " " in text:
        return [item.strip() for item in text.split(" ")]
    return [text.strip()]


def write_xml(nfo_filename: str, content: dict):
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



def rename(old_name: Path, new_name: Path):
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
    try:
        old_name.rename(new_name)
        print(f"已将 {old_name.name} 重命名为 {new_name.name}")
    except Exception as e:
        print(f"重命名文件失败：{e}")
