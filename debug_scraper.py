#!/usr/bin/env python
"""
调试脚本 - 诊断 JAVLibrary 爬虫的连接和加载问题
"""

import asyncio
import logging
from pathlib import Path
from scrapling.fetchers import AsyncDynamicSession

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


async def debug_fetch():
    """调试页面加载"""
    
    url = "https://www.javlibrary.com/cn/vl_mostwanted.php"
    proxy = "http://127.0.0.1:10808"  # 改为你的代理
    
    logger.info(f"目标 URL: {url}")
    logger.info(f"代理: {proxy}")
    logger.info("=" * 80)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.javlibrary.com/cn/",
    }
    
    try:
        logger.info("开始连接...")
        async with AsyncDynamicSession(
            load_dom=True,
            network_idle=True,
            disable_resources=False,
            proxy=proxy,
            headless=True,
            timeout=90000,
            stealth_mode=True,
        ) as session:
            logger.info("会话创建成功，正在获取页面...")
            response = await session.fetch(url, headers=headers)
            
            logger.info("✓ 页面加载成功！")
            logger.info("=" * 80)
            
            # 检查页面内容
            if hasattr(response, 'status'):
                logger.info(f"状态: {response.status}")
            
            # 尝试查找影片信息
            video_items = response.css("div.video")
            logger.info(f"找到 {len(video_items)} 个影片容器")
            
            if video_items:
                logger.info("✓ 页面内容正确加载")
                logger.info("=" * 80)
                logger.info("前 3 部影片预览：")
                
                for i, item in enumerate(video_items[:3], 1):
                    code = item.css("div.id::text").get()
                    title = item.css("a::attr(title)").get()
                    logger.info(f"  {i}. [{code}] {title[:50] if title else 'N/A'}...")
            else:
                logger.warning("⚠️ 没有找到影片容器！")
                logger.warning("页面可能未正确加载或已更改结构")
                
                # 保存 HTML 用于调试
                html_file = Path(__file__).parent / "debug_output.html"
                if hasattr(response, 'text'):
                    with open(html_file, 'w', encoding='utf-8') as f:
                        f.write(response.text)
                    logger.info(f"已保存 HTML 到: {html_file}")
                    
                    # 显示前 500 字符
                    preview = response.text[:500]
                    logger.info(f"页面内容预览：\n{preview}...")
            
            logger.info("=" * 80)
            logger.info("调试完成")
            
    except Exception as e:
        logger.error(f"✗ 连接失败: {e}")
        logger.error("=" * 80)
        logger.error("可能的原因：")
        logger.error("  1. 代理不工作或不可用")
        logger.error("  2. 网络连接问题")
        logger.error("  3. Cloudflare 验证超时")
        logger.error("  4. 网站已更改 HTML 结构")


if __name__ == "__main__":
    asyncio.run(debug_fetch())
