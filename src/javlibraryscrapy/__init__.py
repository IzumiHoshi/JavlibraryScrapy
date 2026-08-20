"""JavlibraryScrapy 主包。

按 src-layout 组织，模块分布：
- scraping     抓取器（JAVBus / JAVLibrary）
- library      本地影片库扫描与刷新
- server       FastAPI 画廊服务
- cli          console_scripts 入口（gallery 启动、export_mostwanted、workflow 等）
- utils        通用工具
- templates    HTML 模板
"""

__version__ = "0.1.0"