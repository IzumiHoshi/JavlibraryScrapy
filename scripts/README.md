# Scripts

辅助脚本，用于视频文件处理和工作流。

> **调用约定：** 所有 CLI 都用模块形式 `uv run python -m javlibraryscrapy.cli.<name>` 启动。
> 装好包后也可直接用 console_script 入口（见 `pyproject.toml [project.scripts]`）：
> `javlibraryscrapy-gallery`、`javlibraryscrapy-export`、`javlibraryscrapy-workflow`、`javlibraryscrapy-move`、`javlibraryscrapy-rename`。

## rename_at_symbol

去除文件名中 `@` 符号之前的内容。

**示例：** `hkbisi.com@ABF-340-C.mp4` → `ABF-340-C.mp4`

```bash
uv run python -m javlibraryscrapy.cli.rename_at_symbol <源路径> [--preview]
```

## move_videos

将视频文件移动到目标路径，支持按大小过滤。

```bash
uv run python -m javlibraryscrapy.cli.move_videos <源路径> <目标路径> [--min-size 500]
```

- 默认只移动 ≥500MB 的视频文件
- 大文件 (≥100MB) 使用 robocopy 移动，支持进度显示
- 目标文件已存在时提供覆盖/跳过/重命名选项

## gallery

影片画廊本地服务器：把 `output/` 里的 JAVLibrary 抓取结果以卡片形式展示，勾选影片后一键抓取磁力链接。

```bash
uv run python -m javlibraryscrapy.cli.gallery
uv run python -m javlibraryscrapy.cli.gallery --port 8000 --data output/javlibrary_movies.json
```

> PowerShell 用户：`.\Start-GalleryServer.ps1 -Action Start`（详见脚本注释）。

默认监听 `0.0.0.0:8000`，同一局域网内可通过启动日志显示的地址访问，例如 `http://192.168.0.116:8000`。如果 Windows 防火墙弹出提示，请允许 Python 在“专用网络”中通信。

**默认启动后不自动打开浏览器**，如需自动打开加上 `--open-browser`（PowerShell 脚本用 `-OpenBrowser`）。

**页面功能：**

- 卡片展示封面、车牌、标题，点卡片任意位置即可勾选（选中状态存在浏览器 localStorage，刷新不丢）
- 搜索框按车牌/标题过滤；「全选 / 清空 / 反选」作用于当前过滤结果
- **抓取选中的磁力** —— 调用 `javlibraryscrapy.scraping.javbus.JavbusSpider.crawl_and_process`，右侧面板显示实时进度、日志和每个车牌的结果，可单条或整批复制磁力链接
- **导出 code** —— 把选中的车牌下载成 `selected_codes.txt`（不联网，纯浏览器侧导出）
- 抓完可「重试失败项」，只重跑没拿到磁力的车牌

**输出文件：**

| 文件 | 内容 |
| --- | --- |
| `output/magnets.json` | 车牌、标题、磁力、状态、发行日期、演员、JavBus 链接 |
| `output/magnets_links.txt` | 纯磁力链接，每行一条，可整体粘进下载器 |

两个文件每次抓取都会覆盖写入。

**参数：**

- `--data` 影片数据文件（默认 `output/javlibrary_movies.json`，缺失时回退同名 `.csv`）
- `--output-dir` 结果输出目录（默认 `output/`）
- `--host` / `--port` 监听地址与端口（默认 `0.0.0.0:8000`，允许局域网访问）
- `--image-proxy {auto,on,off}` 封面是否经服务端代理拉取。`auto`（默认）在 `.env` 里 `PROXY_JAVBUS_ENABLED=true` 时启用；封面缓存在 `output/.cover_cache/`
- `--open-browser` 启动后自动打开浏览器（默认不打开）

> 代理、超时、User-Agent 等都读 `.env`，与 `javlibraryscrapy.scraping.javbus` 共用一套配置。同一时间只允许一个抓取任务。

## Docker 画廊（容器化部署）

容器只装 FastAPI 画廊（前端 + API + 调度）和 Scrapling 浏览器；爬虫/CLI 工作流仍在宿主机跑（robocopy、`os.startfile`、PowerShell 脚本都依赖 Windows）。

**架构**
- `Dockerfile`：python:3.11-slim + uv，三层缓存（依赖 → chromium → 代码）
- `docker-compose.yml`：端口 `8000:8000`、`shm_size=1gb`（Playwright 必需）、`extra_hosts: host.docker.internal`（让容器内 `PROXY` 命中宿主代理）、卷挂载：`{JAV,MostWanted,UnScraper}` 三个卷 + `gallery-output` 命名卷承载 `/app/output`（日志/封面缓存/库索引）
- `.env.docker.example`：容器版配置模板，复制为 `.env.docker` 后填宿主路径
- `scripts/Start-DockerGallery.ps1`：一键打包/启停脚本

**首次跑：**

```powershell
# 1. 镜像构建（首次 5-10 分钟，主要装 chromium）
pwsh scripts/Start-DockerGallery.ps1 -Action Build

# 2. 准备配置：复制模板并编辑挂载源路径
Copy-Item .env.docker.example .env.docker
notepad .env.docker
#   必须按本机情况改：LIBRARY_HOST_PATH / MOSTWANTED_HOST_PATH / DOWNLOAD_HOST_PATH
#   Windows 上不要填映射盘 Z:，Docker Desktop 认不出；用 UNC (//nas/...) 或本地盘 (D:/JAV)

# 3. 后台启动
pwsh scripts/Start-DockerGallery.ps1 -Action Up
# 浏览器打开 http://localhost:8000

# 4. 后续常用
pwsh scripts/Start-DockerGallery.ps1 -Action Status          # 容器/镜像/端口
pwsh scripts/Start-DockerGallery.ps1 -Action Logs -Tail 100   # 跟踪日志
pwsh scripts/Start-DockerGallery.ps1 -Action Restart         # 仅重启容器
pwsh scripts/Start-DockerGallery.ps1 -Action Down            # 停容器（卷保留）
```

**路径映射规则（容易踩坑）：**
| 宿主机 | 容器内 | 说明 |
|---|---|---|
| `${LIBRARY_HOST_PATH}` | `/data/JAV` | 本地影片库（ro 挂载） |
| `${MOSTWANTED_HOST_PATH}` | `/data/MostWanted` | wanted JSON + `<CARID> <title>/`（必须可写） |
| `${DOWNLOAD_HOST_PATH}` | `/data/UnScraper` | NAS 下载目录（wanted 整理要枚举） |
| `gallery-output` 命名卷 | `/app/output` | 临时数据 |

`LIBRARY_ROOT` / `MOSTWANTED_LIBRARY_ROOT` / `LOCAL_DOWNLOAD_PATH` 在 `.env.docker` 里一律填**容器内路径**（`/data/...`），不是宿主的 `Z:/`。

**代理：** 容器内 `PROXY=http://host.docker.internal:10808`（不是 `127.0.0.1`）。`docker-compose.yml` 已配 `extra_hosts: host-gateway`，让 host.docker.internal 解析到宿主网关。

**容器里不可用的功能：**
- `/api/open-folder` → `os.startfile` 仅 Windows，调用会报错（不影响其他功能）
- `cli/move_videos.py` 的 robocopy 分支、`scripts/*.ps1`（除 Start-DockerGallery） → 继续在宿主机跑

## export_mostwanted

把 JAVLibrary「最想要」列表导出到本地库：每部影片一个文件夹，命名 `<CARID> <title>/`，内含：

- `movie.nfo` —— 从 JAVBus 详情页抓到的完整元数据（Kodi/Plex 兼容）
- `poster.jpg` —— JAVLibrary 列表的竖版缩略图
- `fanart.jpg` —— JAVBus 详情页的横版原图

复用 `javlibraryscrapy.scraping.javbus.JavbusSpider` 处理 JAVBus 部分，只覆写 `process_movie` 把 `fanart.png → fanart.jpg`、NFO 改名 `movie.nfo`、不做 poster/fanart 拆分。

```bash
# 读取默认 output/javlibrary_movies.json，导出到 .env 的 MOSTWANTED_LIBRARY_ROOT
uv run python -m javlibraryscrapy.cli.export_mostwanted

# 显式指定路径
uv run python -m javlibraryscrapy.cli.export_mostwanted \
  --source output/javlibrary_movies.json \
  --library-root "Z:\\JAV\\MostWanted"

# 强制覆盖已存在的文件夹
uv run python -m javlibraryscrapy.cli.export_mostwanted --overwrite

# 只打印计划，不写文件
uv run python -m javlibraryscrapy.cli.export_mostwanted --dry-run

# 调试：只处理前 5 部
uv run python -m javlibraryscrapy.cli.export_mostwanted --limit 5

# 只下 poster.jpg（不拉 JAVBus）
uv run python -m javlibraryscrapy.cli.export_mostwanted --skip-javbus
```

**参数：**

- `--source` JAVLibrary 抓取结果 JSON（默认 `MOSTWANTED_LIBRARY_ROOT/javlibrary_movies.json`，未设则退回 `output/javlibrary_movies.json`）
- `--library-root` 本地库根目录（默认读 `.env` 的 `MOSTWANTED_LIBRARY_ROOT`，未设置则报错）
- `--overwrite` 目标文件夹已存在时仍写入（默认跳过）
- `--dry-run` 只打印计划，不写文件
- `--delay` JAVBus 每部间隔秒数（默认 3）
- `--skip-javbus` 跳过 JAVBus 抓取（只写 poster.jpg）
- `--limit` 只处理前 N 部

**排除列表：** `HEYZO / PONDO / CARIB / OKYOHOT`（这些在 JAVBus 上没页面，跳过并提示）。

**注意：** 跑完会清理 JAVBus 留在 `library_root` 下的临时 `<CARID>.png`。

## workflow

完整工作流：从下载目录扫描视频，调用 JAVBus 爬虫，输出 NFO 和封面到指定目录。

```bash
uv run python -m javlibraryscrapy.cli.workflow <下载路径> <中间路径> <输出路径> [--min-size 500] [--preview]
```

**流程：**
1. 扫描下载目录中的视频文件，提取车牌代码
2. 爬取 JAVBus 元数据
3. 生成 NFO 文件和封面图片到输出目录

使用 `--preview` 可预览找到的文件列表，不执行爬取。

## restore_wanted_from_folders

⚠️ **只在 wanted JSON 误删/回滚后用**。`output/javlibrary_movies.json` 不进 git（`output/` 整个被 gitignore）；若被 `git reset` 或分支切换弄丢，NFS 上 `<MOSTWANTED_LIBRARY_ROOT>/<CODE> <title>/` folder 仍在，本脚本可反推 JSON。

```bash
uv run python scripts/restore_wanted_from_folders.py [--dry-run]
uv run python scripts/restore_wanted_from_folders.py --mw-root "Z:\\JAV\\MostWanted"
uv run python scripts/restore_wanted_from_folders.py --json "D:\\backup\\javlibrary_movies.json"
```

**行为：**
- 扫 `mw_root` 下所有 `<CODE> <title>/` folder
- 已存在于 JSON 的 code 跳过（保留最新抓取数据）
- 不存在的 code 写入 JSON，标记 `_restored_from_folder=true`、`_bucket=unknown`、`missing_in_remote=true`
- `release_date` 留空（NFS mtime 不可信），下次 `refresh_wanted` 触发时 `merge_wanted` 会看到空 → 自动加进 `needs_javbus` → 重抓补回真实日期

**何时不需要跑：** JSON 正常、`refresh_wanted` 也正常补 unknown 时不需要动这个脚本。