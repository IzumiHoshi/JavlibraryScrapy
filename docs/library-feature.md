# 本地影片库模块 · 设计文档

> 状态：v1.0 设计稿（已通过 grill-me 五轮拷问）
> 创建日期：2026-08-18
> 关联代码：`src/javlibraryscrapy/cli/gallery.py`、`src/javlibraryscrapy/library/scanner.py`（新增）、`src/javlibraryscrapy/templates/gallery.html`

---

## 1. 目标

扫描 `Z:\JAV`（可配置）下的本地影片目录，在画廊服务里新增**本地库页面**，并在 JAVLibrary "Most Wanted" 页面里显示「本地已有」badge。抓取磁力时已存在的车牌自动跳过（不写入 `magnets_links.txt`，但在 `magnets.json` 里保留记录与 `local_exists` 标志）。

## 2. 决策清单（27 条）

### 2.1 扫描与索引

| # | 决策 | 理由 |
|---|---|---|
| Q1 | **启动扫一次 + 手动「刷新库」按钮** | 用户每月只新增几部，`watchdog` 过度设计；按钮足够 |
| Q2 | `output/library_index.json` 落盘 + 启动加载到内存做哈希 + bisect | 2000+ 部匹配微秒级；JSON 体积可控（~3MB @ 10000 部） |
| Q3 | **双向前缀匹配**：`a.startswith(b) or b.startswith(a)` | 既覆盖 `本地 ABF-340-C ↔ JAVLibrary ABF-340`，也覆盖反过来 |
| Q15 | NFO 只试 UTF-8；失败时 title/actors 用文件名兜底 | 简单，90% 场景够用 |
| Q23 | `magnets.json` 加 `schema_version: 2` 字段 + 新增 `local_exists` / `library_folder` 字段 | 旧脚本读不到 version 仍可运行 |
| Q26 | **处理边界**：Z 盘中途消失 → 索引仍可读，扫描标 error；UNC 路径、特殊字符、长路径都按 Python `pathlib` 默认行为处理 | 全部走标准库，不引入新依赖 |

### 2.2 路由与页面

| # | 决策 |
|---|---|
| Q11 | 双路由：`/wanted`（JAVLibrary）+ `/library`（本地库），顶部导航条 |
| Q8 | `/wanted` 卡片可勾选抓磁力；`/library` 只读浏览 |
| Q6 | `/wanted` 卡片右下角显示「✓ 本地已有」绿色 badge |
| Q7 | badge 鼠标悬停 tooltip：本地路径 / 视频总大小 / 视频格式 / NFO+poster+fanart 勾选 / NFO 修改时间 / 演员前 3 |
| Q25 | `/library` 卡片**点击用 `os.startfile` 打开本地文件夹**（跨平台用 `subprocess.run(["open", path])` / `xdg-open`） |

### 2.3 搜索 / 分页 / 排序

| # | 决策 |
|---|---|
| Q17 | URL 形如 `/library?q=ABF&page=2&size=100`；车牌**前缀** OR 标题**子串** OR 演员**子串**（任一命中即可）；车牌**大写不敏感**、标题/演员原样匹配 |
| Q17 | 默认按车牌字典序排序；可选按 NFO 修改时间倒序（UI 切换） |
| Q17 | 列表 API **不返回视频文件名清单**（避免大 payload），单独 `/api/library/{carid}` 详情端点读目录返回 |

### 2.4 错误兜底与报警

| # | 决策 |
|---|---|
| Q18 | 重复车牌：取 **size 最大的文件夹**为代表，其他记日志 + UI 顶部一次性横幅 |
| Q18 | 无视频：仍入库，`has_video=False`，灰底 ⚠ badge，tooltip 写明 |
| Q19 | 后台扫描拒绝并发：第二次点击返回 409「扫描中」 |
| Q20 | 索引文件损坏 → 自动重建；`root` 与配置不一致 → 以配置为准重扫；落盘 partial 也写（部分索引优于无） |
| Q22 | 顶部显示「上次扫描：3 小时前，共 2341 部」 |
| Q26 | `LIBRARY_ROOT` 未设置 → 报错退出（不静默扫描错目录）；`LIBRARY_ROOT` 不存在 → 启动时检测并退出 |

### 2.5 UI 状态机（Q21 我自己定）

| 状态 | 触发 | UI |
|---|---|---|
| `loading-initial` | 启动首次扫描未完成 | 居中 spinner + `正在扫描 Z:\JAV… 1234/5678` |
| `loading-rescan` | 用户点了刷新库 | 顶部条状 progress，**旧数据继续展示** |
| `empty` | 扫描完成 0 部 | `本地库为空，请检查 Z:\JAV 路径` |
| `error` | 扫描失败 | `扫描失败：<原因> [重试] [修改路径]` |
| `normal` | 有数据 | 搜索框 + 列表 + 分页 + 顶部状态栏 |
| `search-empty` | `q` 有值但 0 命中 | `没有匹配 '<q>' 的影片` + 清除按钮 |

## 3. 架构

```
┌──────────────┐    ┌─────────────────────┐    ┌──────────────┐
│ gallery.html │<──>│ gallery_server.py   │<──>│ library_index│
│  (UI 双路由) │    │  GalleryApp         │    │   .json      │
└──────────────┘    │  - library_index    │    └──────────────┘
                    │  - scan_progress    │            ▲
                    │  - scrape jobs      │            │ 写
                    └──────────┬──────────┘            │
                               │ 启动时后台调用       │
                               ▼                      │
                    ┌─────────────────────┐            │
                    │ library_scanner.py  │────────────┘
                    │  scan_library()     │
                    │  save_index()       │
                    │  load_index()       │
                    └─────────────────────┘
                               │
                               ▼ 扫描 Z:\JAV
                    ┌─────────────────────┐
                    │  Z:\JAV\ABF-340 ... │
                    │      └── ABF-340.mp4 │
                    │      └── movie.nfo    │
                    │      └── poster.jpg  │
                    └─────────────────────┘
```

`library_scanner.py` 是**独立模块**——可以被服务调用，也可以 `python -m javlibraryscrapy.library.scanner` 单独跑（CLI 模式）。

## 4. 数据模型

### 4.1 `output/library_index.json`（schema_version = 1）

```json
{
  "schema_version": 1,
  "scanned_at": "2026-08-18T12:34:56",
  "root": "Z:\\JAV",
  "scan_duration_seconds": 42.3,
  "stats": {
    "total_folders_scanned": 2500,
    "movies_indexed": 2341,
    "duplicate_carids": ["Z:\\JAV\\ABF-340 (1)", "Z:\\JAV\\ABF-340 old"],
    "folders_without_video": [],
    "folders_without_nfo": ["Z:\\JAV\\MIDE-001 xxx"],
    "errors": ["无法访问 Z:\\JAV\\私密: PermissionError"]
  },
  "movies": {
    "ABF-340": {
      "carid": "ABF-340",
      "folder": "Z:\\JAV\\ABF-340 xxx",
      "title": "...",
      "actors": ["..."],
      "release_date": "2024-05-01",
      "has_nfo": true,
      "has_poster": true,
      "has_fanart": true,
      "has_video": true,
      "video_count": 1,
      "total_size_bytes": 1234567890,
      "modified": "2026-07-15T10:30:00"
    }
  }
}
```

> 注意：`videos`（视频文件名清单）**不在索引里**，详情端点按需读目录。

### 4.2 `output/magnets.json`（schema_version = 2，向后兼容）

```json
[
  {
    "code": "ABF-340",
    "title": "...",
    "magnet": "magnet:?xt=urn:btih:...",
    "status": "ok",
    "release_date": "...",
    "actors": "...",
    "javbus_url": "https://www.javbus.com/ABF-340",
    "local_exists": true,
    "library_folder": "Z:\\JAV\\ABF-340 xxx"
  }
]
```

## 5. 端点

### 5.1 新增

| 路径 | 方法 | 用途 |
|---|---|---|
| `/library` | GET | 本地库页面（HTML） |
| `/api/library` | GET | 列表（支持 `?q=...&page=N&size=100&sort=carid|mtime`） |
| `/api/library/{carid}` | GET | 单条详情（视频文件名清单） |
| `/api/library/status` | GET | 扫描状态：scanned/total/current_folder/is_running |
| `/api/library/rescan` | POST | 触发后台扫描（409 if already running） |
| `/api/library/warnings` | GET | 重复车牌 / 无 NFO 等报警汇总（用于顶部横幅） |
| `/api/local-cover` | GET | 读本地 `poster.jpg`，`?folder=...&name=poster.jpg` |

### 5.2 修改

| 路径 | 改动 |
|---|---|
| `/api/movies` | 新增 `local_exists: bool` 与 `library_folder: str \| null` 字段 |
| `/api/scrape` | 接收的 `codes` 在服务端做去重：**已存在的不加入任务**，返回 `{job_id, skipped: ["ABF-340"]}` |
| `/` | 落地导航页：链接到 `/wanted` 与 `/library` |
| `/favicon.ico` | 维持原样 |

## 6. 配置文件（`.env`）

```env
# 新增
LIBRARY_ROOT=Z:\JAV
LIBRARY_INDEX=output/library_index.json

# 已有
JAVBUS_URL=https://www.javbus.com/
...
```

不设默认值 —— 没配就启动失败，避免误扫错目录。

## 7. 关键算法

### 7.1 扫描算法（Q13 方案 a）

```python
def walk(dir):
    entries = list(dir.iterdir())
    if any(e.is_file() and e.suffix.lower() in VIDEO_EXTENSIONS for e in entries):
        movie_dirs.append(dir)   # 当前是影片目录
        return                   # 不再深入
    for e in entries:
        if e.is_dir(): walk(e)
```

### 7.2 双向前缀匹配

```python
def is_local_match(target_code, local_code):
    t, l = target_code.upper(), local_code.upper()
    return t == l or t.startswith(l) or l.startswith(t)
```

2000 条本地记录，最坏 O(N) 全扫一次也是亚毫秒；不做 bisect 优化也够。

### 7.3 扫描原子性

- 扫描线程构建本地 `new_dict: Dict[str, MovieEntry]`，**不修改共享状态**
- 扫描完成后**单次赋值** `app.library_index = new_dict`（Python dict 赋值原子）
- 进度通过独立 `app.scan_progress`（独立 dict 引用，可被读线程安全访问）
- 状态机：`idle → scanning → done/error`，由 `app.scan_status: str` 持有

## 8. 文件改动清单

### 新增

| 文件 | 作用 |
|---|---|
| `src/javlibraryscrapy/library/scanner.py` | 扫描模块（CLI + import） |
| `docs/library-feature.md` | 本设计文档 |

### 修改

| 文件 | 改动 |
|---|---|
| `src/javlibraryscrapy/cli/gallery.py` | 新增 `LibraryApp` 状态、新增 5 个端点、修改 `/api/movies` 与 `/api/scrape`、新增导航条 HTML |
| `src/javlibraryscrapy/templates/gallery.html` | 重构为支持双页面（`/wanted`、`/library`）；新增 nav bar、search bar、tooltip、状态横幅 |
| `.gitignore` | 加 `output/library_index.json` |
| `CLAUDE.md` | 补充「本地影片库」架构说明 |
| `.env`（或 `.env.example`） | 加 `LIBRARY_ROOT`、`LIBRARY_INDEX` 注释 |

## 9. 测试策略

按 CLAUDE.md 的约定，`tests/` 目录区分自动测试与手动调试脚本：

- 自动（pytest 可跑）：`tests/unit/test_library_scanner.py`、`tests/integration/test_gallery_server_library.py`、`tests/integration/test_rescan_queue.py`
- 手动调试脚本（保留为开发辅助）：`tests/unit/{debug_scraper,verify_abf,verify_cawd,verify_parsing,verify_errors,check_iptd}.py`
- 手动：`python -m javlibraryscrapy.library.scanner --root tmp/fake_jav` 看落盘 JSON
- 手动：启服务 → 访问 `/wanted` 看 badge → 访问 `/library` 看列表 → 点卡片看是否打开文件夹 → 点「刷新库」看进度

## 10. 风险与未决问题

1. **首次扫描阻塞启动时长**：2000+ 部 + 网络盘可能 1–5 分钟。Q14 决策是后台扫描，但 UI 会在 `/library` 显示「loading-initial」状态。
2. **`os.startfile` 仅 Windows**：CLAUDE.md 已确认本项目主平台 Windows，不做 macOS/Linux 兼容（虽然代码会用条件分支）。
3. **Z 盘拔除时的部分索引**：扫描中捕获 `OSError`，partial 落盘。下次启动重扫。
4. **未来增量扫描**：v1.0 不做，全量扫。性能边界足够时不必优化。
5. **NFO 字段缺失**：当前只读 `title` / `releasedate` / `actor/name`。若需 `genre` / `studio` 等可在后续版本扩展。

## 11. 实现顺序

1. **library_scanner.py** — 独立模块，先单测（tmp_path 假目录）
2. **gallery_server.py** — 加 `LibraryApp` 状态机 + 端点（最小可用版，先不支持搜索分页）
3. **gallery.html** — 改造为双页面
4. **.env / .gitignore / CLAUDE.md** — 配置与文档收尾
5. **手动端到端测试**

每完成一步 commit 一次（如果用户要求）。