#!/usr/bin/env python
"""
调试脚本 - 诊断 JAVLibrary 爬虫的连接和加载问题
"""

import asyncio
import logging
from pathlib import Path
from scrapling.fetchers import AsyncDynamicSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def debug_fetch():
    """调试页面加载"""
    
    url = "https://www.javlibrary.com/cn/vl_mostwanted.php"
    proxy = "socks5://127.0.0.1:10808"  # 改为你的代理
    
    logger.info(f"目标 URL: {url}")
    logger.info(f"代理: {proxy}")
    logger.info("=" * 80)
    
    try:
        logger.info("尝试 1: 基础连接（无自定义头）...")
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
            
            # 不传入 headers，让 Scrapling 使用默认值
            response = await session.fetch(url)
            
            logger.info(f"状态: {response.status if hasattr(response, 'status') else 'N/A'}")
            
            # 检查响应内容长度
            if hasattr(response, 'text'):
                with open(Path(__file__).parent / "debug_response1.html", 'w', encoding='utf-8') as f:
                    f.write(response.text)
                content_length = len(response.text)
                logger.info(f"内容长度: {content_length} 字符")
            
            # 尝试查找影片信息
            video_items = response.css("div.video")
            logger.info(f"找到 {len(video_items)} 个影片容器")
            
            if video_items:
                logger.info("✓ 页面内容正确加载！")
                logger.info("=" * 80)
                logger.info("前 3 部影片预览：")
                
                for i, item in enumerate(video_items[:3], 1):
                    code = item.css("div.id::text").get()
                    title = item.css("a::attr(title)").get()
                    logger.info(f"  {i}. [{code}] {title[:50] if title else 'N/A'}...")
                
                return True
            else:
                logger.warning("⚠️ 没有找到影片容器")
                
                # 保存 HTML 用于调试
                html_file = Path(__file__).parent / "debug_output.html"
                if hasattr(response, 'text'):
                    with open(html_file, 'w', encoding='utf-8') as f:
                        f.write(response.text)
                    logger.info(f"已保存 HTML 到: {html_file}")
                    
                    # 显示前 1000 字符
                    preview = response.text[:1000]
                    logger.info(f"页面内容预览（前 1000 字符）：\n{preview}...")
                
                # 检查是否是错误页面
                if "403" in response.text or "Forbidden" in response.text:
                    logger.error("✗ 服务器返回 403 Forbidden")
                    return False
                elif "Cloudflare" in response.text:
                    logger.error("✗ 被 Cloudflare 挡住了")
                    return False
                
                return False
            
    except Exception as e:
        logger.error(f"✗ 连接失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


if __name__ == "__main__":
    success = asyncio.run(debug_fetch())
    logger.info("=" * 80)
    if success:
        logger.info("✓ 调试成功！爬虫应该可以工作了")
    else:
        logger.error("✗ 调试失败，请检查代理和网络连接")

