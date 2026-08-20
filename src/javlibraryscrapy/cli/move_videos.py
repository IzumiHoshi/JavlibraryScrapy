"""
将视频文件移动到目标路径，支持按大小过滤

大文件 (>=100MB) 使用 robocopy 移动以支持进度显示
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".m4v", ".ts", ".mpg", ".mpeg", ".3gp"}
DEFAULT_MIN_SIZE_MB = 500
MIN_ROBOCOPY_SIZE_MB = 100


def find_video_files(source_path: Path, min_size_mb: int) -> list[Path]:
    """递归查找所有符合条件的视频文件"""
    min_bytes = min_size_mb * 1024 * 1024
    files = []
    for ext in VIDEO_EXTENSIONS:
        for file in source_path.rglob(f"*{ext}"):
            if file.is_file() and file.stat().st_size >= min_bytes:
                files.append(file)
    return files


def ensure_dir(path: Path) -> bool:
    """确保目录存在，不存在则创建"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"无法创建目录 {path}: {e}")
        return False


def move_large_file(src: Path, dst_dir: Path, file_index: int, total: int) -> bool:
    """使用 robocopy 移动大文件，Move-Item 移动小文件"""
    file_size_mb = src.stat().st_size / (1024 * 1024)
    percent = int((file_index / total) * 100)
    logger.info(f"[{file_index}/{total}] 移动: {src.name} ({file_size_mb:.2f} MB) - {percent}%")

    try:
        if file_size_mb >= MIN_ROBOCOPY_SIZE_MB:
            robocopy_cmd = [
                "robocopy",
                str(src.parent),
                str(dst_dir),
                str(src.name),
                "/MOV", "/Y", "/NP", "/NFL", "/NDL", "/NJH", "/NJS",
            ]
            result = subprocess.run(robocopy_cmd, capture_output=True, text=True)
            if result.returncode <= 7:
                return True
            logger.error(f"robocopy 返回码: {result.returncode}")
            return False
        else:
            shutil.move(str(src), str(dst_dir / src.name))
            return True
    except Exception as e:
        logger.error(f"移动失败 {src.name}: {e}")
        return False


def resolve_conflict(filename: str, dst_dir: Path, src_path: Path) -> Path | None:
    """处理文件名冲突，返回 None 表示跳过"""
    logger.warning(f"文件名冲突: {filename}")

    while True:
        print("[1] 覆盖文件")
        print("[2] 取消移动（默认）")
        print("[3] 重命名后移动")
        choice = input("请选择 (1/2/3): ").strip()

        if choice == "1":
            return dst_dir / filename
        elif choice == "3":
            new_name = input("请输入新的文件名（不含扩展名）: ").strip()
            if not new_name:
                continue
            dst = dst_dir / (new_name + Path(filename).suffix)
            counter = 1
            while dst.exists():
                dst = dst_dir / f"{new_name}_{counter}{Path(filename).suffix}"
                counter += 1
            return dst
        else:
            return None


def main():
    parser = argparse.ArgumentParser(description="移动视频文件到目标路径")
    parser.add_argument("source_path", type=Path, help="源文件夹路径")
    parser.add_argument("destination_path", type=Path, help="目标文件夹路径")
    parser.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE_MB, help=f"最小文件大小 (MB)，默认 {DEFAULT_MIN_SIZE_MB}")
    args = parser.parse_args()

    if not args.source_path.exists():
        logger.error(f"源路径不存在: {args.source_path}")
        return

    if not ensure_dir(args.destination_path):
        sys.exit(1)

    logger.info(f"源路径: {args.source_path}")
    logger.info(f"目标路径: {args.destination_path}")
    logger.info(f"最小文件大小: {args.min_size} MB")

    files = find_video_files(args.source_path, args.min_size)
    if not files:
        logger.warning("未找到符合条件的视频文件")
        return

    logger.info(f"找到 {len(files)} 个符合条件的视频文件")

    success, skip, error = 0, 0, 0
    for i, src in enumerate(files, 1):
        dst = args.destination_path / src.name

        if dst.exists():
            resolved = resolve_conflict(src.name, args.destination_path, src)
            if resolved is None:
                logger.info(f"⊘ 已取消: {src.name}")
                skip += 1
                continue
            dst = resolved

        if move_large_file(src, args.destination_path, i, len(files)):
            success += 1
        else:
            error += 1

    logger.info("=" * 50)
    logger.info("操作完成！")
    logger.info(f"成功移动: {success} 个文件")
    if skip:
        logger.warning(f"取消移动: {skip} 个文件")
    if error:
        logger.error(f"失败: {error} 个文件")


if __name__ == "__main__":
    main()