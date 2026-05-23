# Scripts

辅助脚本，用于视频文件处理和工作流。

## rename_at_symbol.py

去除文件名中 `@` 符号之前的内容。

**示例：** `hkbisi.com@ABF-340-C.mp4` → `ABF-340-C.mp4`

```bash
uv run python scripts/rename_at_symbol.py <源路径> [--preview]
```

## move_videos.py

将视频文件移动到目标路径，支持按大小过滤。

```bash
uv run python scripts/move_videos.py <源路径> <目标路径> [--min-size 500]
```

- 默认只移动 ≥500MB 的视频文件
- 大文件 (≥100MB) 使用 robocopy 移动，支持进度显示
- 目标文件已存在时提供覆盖/跳过/重命名选项

## workflow.py

完整工作流：从下载目录扫描视频，调用 JAVBus 爬虫，输出 NFO 和封面到指定目录。

```bash
uv run python scripts/workflow.py <下载路径> <输出路径> [--preview]
```

**流程：**
1. 扫描下载目录中的视频文件，提取车牌代码
2. 爬取 JAVBus 元数据
3. 生成 NFO 文件和封面图片到输出目录

使用 `--preview` 可预览找到的文件列表，不执行爬取。