"""
Author: izumihoshi
Date: 2025-06-23 23:52:19
LastEditors: izumihoshi
LastEditTime: 2025-09-23 17:51:02
FilePath: /JavlibraryScrapy/utils.py
Description: TODO

Copyright (c) 2025 by Honor, All Rights Reserved.
"""

# coding: utf-8 -*-

from pathlib import Path
from PIL import Image
from typing import Tuple, List
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


def split_poster_from_fanart(fanart_path: Path, poster_path: Path) -> None:
    """
    从 fanart 图片中分离出 poster 图片并保存
    poster 图片位于 fanart 的右半部分,比例为5:7
    """
    try:
        fanart = Image.open(fanart_path)
        width, height = fanart.size
        poster_width = int(height * 5 / 7)
        poster = fanart.crop((width - poster_width, 0, width, height))
        poster.save(poster_path)
        logging.info(f"已保存 poster 图片：{poster_path.name}")
    except Exception as e:
        logging.error(f"处理 {fanart_path.name} 时出错：{e}")


def find_all_fanart_images(directory: Path) -> List[Path]:
    """
    查找指定目录下所有 fanart 图片，递归查找子目录
    假设 fanart 图片以 '-fanart' 结尾
    """
    return list(directory.glob("**/*-fanart.*"))


def process_all_fanarts(directory: Path) -> None:
    """
    处理指定目录下所有 fanart 图片，分离出 poster 图片
    """
    fanart_images = find_all_fanart_images(directory)
    for fanart in fanart_images:
        poster_path = fanart.with_name(
            fanart.name.replace("-fanart", "-poster")
        )  # 替换文件名
    logging.info(f"处理开始：{fanart.name} -> {poster_path.name}")
        split_poster_from_fanart(fanart, poster_path)
    logging.info(f"处理完成：{fanart.name} -> {poster_path.name}")


if __name__ == "__main__":
    process_all_fanarts(Path(r"Y:/JAV/2025"))
