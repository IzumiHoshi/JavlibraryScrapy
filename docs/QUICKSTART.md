# JAVLibrary 爬虫 - 快速开始

## 📥 安装依赖

```bash
# 使用 uv（推荐）
uv sync

# 或使用 pip
pip install -r requirements.txt
```

## ⚙️ 配置代理（可选）

1. **复制配置文件**
   ```bash
   cp .env.example .env
   ```

2. **编辑 `.env` 文件**
   ```
   PROXY_ENABLED=true
   PROXY=http://127.0.0.1:7890
   ```

3. **支持的代理格式**
   - HTTP: `http://host:port`
   - HTTPS: `https://host:port`
   - SOCKS5: `socks5://host:port`

## 🚀 快速运行

### 测试爬虫（仅爬取第一页）

```bash
python test_scraper.py
```

这会生成：
- `output/test_movies.json` - 第一页的 JSON 数据
- `output/test_movies.csv` - 第一页的 CSV 数据

### 爬取全部数据

```bash
python javlibrary_scrapling.py
```

这会生成：
- `output/javlibrary_movies.json` - 所有影片的 JSON 数据
- `output/javlibrary_movies.csv` - 所有影片的 CSV 数据

## 📊 输出示例

### JSON 格式
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

### CSV 格式
| code | title | id | cover_url |
|------|-------|----|----|
| SNOS-222 | 脚フェチが集う... | javmefjl5q | https://... |

## 🔧 在代码中使用

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

## ❓ 常见问题

### Q: 爬虫太慢怎么办？
A: 这是正常的，因为需要通过浏览器加载页面和处理 Cloudflare 验证。建议：
- 使用代理加快速度
- 减少页间延迟（编辑代码中的 `await asyncio.sleep(2)` ）

### Q: Cloudflare 验证失败？
A: Scrapling 应该自动处理，如果还是失败：
- 检查网络连接
- 尝试使用代理
- 增加超时时间

### Q: 数据不完整怎么办？
A: 可能页面还在加载，Scrapling 已配置为等待网络空闲。如果还是有问题：
- 检查网络连接
- 尝试运行测试脚本
- 查看日志输出

## 📖 详细文档

查看 `JAVLIBRARY_SCRAPER_GUIDE.md` 获取完整的使用指南和开发文档。

## 🆘 获取帮助

- 查看日志输出了解详细错误信息
- 在代码中添加 `print()` 或 `logger.debug()` 进行调试
- 检查网络连接和代理配置
