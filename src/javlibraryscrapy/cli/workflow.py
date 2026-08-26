"""
工作流：下载目录 → 中间目录(移动+去@前缀) → 最终目录(削刮)

流程：
1. 从下载目录移动视频到中间目录（按大小过滤）
2. 清理中间目录文件名（去除 @ 前缀）
3. 从中间目录削刮 JAVBus 信息，输出到最终目录（用 MovieExporter 统一削刮）
"""

import argparse
import asyncio
import logging
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
    """递归查找所有符合条件的视频文件"""
    min_bytes = min_size_mb * 1024 * 1024
    files = []
    for ext in VIDEO_EXTENSIONS:
        for file in source_path.rglob(f"*{ext}"):
            if file.is_file() and file.stat().st_size >= min_bytes:
                files.append(file)
    return files


def step1_move_videos(download_path: Path, intermediate_path: Path, min_size_mb: int):
    """步骤1：移动视频文件到中间目录"""
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
    moved, skipped, failed = 0, 0, 0

    for src in files:
        dst = intermediate_path / src.name
        file_size_mb = src.stat().st_size / (1024 * 1024)

        if dst.exists():
            logger.warning(f"跳过（已存在）: {src.name}")
            skipped += 1
            continue

        try:
            shutil.move(str(src), str(dst))
            logger.info(f"已移动: {src.name} ({file_size_mb:.1f} MB)")
            moved += 1
        except Exception as e:
            logger.error(f"移动失败: {src.name} - {e}")
            failed += 1

    logger.info(f"移动完成: 成功 {moved}, 跳过 {skipped}, 失败 {failed}")
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