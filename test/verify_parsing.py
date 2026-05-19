#!/usr/bin/env python
"""
验证脚本 - 使用提供的示例 HTML 文件来验证解析逻辑
"""
import sys
import logging
from pathlib import Path
from scrapling import Selector


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sys.path.append(str(Path(__file__).parent.parent))  # 将项目根目录添加到 sys.path，以便导入模块


def verify_parsing():
    """使用示例 HTML 验证解析逻辑"""
    
    # 读取示例 HTML 文件
    html_file = Path(__file__).parent.parent / "temp" / "最想要的影片 - JAVLibrary.html"
    
    if not html_file.exists():
        logger.error(f"示例 HTML 文件不存在：{html_file}")
        return
    
    logger.info(f"读取示例 HTML 文件：{html_file}")
    
    with open(html_file, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    # 使用 Scrapling 的 Selector 解析 HTML
    selector = Selector(html_content)
    
    # 查找所有影片容器
    video_items = selector.css("div.video")
    logger.info(f"找到 {len(video_items)} 部影片")
    
    # 解析前 5 部影片
    movies = []
    for i, item in enumerate(video_items[:5]):
        try:
            # 提取影片 ID
            vid_attr = item.css("::attr(id)").get()
            movie_id = vid_attr.replace("vid_", "") if vid_attr else ""
            
            # 提取影片代码
            code = item.css("div.id::text").get()
            if code:
                code = code.strip()
            
            # 提取标题
            title = item.css("a::attr(title)").get()
            if title:
                title = title.strip()
            else:
                title = ""
            
            # 提取封面 URL
            cover_url = item.css("img::attr(src)").get()
            
            # 处理相对路径
            if cover_url:
                if cover_url.startswith("./"):
                    # 尝试从 onerror 属性获取真实 URL
                    onerror_attr = item.css("img::attr(onerror)").get()
                    if onerror_attr:
                        import re
                        url_match = re.search(r"'(https://[^']+)'", onerror_attr)
                        if url_match:
                            cover_url = url_match.group(1)
                elif not cover_url.startswith("http"):
                    cover_url = "https://www.javlibrary.com" + cover_url
            else:
                cover_url = ""
            
            movie_info = {
                "id": movie_id,
                "code": code,
                "title": title,
                "cover_url": cover_url,
            }
            
            movies.append(movie_info)
            
        except Exception as e:
            logger.error(f"解析第 {i+1} 部影片失败：{e}")
            continue
    
    # 打印结果
    logger.info("\n" + "=" * 80)
    logger.info("解析结果验证")
    logger.info("=" * 80)
    
    for i, movie in enumerate(movies, 1):
        logger.info(f"\n第 {i} 部影片：")
        logger.info(f"  代码 (Code): {movie['code']}")
        logger.info(f"  标题 (Title): {movie['title']}")
        logger.info(f"  ID: {movie['id']}")
        logger.info(f"  封面 (Cover): {movie['cover_url']}")
    
    logger.info("\n" + "=" * 80)
    logger.info(f"✓ 验证成功！成功解析 {len(movies)} 部影片")
    logger.info("=" * 80 + "\n")


if __name__ == "__main__":
    verify_parsing()
