# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Python 网络爬虫，用于从 **JAVBus**（按视频抓取元数据 → 生成 Kodi/Plex NFO）和 **JAVLibrary**（多页 "Most Wanted" 列表 → JSON/CSV）抓取成人视频元数据。基于 [Scrapling](https://github.com/D4Vinci/Scrapling) 框架实现 JavaScript 渲染和 Cloudflare 绕过。`pyproject.toml` 中项目名为 `javlibraryscrapy`，Python 3.11，使用 `uv` 管理依赖。主要面向 Windows 平台（工作流用 `robocopy` 处理大文件；`scripts/` 下还有 PowerShell 脚本）。

## 常用命令

```bash
# 安装/同步依赖
uv sync

# JAVBus：扫描视频目录，抓取元数据，生成 NFO + 封面
uv run javbus_scrapling.py
# （会交互式提示输入视频目录）

# JAVLibrary：爬取 "Most Wanted" 列表，输出 JSON/CSV
uv run javlibrary_scrapling.py

# 最想要列表 → 本地库（每部一个文件夹，含 movie.nfo / poster.jpg / fanart.jpg）
uv run python scripts/export_mostwanted.py
# 等价于：uv run python scripts/export_mostwanted.py --source output/javlibrary_movies.json --library-root "$MOSTWANTED_LIBRARY_ROOT"
uv run python scripts/export_mostwanted.py --library-root "Z:\\JAV\\MostWanted" --overwrite  # 强制覆盖已存在
uv run python scripts/export_mostwanted.py --dry-run  # 只打印计划，不写文件
uv run python scripts/export_mostwanted.py --limit 5  # 只处理前 5 部（调试用）
uv run python scripts/export_mostwanted.py --skip-javbus  # 只下 poster.jpg，跳过 JAVBus 抓取

# 影片画廊（FastAPI + uvicorn，把 output/ 的结果以卡片展示，勾选后一键抓磁力链接）
uv run python scripts/gallery_server.py [--port 8000] [--data output/javlibrary_movies.json] [--open-browser]
# 加上本地库（需在 .env 配置 LIBRARY_ROOT）：
uv run python scripts/gallery_server.py --library-root "Z:\\JAV"
# 等价的新入口（python -m 形式，推荐用于后台/调试）：
uv run python -m scripts.gallery.main [--help]
# 后台启停（Windows）：用 PowerShell 脚本管理 uvicorn 进程（PID 持久化 + 日志）
pwsh scripts/Start-GalleryServer.ps1 -Action Start     # 后台启动（pythonw），PID 写入 output/.gallery_server.pid
pwsh scripts/Start-GalleryServer.ps1 -Action Status   # 显示 PID/内存/端点探活/最近日志
pwsh scripts/Start-GalleryServer.ps1 -Action Stop      # 优雅停止（PID 文件 + 端口双定位）
pwsh scripts/Start-GalleryServer.ps1 -Action Restart   # Stop 后自动 Start
pwsh scripts/Start-GalleryServer.ps1 -Action Start -Port 8080 -LibraryRoot "Z:\\JAV" -OpenBrowser  # 默认不打开浏览器，加 -OpenBrowser 才打开

# 单独跑本地库扫描（不启动 server）：
uv run python scripts/library_scanner.py --root "Z:\\JAV" --index output/library_index.json
# 把本地库的 "<carid> <title>-poster.jpg" 之类命名复制成 library_scanner 识别的标准名（PowerShell）：
pwsh scripts/Sync-LibraryCoverNames.ps1 [-LibraryRoot "Z:\\JAV"] [-DryRun | -Force]

# 端到端工作流：移动大视频 → 去除 "@" 前缀 → 抓取
uv run python workflow.py <download_path> <intermediate_path> <output_path> [--min-size 500] [--preview]

# 按大小过滤移动视频（Windows 下 ≥100MB 文件使用 robocopy）
uv run python scripts/move_videos.py <source> <destination> [--min-size 500]

# 去除文件名中的 "@site.com@" 前缀
uv run python scripts/rename_at_symbol.py <path> [--preview]

# 调试辅助脚本（手动脚本，不是 pytest；test/ 里 .py 是 Python，.ps1 是 PowerShell 辅助）
uv run python test/test_proxy.py          # 验证代理 + Referer 头到达 JAVLibrary
uv run python test/debug_scraper.py       # 诊断 AsyncDynamicSession 加载
uv run python test/test_scraper.py        # 仅爬取 JAVLibrary 首页
uv run python test/verify_parsing.py      # 解析 temp/ 中保存的 HTML 文件
uv run python test/verify_abf.py          # 校验具体车牌 ABF-* 的解析
uv run python test/test_library_scanner.py # 离线跑 library_scanner 单元测试
uv run python test/test_gallery_server_library.py # 离线跑画廊 + 本地库集成测试
```

> `test/` 目录下是手动调试脚本 —— 没有 pytest 测试套件。

## 架构

### 爬虫（顶层入口）

- **`javbus_scrapling.py`** — `JavbusSpider`：扫描视频目录，提取车牌，通过 `AsyncDynamicSession` 抓取 JAVBus 视频详情页，下载封面，在 `<root>/<CARID> <title>/` 下生成 NFO + poster/fanart。
  - `parse()` 提取标题、发行日期、制作商/发行商、类别、演员、封面 URL、**樣品圖像 URL 列表**（`#sample-waterfall a.sample-box[href]`）和磁力链接（`_extract_magnet_link` 中按 HD+字幕 > HD > 标准 优先级）。
  - **`download_cover()`** 与新增的 **`download_samples()`**：cover 落地为 `<root>/<CARID>.png`；samples 落地为 `<root>/<CARID>_sample_NNN.jpg`（按 URL 顺序编号）。两者都带 JAVBus Referer 头，幂等（已存在跳过）。Caller 负责把临时文件移动到目标目录 —— wanted refresh 的 Phase 3 会把 cover 重命名为 `cover.jpg`、samples 重命名为 `sample_NNN.jpg` 落到 `<MOSTWANTED_LIBRARY_ROOT>/<CARID> <title>/`。
  - **磁链解析鲁棒性**（commit `45e0d9a`）：`_extract_magnet_link` 优先尝试 `a.magnet-link` / `link-magnet` class；若页面里没有这些 class（部分画廊详情页的 HTML 结构），回退到全文正则匹配 `magnet:?xt=urn:btih:...`，避免漏抓；解析过程会写 debug 日志便于追踪。
  - `download_cover()` 使用同步 `requests`（而非 Scrapling session）以便显式设置 `Referer` 头指向视频页面 —— 这是避免 403 的必需操作。封面初次保存到 `root_dir/<car_id>.png`，然后 `process_movie()` 在每个视频子目录中将其重命名为 `fanart.png`。
  - `process_movie()` 被 `workflow.py` 子类化以重定向输出到不同目录（构造后设置 `spider.output_dir = output_path`；子类把封面复制到子目录，而不是原地重命名）。

- **`javlibrary_scrapling.py`** — `JAVLibrarySpider`：爬取 JAVLibrary `vl_mostwanted.php`（或可配置的基础 URL），自动检测总页数，页间休眠 3 秒，导出 `movies.json` + `movies.csv` 到 `output/`（若 `.env` 的 `MOSTWANTED_LIBRARY_ROOT` 设了则落到该目录下）。使用 `stealth_mode=True` 和 90 秒超时以通过 Cloudflare 验证。
- **`scripts/export_mostwanted.py`** — 把 JAVLibrary `movies.json` 导出到本地库。对每部影片建 `<root>/<CARID> <title>/`，写 `movie.nfo`（用 `JavbusSpider.parse()` 拉 JAVBus 详情页元数据填 NFO）+ `poster.jpg`（JAVLibrary 缩略图）+ `fanart.jpg`（JAVBus 横版原图）。复用 `JavbusSpider` 处理 JAVBus 部分，仅覆写 `process_movie` 把 `fanart.png → fanart.jpg`、NFO 改名为 `movie.nfo`、不做 `split_poster_from_fanart`。默认跳过已存在文件夹；排除列表与 `find_car_bus` 一致（`HEYZO/PONDO/CARIB/OKYOHOT`，这些在 JAVBus 上没页面）。`--source` / `--library-root` 默认都从 `.env` 的 `MOSTWANTED_LIBRARY_ROOT` 读，未设则退回 `output/`。

两个爬虫都使用 `scrapling.fetchers.AsyncDynamicSession` 进行 JS 渲染。

### `utils/`

- **`car.py`** — `find_car_bus(file, list_suren_car)` 从大写后的文件名中提取 JAVBus 车牌号。三个正则分支按优先级顺序：`T28-###`、`##ID-###`（如 `20ID-020`）、标准 `[A-Z]+-###`。从长后缀中去除前导零（`AVOP00127` → `AVOP-127`）。`find_car_bus` 内部硬编码的排除列表：`HEYZO`、`PONDO`、`CARIB`、`OKYOHOT`（这些在 JAVBus 上不存在页面）。`javbuscar(root_dir)` 包装器遍历目录，对每个视频调用 `find_car_bus(file, ["LUXU", "MIUM"])`（`LUXU`/`MIUM` 由调用方传入，不由 `find_car_bus` 自身强制），返回 `[(car_id, file_path), ...]`。
- **`filesave.py`** — `write_xml(nfo_path, info)` 生成 Kodi/Plex NFO，硬编码 `mpaa=NC-17`、`countrycode=JP`、`country=日本`；按空格分割类别/演员；转义 XML。`rename()` 是 `Path.rename` 的安全包装，目标存在时 no-op。
- **`fanart.py`** — `split_poster_from_fanart(fanart, poster)` 从 fanart 的**右边缘**裁剪出 5:7 比例（这就是 JAVBus 海报叠在 fanart 上的布局）。注意：`process_all_fanarts()` 是死代码 —— `poster_path` 赋值在循环体的错误作用域内。

### `scripts/`

- **`move_videos.py`** — 递归复制视频文件，带 `--min-size` 过滤（默认 500 MB）。≥100 MB 文件通过 `robocopy`（Windows）处理以显示进度；较小文件使用 `shutil.move`。文件名冲突时交互式提示（覆盖 / 跳过 / 重命名）。
- **`rename_at_symbol.py`** — 去除文件名中第一个 `@` 之前的所有内容（`hkbisi.com@ABF-340-C.mp4` → `ABF-340-C.mp4`）。支持 `--preview`。
- **`gallery_server.py`** — 向后兼容 shim（915 字节），实际实现已迁到 `scripts/gallery/` 包。直接运行 `scripts/gallery_server.py` 等价于 `python -m scripts.gallery.main`，但推荐用 `python -m` 形式做调试 / 后台启动。
- **`scripts/gallery/`** — FastAPI + uvicorn 的重构版本。包布局：
  - `app.py` — `create_app()` 工厂 + `local_ip_address()`（局域网展示用）
  - `config.py` — 从 `.env` 读取的 `Settings`（pydantic-settings）
  - `models.py` — 请求/响应 Pydantic 模型（保留原服务的 JSON shape）
  - `services/` — 业务逻辑：`jobs.py`（`ScrapeJob` 状态机 + `JobLogHandler`）、`jobs_runner.py`、`wanted.py`（wanted 列表）、`wanted_refresh.py`（单部刷新）、`library.py`（本地库查询 / 详情 / 报警）、`covers.py`（封面代理）
  - `routes/` — HTTP 路由层：仅做请求解析 + 响应包装。模块按资源拆分：`movies`、`scrape`、`covers`、`folder`、`library`、`rescan`、`wanted_refresh`、`pages`
  - `main.py` — CLI 入口（argparse + uvicorn.run）
  - 关键行为：
    - 读取 `output/javlibrary_movies.json`（缺失时回退 `.csv`），以卡片 + 复选框形式展示；勾选后点"抓取选中的磁力"，后端在**后台线程**里 `asyncio.run(MagnetSpider.crawl_and_process(...))`，结果写入 `output/magnets.json` + `output/magnets_links.txt`（纯磁力链接，每行一条）。
    - `MagnetSpider(JavbusSpider)` 覆写 `download_cover()` 返回 `None`（跳过封面下载）和 `process_movie()`（不落地 NFO/视频，只把磁力记录到 `ScrapeJob`）；`crawl_and_process` 要求 `[(car_id, video_path), ...]`，这里视频路径传空串占位。
    - 进度靠 `ScrapeJob` 状态机 + 一个临时挂到 root logger 的 `JobLogHandler` 实时回传日志。因为 `crawl_and_process` 按顺序处理且仅在成功时回调，"正在抓取 X" 取的是首个仍为 `pending` 的车牌；任务结束时残留的 `pending` 统一标记为 `failed`。
    - 同一时刻只允许一个任务（第二次提交返回 409）；`code` 必须匹配 `[A-Z0-9_-]{2,32}` 才会被拼进 URL。
    - `/api/cover?url=` 用 `requests` + `.env` 代理在服务端拉封面并缓存到 `output/.cover_cache/`（`--image-proxy auto` 时，配了代理才启用）—— `pics.dmm.co.jp` 的图在部分网络下浏览器直连拿不到。
    - **单部刷新**（commit `0471157`）：`wanted_refresh` 路由对单条车牌重新拉 JAVBus，可保留已选状态、避免整页重抓。
  - 页面模板在 `scripts/templates/gallery.html`，每次请求从磁盘读取，改完刷新即可，不用重启服务。模板同时承载 `/wanted`（爬取结果）和 `/library`（本地库）双页面 + 顶部导航 + 海报灯箱（commit `0471157` 的双页面灯箱指 wanted/library 两个页面共用一个灯箱组件）。

> `scripts/README.md` 引用的 `scripts/workflow.py` 不存在 —— 工作流位于**项目根目录**的 `workflow.py`。`scripts/` 下的 PowerShell 脚本：
> - `Start-GalleryServer.ps1` —— FastAPI 画廊的后台启停 / 探活（PID + 端口双定位，日志写到 `output/.gallery_server.log`）。
> - `Sync-LibraryCoverNames.ps1` —— 把本机 `<carid> <title>-poster.jpg` 等命名复制成 `library_scanner` 识别的标准名。
> - `Move-VideoFiles.ps1` / `Rename-FilesByAtSymbol.ps1` —— 对应早期的 `scripts/move_videos.py` / `scripts/rename_at_symbol.py`，保留以备旧流程使用。

### `workflow.py`（根目录，权威的端到端流水线）

三步流程：
1. `step1_move_videos()` — 用 `shutil.move`（不用 robocopy）将 `download_path` 中所有 ≥`--min-size` MB 的文件移动到 `intermediate_path`。
2. `step2_clean_at_prefix()` — 去除 `intermediate_path` 中文件名的 `@` 前缀（`--preview` 模式下仅记录）。
3. `step3_scrape()` — 子类化 `JavbusSpider` 以重写 `process_movie()`：每个车牌在 `output_path` 下有独立的子目录 `<CARID> <title>`，视频被移入、写入 NFO，封面被复制为 `fanart.png` 然后裁剪为 `poster.png`。

`--preview` 仅执行步骤 1–2，到抓取步骤前停止。

### 本地影片库（`scripts/library_scanner.py` + `gallery_server.py` 集成）

从 `Z:\JAV`（`LIBRARY_ROOT`）扫描已下载影片，在画廊服务里新增 `/library` 页面，并在 JAVLibrary 页面上给已下载的车牌打 badge。设计文档：[`docs/library-feature.md`](docs/library-feature.md)。

- `scripts/library_scanner.py` — 独立模块，提供：
  - `scan_library(root)` — 递归扫描 `root`（策略：遇到任一含视频文件的目录就停止深入），返回 `(Dict[carid, MovieEntry], ScanStats)`
  - `LibraryIndex` — 索引包装类；`find_match(code)` 实现**双向前缀匹配**（`a.startswith(b) or b.startswith(a)`），优先返回更具体（更长）的命中
  - `save_index` / `load_index` — 原子写入 + 加载 `output/library_index.json`（schema_version=1）
  - CLI：`uv run python scripts/library_scanner.py --root Z:\JAV`
- `gallery_server.py` 集成（实际逻辑在 `scripts/gallery/` 包）：
  - `GalleryApp.library_root` / `library_index` / `scan_state`：启动时按需加载索引（root 不一致则等手动刷新），后台线程扫描
  - `/library` 页面 + `/api/library*` 端点（列表/详情/状态/重扫/报警）；`/api/movies` 返回时附加 `local_exists` / `library_folder`；`/api/scrape` 自动跳过本地已存在的车牌（**不入 `magnets_links.txt`**，但 `magnets.json` v2 仍记录 `status=local_skip` 与 `library_folder`）
  - **路径归一化**（commit `c0ab1d7`）：用户传入的 `LIBRARY_ROOT` 和索引里已存的路径可能一个用映射盘（`Z:\JAV`）一个用 UNC（`\\nas\JAV`），形参上看着不一样但其实指向同一物理卷；服务启动 / 扫描 / 查询时统一走规范化比较，避免"索引被判定为 root 不一致 → 强制重扫"的误判。
  - `/api/local-cover` 读本地 `poster.jpg`（按 poster/folder/cover 顺序自动挑选，受 `library_root` 越界检查保护）；`/api/open-folder` 调 `os.startfile` 打开目录
  - HTML 模板 `scripts/templates/gallery.html` 重构为支持 `/wanted` + `/library` 双路由 + 顶部导航 + badge tooltip + 搜索/分页/翻页/打开文件夹 + 双页面共用的海报灯箱（commit `0471157`）
- `scripts/Sync-LibraryCoverNames.ps1` — 一次性工具：把本地库里 `<carid> <title>-poster.jpg` / `...-fanart.jpg` / `<carid> <title>.nfo` 这类本机实际命名，复制一份成 `library_scanner` 识别的标准名（`poster.jpg` / `fanart.jpg` / `movie.nfo`）。原文件不动；`-DryRun` 只打印计划，`-Force` 覆盖已存在的标准名文件。一次 `Get-ChildItem` 拿齐文件夹内容再做批量决策，避免大量 PSObject 实例化。

## 仓库布局（精选）

- `output/` — `workflow.py` 结果和 JAVLibrary `movies.json`/`movies.csv` 的默认目的地。仓库中已存在（可能是历史运行产物）。画廊的磁力结果也写在这里（`magnets.json` v2 含 `local_skip`、`magnets_links.txt`），封面缓存在 `output/.cover_cache/`（已 gitignore），本地库索引 `output/library_index.json`（已 gitignore），画廊后台运行 PID 在 `output/.gallery_server.pid`、日志在 `output/.gallery_server.log`。
- `scripts/gallery/` — FastAPI 画廊的实际实现（见上方架构一节）。`scripts/gallery_server.py` 是兼容旧命令的 shim，体积已缩到 915 字节。
- `scripts/templates/gallery.html` — 单文件模板，承载 `/wanted` + `/library` 双路由 + 海报灯箱 + 顶部导航。模板从磁盘实时读取，不需要重启服务。
- `temp/` — `test/verify_parsing.py` 的测试夹具：调试 JAVLibrary 解析时把 HTML 响应文件保存到这里。
- `.claude/` — 本项目的 Claude Code 配置（`settings.json` 启用 `frontend-design` 插件、`settings.local.json` 放行 `Bash(*)`）。添加允许的权限或 hooks 时编辑这里。
- `.pytest_cache/` — 已过期；没有 pytest 套件。可安全删除。
- `pyproject.toml` — 默认索引为阿里云 PyPI 镜像，`torch` 从 NJU 镜像拉取（显式 override）。`uv sync` 会使用这些配置；在镜像不可达的网络环境下，可用 `UV_INDEX_URL` 覆盖。运行时依赖包含 `fastapi`/`starlette`/`uvicorn`/`pydantic`/`pydantic-settings`（画廊用）。
- `docs/archive/` — 历史开发文档（Scrapling 迁移、403 排查、归档的 JAVLibrary-scraper skill 描述在 `docs/archive/SKILL.md`）。`docs/library-feature.md` 是本地库功能的设计文档。`docs/refresh-flows.md` 整理了画廊里 3 个刷新按钮（手动刷新 / 刷新库 / 单部 ↻）的完整工作流程（前端 → API → 后台 → 落盘 → 轮询）。

## 配置（`.env`）

```env
JAVBUS_URL=https://www.javbus.com/        # 车牌页 URL 前缀
JAVBUS_BASE_URL=https://www.javbus.com    # 用于解析相对封面 URL
PROXY_ENABLED=false                       # 大多数地区必需
PROXY=http://127.0.0.1:10808              # HTTP/HTTPS/SOCKS5
SCRAPLING_LOAD_DOM=true
SCRAPLING_NETWORK_IDLE=true
SCRAPLING_DISABLE_RESOURCES=true
SCRAPLING_HEADLESS=true
SCRAPLING_TIMEOUT=30000                   # 毫秒；JAVLibrary 内部用 90 秒
LIBRARY_ROOT=Z:\JAV                       # 本地影片库根目录；不设则禁用本地库功能
LIBRARY_INDEX=output/library_index.json   # 索引输出路径（已 gitignore）
USER_AGENT=Mozilla/5.0 (...)
DOWNLOAD_TIMEOUT=10
VERIFY_SSL=false
```

JAVLibrary 在 `main()` 中直接读取 `PROXY_ENABLED`/`PROXY`，忽略 Scrapling 前缀的变量。

## 关键技术细节

- **Cloudflare 绕过**：JAVBus 使用 `AsyncDynamicSession`，`disable_resources=True`（快约 25%）和 30 秒超时。JAVLibrary 使用 `stealth_mode=True`、`disable_resources=False`、90 秒超时。
- **封面 403 修复**：封面图片下载必须设置 `Referer: <JAVBUS_URL><car_id>` —— JAVBus 图片 CDN 不带该头会拒绝请求。
- **磁链优先级**：`HD + 字幕` > `HD` > `标准`。命中最高优先级时短路循环。
- **文件名编码**：所有 I/O 使用 UTF-8；车牌正则期望大写文件名。
- **输出布局**（每个视频）：`<CARID> <title>/` 包含 `<prefix>.<ext>`（视频）、`<prefix>.nfo`、`fanart.png`、`poster.png`。根目录的 `JavbusSpider.process_movie()` 把封面放到 `fanart.png`；`workflow.py` 的子类同样如此。
- **没有真正的测试套件**：`test/` 脚本访问网络，依赖 `temp/*.html` 夹具。`.pytest_cache/` 已过期。

## 范围外 / 已废弃

- `deprecated/javbus.py`、`deprecated/javbus_scrapy.py` — 旧的 Selenium/Scrapy 实现，仅作参考保留。
- `docs/archive/` — 历史开发文档（Scrapling 迁移、403 排查等）。归档的 JAVLibrary-scraper skill 描述见 `docs/archive/SKILL.md`。
