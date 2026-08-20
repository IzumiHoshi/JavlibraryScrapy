# JAVBus / JAVLibrary Web Scraper

使用 [Scrapling](https://github.com/D4Vinci/Scrapling) 框架爬取 JAVBus 和 JAVLibrary 元数据，生成 Kodi/Plex 兼容的 NFO 文件。

## 快速开始

```bash
# 安装依赖
uv sync

# JAVBus 爬虫（从视频文件名提取车牌，生成 NFO）
uv run src/javlibraryscrapy/scraping/javbus.py

# JAVLibrary 爬虫（爬取 Most Wanted 列表）
uv run src/javlibraryscrapy/scraping/javlibrary.py
```

## 目录结构

```
├── src/javlibraryscrapy/scraping/javbus.py      # JAVBus 主爬虫
├── src/javlibraryscrapy/scraping/javlibrary.py  # JAVLibrary 爬虫
├── src/javlibraryscrapy/utils/
│   ├── car.py              # 车牌代码提取 (regex)
│   ├── filesave.py         # NFO 文件生成 / 文件重命名
│   └── fanart.py          # 封面裁剪为 poster
├── src/javlibraryscrapy/cli/
│   ├── gallery.py          # FastAPI 画廊服务入口
│   ├── export_mostwanted.py # 导出 Most Wanted 到本地库
│   ├── workflow.py         # 完整工作流：扫描 → 爬取 → 输出
│   ├── move_videos.py      # 移动视频（按大小过滤）
│   └── rename_at_symbol.py # 去除文件名 @ 前缀
├── src/javlibraryscrapy/server/                 # FastAPI 画廊实现
├── tests/                  # pytest 测试 + 调试脚本（unit / integration / ps1）
├── scripts/                # 仅 PowerShell 运维脚本
├── deprecated/             # 废弃版本（Selenium/Scrapy）
└── docs/archive/           # 开发文档
```

## 工作流

```bash
# 完整流程：下载目录 → 爬取 → 输出 NFO/封面
uv run python -m javlibraryscrapy.cli.workflow <下载路径> <输出路径> [--preview]
```

## 环境配置

`.env` 文件：

```env
JAVBUS_URL=https://www.javbus.com/
PROXY_ENABLED=true
PROXY=http://127.0.0.1:10808
```

## 输出示例

```
output/
├── ABF-340 性欲に支配された倒錯カップルの同棲中出し性交録。 瀧本雫葉/
│   ├── ABF-340 性欲に...nfo     (Kodi 元数据)
│   ├── fanart.png              (封面)
│   └── poster.png              (5:7 缩略图)
```

## 依赖

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 包管理器
- 代理（部分地区必需）

## License

本项目仅供学习教育使用。