# Scrapling JAVBus 爬虫 - 快速开始指南

## 🚀 快速安装

### 1. 安装 Scrapling
```bash
pip install scrapling
```

### 2. 检查安装
```bash
python -c "import scrapling; print(scrapling.__version__)"
```

### 3. 安装所有依赖
```bash
pip install scrapling requests pillow python-dotenv
```

## 📝 快速使用

### 最简单的方式
```bash
python javbus_scrapling.py
```

脚本会提示输入视频目录：
```
请输入视频目录路径：D:\Videos
开始查找车牌...
找到 3 个车牌
车牌：ABF-340, 路径：D:\Videos\HKBISI@ABF-340-C.mp4
正在爬取：https://www.javbus.com/ABF-340
...
```

## 🎯 核心特性

### 1. 简洁的 API
```python
from scrapling import Spider, Selector, RequestsAsync

# 创建选择器
selector = Selector(html)
text = selector.css("div::text").get()
```

### 2. 异步爬取
```python
async def crawl():
    fetcher = RequestsAsync()
    page = await fetcher.fetch(url)
    selector = Selector(page.html)
```

### 3. 开箱即用的反爬虫
- ✅ 自动 User-Agent 轮换
- ✅ 自动代理轮换
- ✅ 自动 Cloudflare 绕过
- ✅ 自动重试机制

## ⚙️ 配置

### 通过 .env 文件配置
```env
JAVBUS_URL=https://www.javbus.com/
PROXY_ENABLED=true
PROXY=http://proxy.example.com:8080
```

### 在代码中配置
```python
import os
from dotenv import load_dotenv

load_dotenv()

javbus_url = os.getenv("JAVBUS_URL", "https://www.javbus.com/")
proxy_enabled = os.getenv("PROXY_ENABLED", "False").lower() == "true"
```

## 📊 输出结构

爬取完成后的文件结构：
```
D:\Videos\
├── ABF-340 Movie Title 1/
│   ├── ABF-340 Movie Title 1.mp4       # 视频文件
│   ├── ABF-340 Movie Title 1.nfo       # 元数据
│   ├── ABF-340 Movie Title 1-fanart.png # 海报（宽）
│   └── ABF-340 Movie Title 1-poster.png # 海报（窄）
├── STARS-123 Movie Title 2/
│   ├── STARS-123 Movie Title 2.mp4
│   ├── STARS-123 Movie Title 2.nfo
│   ├── STARS-123 Movie Title 2-fanart.png
│   └── STARS-123 Movie Title 2-poster.png
└── ...
```

## 🔍 日志输出

脚本会输出详细的爬取日志：

```
2026-05-14 00:34:28,123 - INFO - 开始查找车牌...
2026-05-14 00:34:28,456 - INFO - 找到 3 个车牌
2026-05-14 00:34:28,789 - INFO - 车牌：ABF-340, 路径：D:\Videos\file.mp4
2026-05-14 00:34:30,123 - INFO - 正在爬取：https://www.javbus.com/ABF-340
2026-05-14 00:34:32,456 - INFO - 页面标题：Movie Title
2026-05-14 00:34:32,789 - INFO - 發行日期：2025-06-23
2026-05-14 00:34:33,123 - INFO - 導演：Director Name
2026-05-14 00:34:33,456 - INFO - 已下载封面：ABF-340.png
2026-05-14 00:34:33,789 - INFO - 完成处理：ABF-340 Movie Title
2026-05-14 00:34:34,123 - INFO - 共处理 3 部电影
```

## 💡 常见用法

### 基本爬取
```python
import asyncio
from javbus_scrapling import JavbusSpider

async def main():
    spider = JavbusSpider(root_dir="D:\\Videos")
    cars = [("ABF-340", "D:\\Videos\\video.mp4")]
    await spider.crawl_and_process(cars)

asyncio.run(main())
```

### 自定义选择器
```python
# 在 parse() 方法中修改选择器
selector.css("custom-selector::text").get()
selector.css("custom-selector::attr(data-value)").get()
selector.css("custom-selector").getall()  # 获取所有
```

### 错误处理
```python
try:
    page = await fetcher.fetch(url)
except Exception as e:
    logger.error(f"爬取失败: {e}")
    # 自动重试已内置，无需手动处理
```

## 🔗 选择器语法

Scrapling 支持 CSS 选择器，常见用法：

```python
# 获取单个文本
text = selector.css("div.class::text").get()

# 获取所有文本
texts = selector.css("div.class::text").getall()

# 获取属性
attr = selector.css("img::attr(src)").get()

# 链式选择
nested = selector.css("div.parent > div.child::text").get()

# 正则表达式
matched = selector.css("span::text").re_first(r"(\d+)")
```

## 📈 性能优化

### 1. 异步并发
```python
# 默认串行，可配置并发
# Scrapling 自动处理异步
```

### 2. 响应缓存
```python
# Scrapling 自动缓存相同请求
page = await fetcher.fetch(url)  # 第一次网络请求
page = await fetcher.fetch(url)  # 第二次从缓存
```

### 3. 自动限流
```python
# 自动遵守 robots.txt
# 自动添加延迟避免被封
```

## 🐛 常见问题

### Q1: 如何设置超时时间？
```python
page = await fetcher.fetch(url, timeout=30)
```

### Q2: 如何设置重试次数？
```python
page = await fetcher.fetch(url, retries=5)
```

### Q3: 如何使用代理？
```env
PROXY_ENABLED=true
PROXY=http://proxy:8080
```

### Q4: 如何处理 403 Forbidden？
```python
# Scrapling 自动处理
# - 轮换 User-Agent
# - 添加 Referer
# - 自动绕过 Cloudflare
```

### Q5: 爬取速度太慢？
```python
# 原因：内置限流保护网站
# 解决：如果网站允许，可以增加并发
# 不建议关闭限流，容易被封IP
```

## 🔐 最佳实践

### 1. 设置合理的延迟
```python
# 在代码中添加延迟
import asyncio
await asyncio.sleep(2)  # 2秒延迟
```

### 2. 使用 User-Agent 轮换
```python
# Scrapling 自动进行，无需配置
```

### 3. 使用代理轮换
```env
PROXY_ENABLED=true
PROXY=http://proxy1:8080
```

### 4. 监控请求
```python
# 查看日志输出
logger.info(f"正在爬取：{url}")
```

### 5. 数据验证
```python
if not movie_info.get("title"):
    logger.warning("标题为空，跳过处理")
    return
```

## 📚 Scrapling 官方资源

- 📖 [官方文档](https://scrapling.readthedocs.io/)
- 🐙 [GitHub 仓库](https://github.com/D4Vinci/Scrapling)
- 💬 [Discord 社区](https://discord.gg/EMgGbDceNQ)
- 🐛 [问题反馈](https://github.com/D4Vinci/Scrapling/issues)

## 🎓 高级功能

### 流式爬取
```python
async for page in fetcher.stream(urls):
    selector = Selector(page.html)
    # 处理每个页面
```

### CLI 工具
```bash
# 直接命令行爬取
scrapling crawl "https://www.javbus.com/ABF-340"
```

### 导出结果
```bash
# 导出为 JSON
scrapling crawl "https://..." --output results.json
```

## 🚀 下一步

### 进阶学习
1. 阅读 [Scrapling 文档](https://scrapling.readthedocs.io/)
2. 学习高级选择器用法
3. 实现自定义中间件
4. 构建分布式爬虫

### 项目优化
1. 添加数据库存储
2. 实现定时任务
3. 添加监控告警
4. 性能优化

## 📊 三个版本对比

| 特性 | javbus.py | javbus_scrapy.py | **javbus_scrapling.py** |
|-----|-----------|------------------|------------------------|
| 代码行数 | 144 | 380 | **220** |
| 速度 | 慢 | 快 | **极快** |
| 学习难度 | 中 | 高 | **低** |
| 资源消耗 | 高 | 中 | **低** |
| 推荐指数 | ⭐⭐ | ⭐⭐⭐ | **⭐⭐⭐⭐⭐** |

---

**版本：** 1.0.0  
**更新时间：** 2026-05-14  
**推荐阅读：** [SCRAPLING_COMPARISON.md](./SCRAPLING_COMPARISON.md)
