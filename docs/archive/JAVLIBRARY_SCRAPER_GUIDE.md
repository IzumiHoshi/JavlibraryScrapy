# JAVLibrary Scrapling 爬虫使用指南

## 功能介绍

这个脚本使用 **Scrapling** 库来爬取 JAVLibrary 网站上的影片信息，包括：
- **影片 ID**（javmefjl5q 等唯一标识）
- **影片代码**（SNOS-222、DASS-926 等）
- **影片标题**（完整的日文标题）
- **封面 URL**（影片封面图片链接）

## 特性

✅ **Cloudflare 机器人验证处理**
- 使用 Scrapling 的动态浏览器引擎自动处理 Cloudflare 验证
- `load_dom=True` 加载完整 DOM
- `network_idle=True` 等待页面完全加载

✅ **代理支持**
- 支持 HTTP、HTTPS、SOCKS5 代理
- 可通过 `.env` 文件配置
- 自动避免 IP 被封

✅ **多页爬取**
- 自动检测总页数
- 支持指定最多爬取页数
- 每页间自动延迟 2 秒

✅ **数据导出**
- 支持 JSON 格式导出
- 支持 CSV 格式导出

## 环境配置

### 1. 配置 .env 文件

```bash
# 复制示例文件
cp .env.example .env

# 编辑 .env 文件，配置代理（如果需要）
PROXY_ENABLED=true
PROXY=http://127.0.0.1:7890
```

### 2. 代理配置示例

支持的代理格式：
- **HTTP 代理**: `http://127.0.0.1:7890`
- **HTTPS 代理**: `https://127.0.0.1:7890`
- **SOCKS5 代理**: `socks5://127.0.0.1:1080`

常用代理工具：
- **Clash**: 通常在 `127.0.0.1:7890` (HTTP) 或 `127.0.0.1:7891` (SOCKS5)
- **V2Ray**: 可配置多种协议
- **Shadowsocks**: 需要配置本地 HTTP/SOCKS5 服务

## 使用方法

### 方法 1：直接运行脚本

```bash
python javlibrary_scrapling.py
```

这会：
1. 自动检测总页数
2. 爬取所有页面的影片信息
3. 保存到 `output/javlibrary_movies.json` 和 `output/javlibrary_movies.csv`
4. 打印抓取摘要

### 方法 2：在代码中使用

```python
import asyncio
from pathlib import Path
from javlibrary_scrapling import JAVLibrarySpider

async def main():
    # 创建爬虫实例
    spider = JAVLibrarySpider(
        output_dir=Path("output"),
        proxy="http://127.0.0.1:7890"  # 可选
    )
    
    # 爬取前 5 页
    await spider.crawl(max_pages=5)
    
    # 保存数据
    spider.save_to_json("movies.json")
    spider.save_to_csv("movies.csv")
    
    # 打印摘要
    spider.print_summary()

asyncio.run(main())
```

### 方法 3：自定义目标 URL

```python
spider = JAVLibrarySpider(
    base_url="https://www.javlibrary.com/cn/vl_mostwanted.php",
    output_dir=Path("output"),
    proxy="http://127.0.0.1:7890"
)
```

## 输出数据格式

### JSON 格式 (javlibrary_movies.json)

```json
[
  {
    "id": "javmefjl5q",
    "code": "SNOS-222",
    "title": "脚フェチが集うパンストメーカー全男性社員を狂わせた 魔性のあし 楓ふうあ",
    "cover_url": "https://t2.pixhost.to/thumbs/7623/721821470_t677565.jpg"
  },
  {
    "id": "javmefjy3m",
    "code": "DASS-926",
    "title": "男をダメにする美脚堪能。甘すぎる極上足フェティスイートルーム。蒸れパンスト神聖おみ足でペニス挟撃コキシコぎゅむゥの極ズリ射精焦らし。 白峰ミウ",
    "cover_url": "https://t2.pixhost.to/thumbs/7262/716061823_t676175.jpg"
  }
]
```

### CSV 格式 (javlibrary_movies.csv)

```
code,title,id,cover_url
SNOS-222,脚フェチが集うパンストメーカー全男性社員を狂わせた 魔性のあし 楓ふうあ,javmefjl5q,https://t2.pixhost.to/thumbs/7623/721821470_t677565.jpg
DASS-926,男をダメにする美脚堪能。甘すぎる極上足フェティスイートルーム。蒸れパンスト神聖おみ足でペニス挟撃コキシコぎゅむゥの極ズリ射精焦らし。 白峰ミウ,javmefjy3m,https://t2.pixhost.to/thumbs/7262/716061823_t676175.jpg
```

## 常见问题

### Q: 爬虫被 Cloudflare 挡住了怎么办？
**A:** Scrapling 已经处理了 Cloudflare 验证。如果还是超时，可以：
1. 增加 `timeout` 时间（当前是 60000ms）
2. 检查代理是否正常工作
3. 尝试不同的代理

### Q: 如何加快爬虫速度？
**A:** 
1. 减少页间延迟（修改 `await asyncio.sleep(2)` 为更小的值）
2. 增加并发（需要修改代码为真并发爬取）
3. 使用更快的代理

### Q: 爬虫被 IP 封了怎么办？
**A:**
1. 使用代理重新尝试
2. 更换代理 IP
3. 增加页间延迟
4. 等待 IP 解封

### Q: 封面 URL 过期或失效怎么办？
**A:** 图片 URL 有缓存，可能需要实时下载。可以在获取后立即下载：

```python
import requests

def download_cover(url, filename):
    response = requests.get(url, timeout=10)
    with open(filename, 'wb') as f:
        f.write(response.content)
    print(f"已下载：{filename}")
```

## 脚本参数说明

### AsyncDynamicSession 配置

- `load_dom=True` - 加载完整 DOM（处理 JavaScript 渲染）
- `network_idle=True` - 等待网络空闲（等待所有请求完成）
- `disable_resources=False` - 允许加载资源（图片、CSS 等）
- `proxy` - 代理 URL
- `headless=True` - 无头模式（不显示浏览器窗口）
- `timeout=60000` - 超时时间（毫秒）

## 开发者指南

### 修改爬取逻辑

编辑 `parse_movies_from_html()` 方法来改变提取的字段：

```python
def parse_movies_from_html(self, html_content) -> List[Dict[str, Any]]:
    movies = []
    video_items = html_content.css("div.video")
    
    for item in video_items:
        # 添加自定义字段提取逻辑
        movie_info = {
            "id": item.css("::attr(id)").get(),
            # 添加新字段...
        }
        movies.append(movie_info)
    
    return movies
```

### 自定义数据导出

添加新的导出格式：

```python
def save_to_custom_format(self, filename: str):
    # 实现自定义导出逻辑
    pass
```

## 许可证

本脚本仅供学习和研究使用。请遵守网站的 robots.txt 和服务条款。

## 相关链接

- [Scrapling 文档](https://github.com/D4Vinci/Scrapling)
- [JAVLibrary](https://www.javlibrary.com/cn/)
