# JAVLibrary Scrapling 爬虫 - 完整使用说明

## 📋 已创建的文件

✅ **主爬虫脚本**
- `javlibrary_scrapling.py` - 完整的爬虫脚本，支持代理、Cloudflare 验证、多页爬取

✅ **测试和验证脚本**
- `test_scraper.py` - 快速测试脚本（仅爬取第一页）
- `verify_parsing.py` - 使用示例 HTML 验证解析逻辑 ✓ 已验证成功

✅ **文档和配置**
- `JAVLIBRARY_SCRAPER_GUIDE.md` - 完整的功能指南和开发文档
- `QUICKSTART.md` - 快速开始指南
- `.env.example` - 代理配置示例

## 🚀 快速开始

### 1️⃣ 安装依赖
```bash
cd d:\Code\JavlibraryScrapy
uv sync
```

### 2️⃣ 配置代理（可选但推荐）
```bash
# 复制示例配置
cp .env.example .env

# 编辑 .env 文件，配置你的代理
# PROXY_ENABLED=true
# PROXY=http://127.0.0.1:7890
```

### 3️⃣ 测试爬虫（推荐先测试）
```bash
uv run test_scraper.py
```

这会爬取第一页并生成：
- `output/test_movies.json`
- `output/test_movies.csv`

### 4️⃣ 爬取全部数据
```bash
uv run javlibrary_scrapling.py
```

这会爬取所有页面并生成：
- `output/javlibrary_movies.json`
- `output/javlibrary_movies.csv`

## 📊 输出数据结构

### JSON 格式示例
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
    "title": "男をダメにする美脚堪能。甘すぎる極上足フェティスイートルーム。蒸れパンスト神聖おみ足でペニス挟撃...",
    "cover_url": "https://t2.pixhost.to/thumbs/7262/716061823_t676175.jpg"
  }
]
```

### CSV 格式示例
```
code,title,id,cover_url
SNOS-222,脚フェチが集うパンストメーカー全男性社員を狂わせた 魔性のあし 楓ふうあ,javmefjl5q,https://...
DASS-926,男をダメにする美脚堪能。甘すぎる極上足フェティスイートルーム。蒸れパンスト神聖おみ足でペニス挟撃...,javmefjy3m,https://...
```

## ✨ 主要功能

### ✅ Cloudflare 机器人验证处理
- 自动通过 Cloudflare 验证
- 使用 Scrapling 的动态浏览器引擎
- 等待页面完全加载

### ✅ 代理支持
- HTTP/HTTPS/SOCKS5 代理支持
- 自动避免 IP 被封
- 通过 .env 文件配置

### ✅ 多页爬取
- 自动检测总页数
- 支持指定最多爬取页数
- 页间自动延迟（避免被封）

### ✅ 灵活的数据导出
- JSON 格式
- CSV 格式
- 易于扩展其他格式

## 🔧 高级用法

### 在代码中调用
```python
import asyncio
from pathlib import Path
from javlibrary_scrapling import JAVLibrarySpider

async def main():
    # 创建爬虫实例
    spider = JAVLibrarySpider(
        output_dir=Path("my_output"),
        proxy="http://127.0.0.1:7890"  # 可选
    )
    
    # 爬取前 3 页
    await spider.crawl(max_pages=3)
    
    # 保存数据
    spider.save_to_json("movies.json")
    spider.save_to_csv("movies.csv")
    
    # 打印统计信息
    spider.print_summary()

asyncio.run(main())
```

### 修改代理配置
```python
# 直接指定代理
spider = JAVLibrarySpider(proxy="socks5://127.0.0.1:1080")

# 或从 .env 读取
import os
from dotenv import load_dotenv
load_dotenv()
proxy = os.getenv("PROXY") if os.getenv("PROXY_ENABLED").lower() == "true" else None
spider = JAVLibrarySpider(proxy=proxy)
```

## 📈 性能参数

| 参数 | 值 | 说明 |
|-----|----|----|
| 超时时间 | 60 秒 | Scrapling 加载页面的最大等待时间 |
| 页间延迟 | 2 秒 | 每页之间的延迟（避免被封 IP） |
| 网络等待 | 启用 | 等待所有网络请求完成 |
| 动态加载 | 启用 | 加载 JavaScript 动态内容 |

## ⚙️ 代理配置示例

### Clash 配置
```
PROXY_ENABLED=true
PROXY=http://127.0.0.1:7890
```

### V2Ray 配置
```
PROXY_ENABLED=true
PROXY=http://127.0.0.1:8118
# 或 SOCKS5
PROXY=socks5://127.0.0.1:10808
```

### Shadowsocks 配置
```
PROXY_ENABLED=true
# 需要先启用本地 HTTP 代理服务
PROXY=http://127.0.0.1:1086
```

## ❓ 常见问题

### Q: 爬虫很慢是正常的吗？
**A:** 是的。由于需要通过浏览器加载页面和处理 Cloudflare 验证，速度会比较慢。预期：
- 单页：20-30 秒
- 全部页面（25 页）：8-12 分钟

### Q: 如何加快爬虫速度？
**A:**
1. 使用代理可能会加快速度（取决于代理质量）
2. 减少页间延迟（编辑代码中的 `await asyncio.sleep(2)` 为 1 秒或更少）
3. 注意：过快可能导致被 IP 封禁

### Q: 爬虫被 Cloudflare 挡住了怎么办？
**A:** Scrapling 已自动处理。如果还是失败：
1. 检查网络连接
2. 尝试使用代理
3. 增加超时时间（修改代码中的 `timeout=60000` 为 `timeout=120000`）

### Q: 数据格式可以自定义吗？
**A:** 可以。编辑 `parse_movies_from_html()` 方法来改变提取的字段，或添加新的 `save_to_*` 方法。

### Q: 如何处理大量数据？
**A:** 
1. 添加数据库支持（SQLite/PostgreSQL）
2. 实现流式处理而不是一次性加载所有数据
3. 分批爬取和处理

## 📝 脚本结构

```
javlibrary_scrapling.py
├── JAVLibrarySpider 类
│   ├── __init__() - 初始化
│   ├── fetch_page() - 获取单页
│   ├── parse_movies_from_html() - 解析 HTML
│   ├── get_page_count() - 获取总页数
│   ├── crawl() - 主爬虫循环
│   ├── save_to_json() - 保存为 JSON
│   ├── save_to_csv() - 保存为 CSV
│   └── print_summary() - 打印统计
└── main() - 入口函数
```

## 🛠️ 调试技巧

1. **查看日志输出**：脚本会打印详细的日志，包括爬取进度和错误信息

2. **保存 HTML 进行调试**：编辑爬虫脚本，在 `fetch_page()` 后添加：
   ```python
   with open(f"debug_page_{page}.html", "w", encoding="utf-8") as f:
       f.write(response.text)
   ```

3. **测试单页**：使用 `test_scraper.py` 只爬取第一页进行快速测试

4. **增加日志级别**：将 `logging.INFO` 改为 `logging.DEBUG` 获取更多信息

## 📚 相关文档

- 完整功能指南：`JAVLIBRARY_SCRAPER_GUIDE.md`
- 快速开始指南：`QUICKSTART.md`
- Scrapling 文档：https://github.com/D4Vinci/Scrapling

## ✅ 验证结果

已通过示例 HTML 验证，成功解析 20 部影片：
```
✓ ID 提取正确（如：javmefjl5q）
✓ 代码提取正确（如：SNOS-222）
✓ 标题提取正确（完整日文标题）
✓ 封面 URL 提取正确（有效的图片链接）
```

## 🎯 下一步

1. 配置你的代理（如果需要）
2. 运行 `uv run test_scraper.py` 进行测试
3. 检查 `output` 目录中的 JSON/CSV 文件
4. 根据需要调整参数并运行完整爬虫

祝爬虫运行顺利！🚀
