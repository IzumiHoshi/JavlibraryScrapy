"""
工作流：下载目录 → 中间目录(移动+去@前缀) → 最终目录(削刮)

流程：
1. 从下载目录（含所有子目录）移动视频到中间目录（按大小过滤），
   移走后清理已空的原父目录
2. 清理中间目录文件名（去除 @ 前缀）
3. 从中间目录削刮 JAVBus 信息，输出到最终目录（用 MovieExporter 统一削刮）
"""

import argparse
import asyncio
import logging
import os
import shutil
from pathlib import Path
import sys

from dotenv import load_dotenv
from javlibraryscrapy._paths import REPO_ROOT as _project_root
from javlibraryscrapy.scraping.exporter import MovieExporter
from javlibraryscrapy.utils.car import javbuscar

load_dotenv(_project_root / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".m4v", ".ts", ".mpg", ".mpeg", ".3gp"}
DEFAULT_MIN_SIZE_MB = 500


def find_video_files(source_path: Path, min_size_mb: int) -> list[Path]:
    """递归查找下载目录（含所有子目录）中 ≥min_size_mb 的视频文件。

    用 ``os.walk`` 而不是 ``Path.rglob``：
      - 单个目录 stat 失败（权限 / 符号链接）只影响该目录，不中断整体扫描
      - 显式跳过 ``.`` 开头的隐藏目录（``.git`` / ``.cache`` / 客户端缓存）
      - 日志明确报扫到多少子目录，便于确认递归生效
    """
    min_bytes = min_size_mb * 1024 * 1024
    files: list[Path] = []
    scanned_dirs = 0

    if not source_path.is_dir():
        logger.warning(f"下载目录不存在或不是目录：{source_path}")
        return files

    for root, dirs, filenames in os.walk(source_path):
        scanned_dirs += 1
        # 原地修改 dirs 以阻止 os.walk 进入隐藏目录
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in filenames:
            file_path = Path(root) / name
            ext = file_path.suffix.lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            try:
                size = file_path.stat().st_size
            except OSError as e:
                logger.warning(f"无法 stat {file_path}：{e}")
                continue
            if size >= min_bytes:
                files.append(file_path)

    files.sort()
    logger.info(
        f"递归扫描完成：扫了 {scanned_dirs} 个目录，"
        f"找到 {len(files)} 个 ≥{min_size_mb}MB 的视频"
    )
    return files


def _cleanup_empty_parents(original_parent: Path, *, stop_at: Path) -> int:
    """视频移走后，若其原父目录（含祖先）已空则删；向上递归直到 stop_at。

    用于处理 qBittorrent / 115 浏览器等下载器"一个种子一个文件夹"的布局：
    视频被移走后那个 ``<torrent_name>/`` 目录只剩元数据或直接空了，整目录删掉。

    安全约束：
      - ``stop_at`` 自身永不删除（避免把下载根整个干掉）
      - 非 ``stop_at`` 子目录拒绝操作（防止跨下载根误删）
      - 只删空目录（``rmdir`` 在非空时抛 OSError 自动停止向上）

    Returns:
        实际删除的空目录数。
    """
    try:
        stop_resolved = stop_at.resolve()
    except OSError:
        stop_resolved = stop_at

    try:
        parent_resolved = original_parent.resolve()
    except OSError:
        return 0

    # 视频本身就在 stop_at 根下：没父目录可清
    if parent_resolved == stop_resolved:
        return 0
    try:
        parent_resolved.relative_to(stop_resolved)
    except ValueError:
        logger.warning(f"跳过清理：{parent_resolved} 不在 {stop_resolved} 下")
        return 0

    removed = 0
    current = parent_resolved
    while current != stop_resolved:
        try:
            current.rmdir()  # 仅删空目录；非空抛 OSError 自动跳出
        except OSError:
            break
        logger.info(f"已删除空目录：{current}")
        removed += 1
        current = current.parent
    return removed


def step1_move_videos(download_path: Path, intermediate_path: Path, min_size_mb: int):
    """步骤1：移动视频文件到中间目录。

    - 递归扫描下载根 + 所有子目录里的视频
    - 移动后若视频原父目录（及上层空目录）变空，自动删到下载根为止
    """
    logger.info("=" * 50)
    logger.info("步骤1：移动视频文件")
    logger.info(f"  下载目录: {download_path}")
    logger.info(f"  中间目录: {intermediate_path}")
    logger.info(f"  最小文件大小: {min_size_mb} MB")
    logger.info("=" * 50)

    intermediate_path.mkdir(parents=True, exist_ok=True)
    files = find_video_files(download_path, min_size_mb)

    if not files:
        logger.warning("未找到符合条件的视频文件")
        return False

    logger.info(f"找到 {len(files)} 个视频文件")
    moved, skipped, failed, cleaned_dirs = 0, 0, 0, 0

    for src in files:
        dst = intermediate_path / src.name
        file_size_mb = src.stat().st_size / (1024 * 1024)
        # 在 move 之前记录原父目录：move 后 src 仍指向同一 Path 对象，
        # 但显式保留原 parent 更直观、避免对失效路径的歧义。
        original_parent = src.parent

        if dst.exists():
            logger.warning(f"跳过（已存在）: {src.name}")
            skipped += 1
            continue

        try:
            shutil.move(str(src), str(dst))
            logger.info(f"已移动: {src.name} ({file_size_mb:.1f} MB)")
            moved += 1
            # 移动成功 → 清理变空的原父目录（及上层空目录）
            cleaned_dirs += _cleanup_empty_parents(original_parent, stop_at=download_path)
        except Exception as e:
            logger.error(f"移动失败: {src.name} - {e}")
            failed += 1

    logger.info(
        f"移动完成: 成功 {moved}, 跳过 {skipped}, 失败 {failed}, "
        f"清理空目录 {cleaned_dirs} 个"
    )
    return moved > 0


def step2_clean_at_prefix(intermediate_path: Path, preview: bool = False):
    """步骤2：去除文件名中 @ 符号之前的内容"""
    logger.info("=" * 50)
    logger.info("步骤2：清理文件名 (@ 前缀)")
    logger.info(f"  中间目录: {intermediate_path}")
    logger.info("=" * 50)

    files = [f for f in intermediate_path.rglob("*") if f.is_file() and "@" in f.name]
    if not files:
        logger.info("未找到包含 @ 的文件")
        return True

    logger.info(f"找到 {len(files)} 个需要清理的文件")
    cleaned, skipped, failed = 0, 0, 0

    for src in files:
        new_name = src.name.split("@", 1)[1]
        dst = src.parent / new_name

        if dst.exists() and dst != src:
            base = dst.stem
            ext = dst.suffix
            counter = 1
            while dst.exists():
                dst = dst.parent / f"{base}_{counter}{ext}"
                counter += 1

        try:
            if preview:
                logger.info(f"预览: {src.name} → {dst.name}")
            else:
                src.rename(dst)
                logger.info(f"已重命名: {src.name} → {dst.name}")
            cleaned += 1
        except Exception as e:
            logger.error(f"重命名失败: {src.name} - {e}")
            failed += 1

    logger.info(f"清理完成: 成功 {cleaned}, 失败 {failed}")
    return True


async def step3_scrape(intermediate_path: Path, output_path: Path):
    """步骤3：从中间目录削刮，输出到最终目录。

    走统一的 :class:`MovieExporter`：
        - move_video=True（把中间目录的视频移进子目录）
        - download_samples=True
        - collect_magnets=True（写 ``<output_path>/magnets.json`` + ``magnets_links.txt``）
    """
    logger.info("=" * 50)
    logger.info("步骤3：削刮 JAVBus")
    logger.info(f"  中间目录: {intermediate_path}")
    logger.info(f"  最终目录: {output_path}")
    logger.info("=" * 50)

    cars = javbuscar(intermediate_path)
    if not cars:
        logger.warning("未找到任何车牌")
        return False

    logger.info(f"找到 {len(cars)} 个车牌")
    for car_id, path in cars:
        logger.info(f"  {car_id}: {path}")

    exporter = MovieExporter(
        output_root=output_path,
        move_video=True,
        download_samples=True,
        collect_magnets=True,
        magnets_index=output_path / "magnets.json",
    )
    stats = await exporter.export_movies(cars)

    logger.info(
        f"削刮完成：written={stats['written']}，failed={stats['failed']}，"
        f"magnets_ok={stats['magnets_collected']}"
    )
    return stats["written"] > 0


async def workflow(download_path: Path, intermediate_path: Path, output_path: Path, min_size_mb: int, preview: bool = False):
    """
    执行完整工作流

    Args:
        download_path: 下载目录
        intermediate_path: 中间目录
        output_path: 最终输出目录
        min_size_mb: 最小文件大小 (MB)
        preview: 预览模式（只执行步骤1-2，不执行削刮）
    """
    if not download_path.exists():
        logger.error(f"下载目录不存在: {download_path}")
        return

    intermediate_path.mkdir(parents=True, exist_ok=True)
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info("\n" + "=" * 60)
    logger.info("JAVBus 工作流开始")
    logger.info(f"下载目录:   {download_path}")
    logger.info(f"中间目录:   {intermediate_path}")
    logger.info(f"最终目录:   {output_path}")
    logger.info("=" * 60)

    # 步骤1：移动视频
    if not step1_move_videos(download_path, intermediate_path, min_size_mb):
        logger.error("步骤1失败，终止工作流")
        return

    # 步骤2：清理文件名
    step2_clean_at_prefix(intermediate_path, preview=preview)
    if preview:
        logger.info("预览模式，跳过削刮步骤")
        return

    # 步骤3：削刮
    await step3_scrape(intermediate_path, output_path)

    logger.info("\n" + "=" * 60)
    logger.info("工作流完成")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="JAVBus 工作流")
    parser.add_argument("download_path", type=Path, help="下载目录")
    parser.add_argument("intermediate_path", type=Path, help="中间目录")
    parser.add_argument("output_path", type=Path, help="最终输出目录")
    parser.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE_MB, help=f"最小文件大小 (MB)，默认 {DEFAULT_MIN_SIZE_MB}")
    parser.add_argument("--preview", action="store_true", help="预览模式（只移动+清理，不削刮）")
    args = parser.parse_args()

    asyncio.run(workflow(
        args.download_path,
        args.intermediate_path,
        args.output_path,
        args.min_size,
        args.preview,
    ))


if __name__ == "__main__":
    main()