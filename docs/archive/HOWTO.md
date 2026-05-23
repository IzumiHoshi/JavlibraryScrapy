# JAVLibrary Scrapling 爬虫 - HOWTO 使用指南

## 📚 目录

1. [快速开始](#快速开始)
2. [安装配置](#安装配置)
3. [使用方法](#使用方法)
4. [常见问题](#常见问题)
5. [故障排查](#故障排查)

## 快速开始

### 最简单的方式（2 分钟）

```bash
# 1. 安装依赖
uv sync

# 2. 测试爬虫（第一页）
uv run test_scraper.py

# 3. 查看输出
cat output/test_movies.json
```

### 完整爬取（10 分钟设置）

```bash
# 1. 配置代理（可选但推荐）
cp .env.example .env
# 编辑 .env，添加你的代理信息

# 2. 运行爬虫
uv run javlibrary_scrapling.py

# 3. 爬虫会自动：
#    - 检测总页数
#    - 爬取所有页面
#    - 导出 JSON 和 CSV
```

## 安装配置

### 1. 安装依赖

```bash
cd JavlibraryScrapy
uv sync
```

这会安装所有必需的包：
- Scrapling（网页爬虫框架）
- Playwright（浏览器引擎）
- python-dotenv（环境配置）

### 2. 配置代理（推荐）

```bash
# 复制配置文件
cp .env.example .env

# 编辑 .env，填入你的代理信息
```

#### 代理配置示例

**Clash（最常用）**
```
PROXY_ENABLED=true
PROXY=http://127.0.0.1:7890
```

**V2Ray**
```
PROXY=http://127.0.0.1:8118
PROXY=socks5://127.0.0.1:10808
```

**Shadowsocks**
```
PROXY=http://127.0.0.1:1086  # 需要配置本地 HTTP 服务
```

## 使用方法

### 方式 1：快速测试（推荐先用）

```bash
uv run test_scraper.py
```

**优点：**
- 快速（20-30 秒）
- 验证配置是否正确
- 生成 test_movies.json 和 test_movies.csv

**输出：**
```
output/test_movies.json  # 第一页的 JSON 数据
output/test_movies.csv   # 第一页的 CSV 数据
```

### 方式 2：完整爬取

```bash
uv run javlibrary_scrapling.py
```

**特点：**
- 自动检测总页数（通常 25 页）
- 爬取所有页面
- 生成完整数据集

**预期时间：** 8-12 分钟

**输出：**
```
output/javlibrary_movies.json  # 所有影片的 JSON
output/javlibrary_movies.csv   # 所有影片的 CSV
```

### 方式 3：在代码中使用

```python
import asyncio
from pathlib import Path
from javlibrary_scrapling import JAVLibrarySpider

async def main():
    spider = JAVLibrarySpider(
        output_dir=Path("output"),
        proxy="http://127.0.0.1:7890"  # 可选
    )
    
    # 爬取前 5 页
    await spider.crawl(max_pages=5)
    
    # 保存数据
    spider.save_to_json("my_movies.json")
    spider.save_to_csv("my_movies.csv")

asyncio.run(main())
```

## 常见问题

### Q: 爬虫太慢了怎么办？

**A:** 这是正常的，原因有：
1. 需要通过浏览器加载 JS
2. 需要等待 Cloudflare 验证
3. 页间延迟以避免被封 IP

**优化方法：**
- 使用快速代理
- 减少页间延迟（编辑代码中的 `await asyncio.sleep(3)` 改为 1-2）
- 注意：过快可能导致被 IP 封禁

### Q: 遇到 403 错误怎么办？

**A:** 这意味着代理 IP 被网站拒绝。

**解决方法：**
1. 运行诊断：`uv run test_proxy.py`
2. 在代理工具中更换 IP
3. 等待 1-2 分钟
4. 重试爬虫

详见 [TROUBLESHOOT_403.md](TROUBLESHOOT_403.md)

### Q: 数据格式是什么样的？

**A:** JSON 格式：

```json
[
  {
    "id": "javmefjl5q",
    "code": "SNOS-222",
    "title": "脚フェチが集うパンストメーカー全男性社員を狂わせた 魔性のあし 楓ふうあ",
    "cover_url": "https://t2.pixhost.to/thumbs/7623/721821470_t677565.jpg"
  }
]
```

CSV 格式：
```
code,title,id,cover_url
SNOS-222,脚フェチが集う...,javmefjl5q,https://...
```

### Q: 如何下载封面图片？

**A:** 你可以用代码下载：

```python
import requests

def download_covers(movies, output_dir):
    for movie in movies:
        try:
            response = requests.get(movie['cover_url'], timeout=10)
            filename = f"{output_dir}/{movie['code']}.jpg"
            with open(filename, 'wb') as f:
                f.write(response.content)
            print(f"已下载：{filename}")
        except Exception as e:
            print(f"下载失败：{e}")

import json
with open("output/javlibrary_movies.json") as f:
    movies = json.load(f)
download_covers(movies, "covers")
```

## 故障排查

### 问题：连接超时

**原因：** 代理不可用或网络问题

**解决：**
```bash
uv run test_proxy.py  # 检查代理
```

### 问题：未找到影片信息

**原因：** 页面结构可能改变或加载失败

**解决：**
```bash
uv run debug_scraper.py  # 查看详细调试信息
```

### 问题：解析错误

**原因：** HTML 结构变化

**解决：**
```bash
uv run verify_parsing.py  # 验证解析逻辑
```

---

更多信息请查看其他文档或项目 GitHub 仓库。
