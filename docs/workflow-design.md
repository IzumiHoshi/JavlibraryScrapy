# MovieExporter 重构设计文档

> 设计目标：把三套并行的 JAVBus 削刮路径（`cli/workflow.py` step3、`cli/export_mostwanted.py`、
> `server/services/wanted_refresh.py`）合并到统一的 `MovieExporter` 接口。本文档**只覆盖
> 削刮逻辑重构**；把 workflow 暴露到 gallery 服务作为 Web UI 是后续工作，本文档不涉及。

| 字段 | 值 |
|---|---|
| 作者 | Claude (设计) / 用户（决策） |
| 日期 | 2026-08-26 |
| 状态 | ✅ 设计已确认，待实现 |
| 受影响模块 | `scraping/`、`cli/`、`server/services/wanted_refresh.py` |

---

## 1. 背景

项目里目前有 **3 套并行的 JAVBus 削刮代码**，各自维护一份 process_movie 覆写逻辑：

| 路径 | 文件 | 行数级别 | 用途 |
|---|---|---|---|
| workflow step3 | `cli/workflow.py:124` 内嵌 `OutputSpider` | ~50 | 下载目录 → 中间目录 → 最终目录 |
| export_mostwanted | `cli/export_mostwanted.py:55` `MostWantedExporter` | ~50 | wanted JSON → 本地库 |
| wanted 单部刷新 | `server/services/wanted_refresh.py` `scrape_one_javbus` | ~80 | 单车手动重抓 |

三套代码做的是同一件事——**抓 JAVBus 详情页元数据 + 写本地库**，但命名约定 / poster 来源 /
是否移动视频等细节不同，导致：

- bug 修复要改 3 处（如 `_extract_magnet_link` 鲁棒性已经吃过这个亏）
- 新增能力（如下载 samples）要同步 3 处

---

## 2. 目标

1. **单一削刮入口**：所有"按车号削刮并写本地库"的需求走 `MovieExporter`
2. **统一命名**：`<CARID> <title>/{movie.nfo, poster.jpg, fanart.jpg, sample_NNN.jpg}`
3. **统一 poster 来源**：全部从 JAVLibrary 缩略图下载（不再从 fanart 裁剪）
4. **magnet 集中收集**：每次 export 同步输出 `magnets.json` + `magnets_links.txt`

> ~~workflow 集成到 gallery~~ —— 本期不做；后续单独设计。

---

## 3. 核心抽象：`MovieExporter`

### 3.1 接口签名

```python
# src/javlibraryscrapy/scraping/exporter.py
class MovieExporter(JavbusSpider):
    """统一的 JAVBus 削刮 + 本地库写入器。"""

    def __init__(
        self,
        output_root: Path,
        *,
        move_video: bool = False,                # 是否把源视频移进子目录
        download_samples: bool = True,           # 是否下载 JAVBus sample waterfall
        collect_magnets: bool = True,            # 是否集中收集 magnet
        magnets_index: Optional[Path] = None,    # 集中 magnets.json 路径；None → <output_root>/magnets.json
        javlibrary_proxy: Optional[str] = None,  # JAVLibrary 缩略图下载代理
    ): ...

    async def export_movies(
        self,
        car_list: List[Tuple[str, str]],         # [(car_id, video_path_or_empty)]
        *,
        cover_urls: Optional[Dict[str, str]] = None,    # code -> JAVLibrary cover_url
        on_progress: Optional[Callable[[str, str], None]] = None,  # (code, status)
    ) -> Dict[str, Any]:
        """
        Returns:
            {
                "total": int,
                "written": int,        # NFO + 至少一张图都写入了
                "skipped": int,        # 已存在文件夹，跳过
                "failed": int,
                "magnets_collected": int,
            }
        """
```

### 3.2 进程模型

```
export_movies(car_list)
  │
  ├─ 1. plan_phase(car_list)            # 计算每个车的 save_dir + skip/exists/new
  ├─ 2. prewarm_poster(cover_urls)       # 并发下载所有 JAVLibrary poster.jpg
  ├─ 3. crawl_phase(car_list)            # 单 AsyncDynamicSession 串行抓 JAVBus 详情页
  │     └─ 对每车：parse → process_movie → on_progress(code, status)
  ├─ 4. write_magnets_index()            # 写 magnets.json + magnets_links.txt
  └─ 5. cleanup_temp()                   # 清掉 <output_root>/<carid>.png 临时文件
```

### 3.3 process_movie 内部顺序

```python
async def process_movie(self, info: Dict[str, Any]) -> None:
    carid = info["carid"]
    title = info["title"].strip()
    if not carid or not title:
        logger.warning("标题或车牌为空，跳过")
        return

    save_dir = self.output_root / f"{carid} {title}"
    save_dir.mkdir(parents=True, exist_ok=True)

    # 1. 移动视频（可选）
    if self.move_video:
        video_src = Path(info["path"])
        if video_src.exists():
            video_dst = save_dir / f"{carid} {title}{video_src.suffix}"
            shutil.move(str(video_src), str(video_dst))

    # 2. 写 NFO（统一名）
    write_xml(save_dir / "movie.nfo", info)

    # 3. fanart.jpg —— JavbusSpider.download_cover 落到 <root>/<carid>.png
    cover_temp = Path(info.get("cover") or "")
    if cover_temp.exists():
        fanart_dst = save_dir / "fanart.jpg"
        if cover_temp.resolve() != fanart_dst.resolve():
            if fanart_dst.exists():
                cover_temp.unlink()  # 落地的临时文件清掉
            else:
                cover_temp.rename(fanart_dst)

    # 4. poster.jpg —— 从 JAVLibrary cover_url 下
    cover_url = (self.cover_urls or {}).get(carid)
    if cover_url:
        _download_javlibrary_cover(
            cover_url, save_dir / "poster.jpg", self.javlibrary_proxy
        )

    # 5. samples —— 从 JAVBus sample waterfall 下
    if self.download_samples and info.get("samples"):
        self._download_samples_to_target(
            info["samples"], carid, save_dir
        )

    # 6. 收集 magnet
    if self.collect_magnets:
        self._magnet_results.append({
            "code": carid,
            "title": title,
            "magnet": info.get("magnet"),
            "status": _magnet_status(info),
            "release_date": info.get("release_date", ""),
            "actors": info.get("actors", ""),
            "javbus_url": f"{self.javbus_url}{carid}",
        })
```

---

## 4. 三个调用方的配置矩阵

| 调用方 | `move_video` | `download_samples` | `collect_magnets` | `magnets_index` |
|---|---|---|---|---|
| `cli/workflow.py` step3 | ✅ True | ✅ True | ✅ True | `<output_path>/magnets.json` |
| `cli/export_mostwanted.py` | ❌ False | ✅ True | ✅ True | `<library_root>/magnets.json` |
| `wanted_refresh.scrape_one_javbus` | ❌ False | ❌ False | ✅ True | 复用 gallery 的 `magnets_index` |

---

## 5. 输出布局

```
<output_root>/
├── <CARID> <title>/                # 每部影片一个子目录
│   ├── <CARID> <title>.<ext>       # 视频（move_video=True 时）
│   ├── movie.nfo                   # 统一名
│   ├── poster.jpg                  # JAVLibrary 缩略图（统一来源）
│   ├── fanart.jpg                  # JAVBus 原图（统一扩展名）
│   └── sample_NNN.jpg              # JAVBus sample waterfall（NN 从 001 起）
├── magnets.json                    # 集中索引（schema v2）
└── magnets_links.txt               # 一行一条 magnet，纯文本
```

### 5.1 NFO 文件

`movie.nfo` 通过 `utils.filesave.write_xml(info)` 生成。`info` 是 `JavbusSpider.parse()`
返回的 dict，包含：

```
title, carid, cover (本地路径), release_date, director, producer, publisher,
category, actors, magnet, samples (URL list), path (视频本地路径)
```

Kodi/Plex 兼容字段：`title` / `plot` / `mpaa` / `country` / `premiered` / `studio` /
`director` / `actor` / `genre` / `magnet`（自定义）。

### 5.2 magnets.json schema（v2，与 gallery 现存兼容）

```json
{
  "schema_version": 2,
  "scraped_at": "2026-08-26T20:30:00",
  "items": [
    {
      "code": "ABF-340",
      "title": "Some Title",
      "magnet": "magnet:?xt=urn:btih:...",
      "status": "ok",
      "release_date": "2024-01-15",
      "actors": "Actor A / Actor B",
      "javbus_url": "https://www.javbus.com/ABF-340"
    }
  ]
}
```

`status` 枚举：
- `ok` —— 抓到 magnet
- `no_magnet` —— 页面无 magnet 链接（部分片源）
- `failed` —— 抓取 / 解析异常

### 5.3 magnets_links.txt

```
magnet:?xt=urn:btih:AAAA
magnet:?xt=urn:btih:BBBB
```

- 每行一条 magnet
- 仅 `status=ok` 的写入
- 末尾一个换行（POSIX 友好）
- 空文件 = 本次没抓到任何 magnet

---

## 6. 代理策略

| 资源 | 走的代理 | 备注 |
|---|---|---|
| JAVBus 详情页 HTML | `javbus_proxy`（`PROXY_JAVBUS_ENABLED=true` 时启用） | Scrapling `AsyncDynamicSession` 内部 |
| JAVBus cover / samples（图片） | `javbus_proxy` | 同步 `requests.get` |
| JAVLibrary cover（poster） | `javlibrary_proxy`（`PROXY_JAVLIBRARY_ENABLED=true` 时启用） | 同步 `requests.get` |

> **决策**：不显式给 `MovieExporter` 加 `javbus_proxy` 参数。`JavbusSpider.__init__` 已经从
> `.env` 读 `PROXY + PROXY_JAVBUS_ENABLED` 自己设 `self.proxy`。子类继承即可。gallery
> 启动时统一加载 `.env`，调用方共享同一份代理配置。

---

## 7. ~~Workflow 在 Gallery 中的集成~~ （已移除，本期不做）

> 原计划把 `cli/workflow.py` 的三阶段流程暴露到 gallery Web UI（新增 `WorkflowJob` +
> `/api/workflow` 路由 + 前端 `/workflow` 页面）。本期设计**不包含此工作**，仅做削刮逻辑
> 重构。如后续需要 gallery 集成，会另起一份设计文档。

---

## 8. 迁移计划

### 8.1 新增文件

- `src/javlibraryscrapy/scraping/exporter.py` — `MovieExporter` + `ExportConfig`
- `tests/unit/test_movie_exporter.py` — 单元测试

### 8.2 改造文件

- `cli/workflow.py` step3 — 删内嵌 `OutputSpider`，改 `MovieExporter(output_root=output_path, move_video=True, ...)`
- `cli/export_mostwanted.py` — `MostWantedExporter` 改为薄包装，构造时传 `ExportConfig`
- `server/services/wanted_refresh.py` `scrape_one_javbus` — 走 `MovieExporter.export_movies([(code, "")])`

### 8.3 可清理文件（合并完后）

- `cli/workflow.py` 内嵌的 `OutputSpider` 类（已重构走 `MovieExporter`）
- `server/services/jobs_runner.py` 的 `MagnetSpider` 类（与 `MovieExporter` 重叠 80%）
- `server/services/jobs_runner.py` 的 `write_job_outputs`（由 `MovieExporter._write_magnets_index` 替代）
- `utils/fanart.py` 的 `split_poster_from_fanart`（不再调用）
- `JavbusSpider.process_movie` 基类默认实现（不再有调用方）

> ⚠️ 删 `MagnetSpider` / `write_job_outputs` 前要先确认 gallery wanted 抓磁力的"附加能力"
> （library_skip / extra_cached 等）是否全部能用 `MovieExporter` 表达。能就删；不能则保留
> 但标注 deprecated。

### 8.4 行为变更（兼容性注意）

| 变更点 | 旧行为 | 新行为 | 兼容性影响 |
|---|---|---|---|
| NFO 文件名 | workflow 用 `<CARID> <title>.nfo`，wanted 用 `movie.nfo` | 统一 `movie.nfo` | workflow 用户需重命名旧 NFO；或提供迁移脚本 |
| fanart 扩展名 | workflow 用 `.png`，wanted 用 `.jpg` | 统一 `.jpg` | 同上；脚本里 `rename .png → .jpg` |
| poster 来源 | workflow 从 fanart 裁，wanted 从 JAVLibrary 下 | 统一从 JAVLibrary 下 | workflow 旧库需重新跑 |
| 删 `split_poster_from_fanart` 调用 | workflow step3 在用 | 不再调用 | 无（功能等价切换） |

> 旧库一次性迁移脚本：`scripts/migrate_to_unified_naming.py`（在迁移阶段提供）。

---

## 9. 测试策略

### 9.1 单元测试（`tests/unit/test_movie_exporter.py`）

| 用例 | 覆盖点 |
|---|---|
| `test_export_movies_basic` | 单车 export，验证 4 个文件都生成 |
| `test_move_video_true` | 视频从源目录移走 |
| `test_move_video_false` | 视频不动 |
| `test_download_samples_true` | sample_NNN.jpg 写入 |
| `test_download_samples_false` | 不写 sample |
| `test_collect_magnets_true` | magnets.json + magnets_links.txt 写入 |
| `test_collect_magnets_false` | 不写 magnets 文件 |
| `test_skip_existing_folder` | 文件夹已存在时跳过（除 NFO 外） |
| `test_status_mapping` | ok / no_magnet / failed 三种状态 |
| `test_magnet_priority` | HD+字幕 > HD > 标准 顺序保留 |
| `test_cover_urls_missing` | 没给 cover_urls 时跳过 poster 下载 |
| `test_idempotent_rerun` | 第二次 export 不报错且跳过已存在文件 |

### 9.2 Mock 策略

- `JavbusSpider.parse()` → mock 返回固定 dict
- `download_cover` / `download_samples` → mock 写入空文件
- `_download_javlibrary_cover` → mock 返回 True
- 不真实访问 JAVBus / JAVLibrary

---

## 10. 风险与权衡

### 10.1 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 旧库兼容（旧 NFO / fanart.png 名） | 用户需手动迁移 | 提供 `migrate_to_unified_naming.py` 一次性脚本 |
| `MagnetSpider` 删除时漏掉 library_skip 逻辑 | gallery wanted 抓磁力行为变化 | 集成测试覆盖 wanted 抓磁力路径 |
| magnet 收集改双写，JSON 体积膨胀 | wanted 经常重抓时 magnets.json 频繁写 | 用 timestamp 后缀（如 `magnets_2026-08-26T20-30.json`）？或继续覆写，文档说明 |

### 10.2 权衡

| 选项 | 选择 | 理由 |
|---|---|---|
| magnet 文件覆写 vs 追加 | 覆写 | 每次跑都是"当前 job 的 magnet 视图"，追加会让历史磁力混进新一次失败状态 |
| sample 扩展名 | 强制 `.jpg` | 简单一致；偶尔的 `.webp` 改名不影响 Kodi/Plex 显示 |
| poster 来自 JAVLibrary vs 从 fanart 裁 | JAVLibrary | 用户已统一约定；裁剪的 poster 偏小 |
| `move_video` 默认 | `False` | 多数调用方（wanted / 单部刷新）不移动；workflow 显式开 |
| `download_samples` 默认 | `True` | workflow 场景下需要预览；其它场景关闭即可 |

---

## 11. 待办与时间线

| 阶段 | 任务 | 估时 |
|---|---|---|
| 1 | `MovieExporter` 骨架 + process_movie 重构 | 2h |
| 2 | magnet / samples / poster 下载逻辑 | 1.5h |
| 3 | 单元测试 | 1.5h |
| 4 | `cli/workflow.py` + `cli/export_mostwanted.py` 迁移 | 1h |
| 5 | `wanted_refresh.scrape_one_javbus` 迁移 | 0.5h |
| 6 | 旧库迁移脚本 + 文档更新 | 0.5h |

总计约 7 小时。

---

## 12. 参考

- 现有 magnet 抓取逻辑：`src/javlibraryscrapy/scraping/javbus.py` `_extract_magnet_link`
- 现有 export 逻辑：`src/javlibraryscrapy/cli/export_mostwanted.py`
- 现有 wanted 单部刷新：`src/javlibraryscrapy/server/services/wanted_refresh.py`
- Gallery 任务模式参考：`src/javlibraryscrapy/server/services/jobs.py` `ScrapeJob` / `RescanQueue`
- 文档：`docs/architecture.md`、`docs/library-feature.md`、`docs/refresh-flows.md`

---

**确认日期**：2026-08-26
**确认人**：用户
**下一步**：实现阶段 1（`MovieExporter` 骨架）。
