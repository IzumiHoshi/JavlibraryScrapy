#!/usr/bin/env python
"""
代理和连接诊断脚本
"""

import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_proxy():
    """测试代理连接"""
    proxy = "http://127.0.0.1:10808"
    url = "https://www.javlibrary.com/cn/vl_mostwanted.php"
    
    logger.info(f"测试代理: {proxy}")
    logger.info(f"目标 URL: {url}")
    logger.info("=" * 80)
    
    # 测试 1: 不带 referer
    logger.info("测试 1: 无 Referer 头")
    try:
        response = requests.get(
            url,
            proxies={"http": proxy, "https": proxy},
            timeout=10,
            verify=False,
        )
        logger.info(f"状态码: {response.status_code}")
        logger.info(f"内容长度: {len(response.text)}")
    except Exception as e:
        logger.error(f"失败: {e}")
    
    logger.info("")
    
    # 测试 2: 带标准 referer
    logger.info("测试 2: 带 Referer: https://www.javlibrary.com/cn/")
    try:
        response = requests.get(
            url,
            headers={
                "Referer": "https://www.javlibrary.com/cn/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            proxies={"http": proxy, "https": proxy},
            timeout=10,
            verify=False,
        )
        logger.info(f"状态码: {response.status_code}")
        logger.info(f"内容长度: {len(response.text)}")
        
        if "<div class=\"video\"" in response.text:
            logger.info("✓ 找到影片容器！")
        else:
            logger.warning("⚠️ 没找到影片容器")
            
    except Exception as e:
        logger.error(f"失败: {e}")
    
    logger.info("")
    
    # 测试 3: 带首页 referer
    logger.info("测试 3: 带 Referer: https://www.javlibrary.com/cn/ (from homepage)")
    try:
        response = requests.get(
            url,
            headers={
                "Referer": "https://www.javlibrary.com/cn/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            proxies={"http": proxy, "https": proxy},
            timeout=10,
            verify=False,
        )
        logger.info(f"状态码: {response.status_code}")
        logger.info(f"内容长度: {len(response.text)}")
        
        if response.status_code == 200:
            logger.info("✓ 状态 200 OK")
            
        if "<div class=\"video\"" in response.text:
            logger.info("✓ 找到影片容器！")
        
    except Exception as e:
        logger.error(f"失败: {e}")
    
    logger.info("=" * 80)


if __name__ == "__main__":
    test_proxy()
