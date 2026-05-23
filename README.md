# JAVBus / JAVLibrary Web Scraper

使用 [Scrapling](https://github.com/D4Vinci/Scrapling) 框架爬取 JAVBus 和 JAVLibrary 元数据，生成 Kodi/Plex 兼容的 NFO 文件。

## 快速开始

```bash
# 安装依赖
uv sync

# JAVBus 爬虫（从视频文件名提取车牌，生成 NFO）
uv run javbus_scrapling.py

# JAVLibrary 爬虫（爬取 Most Wanted 列表）
uv run javlibrary_scrapling.py
```

## 目录结构

```
├── javbus_scrapling.py      # JAVBus 主爬虫
├── javlibrary_scrapling.py  # JAVLibrary 爬虫
├── utils/
│   ├── car.py              # 车牌代码提取 (regex)
│   ├── filesave.py         # NFO 文件生成 / 文件重命名
│   └── fanart.py          # 封面裁剪为 poster
├── scripts/
│   ├── workflow.py         # 完整工作流：扫描 → 爬取 → 输出
│   ├── move_videos.py     # 移动视频（按大小过滤）
│   └── rename_at_symbol.py # 去除文件名 @ 前缀
├── test/                   # 测试脚本
├── deprecated/             # 废弃版本（Selenium/Scrapy）
└── docs/archive/           # 开发文档
```

## 工作流

```bash
# 完整流程：下载目录 → 爬取 → 输出 NFO/封面
uv run python scripts/workflow.py <下载路径> <输出路径> [--preview]
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