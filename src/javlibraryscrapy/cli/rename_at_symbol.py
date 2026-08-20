"""
将文件名中 @ 字符前的内容去掉，只保留 @ 之后的部分

例如：hkbisi.com@ABF-340-C.mp4 → ABF-340-C.mp4
"""

import argparse
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".m4v", ".ts", ".mpg", ".mpeg", ".3gp"}


def find_files_with_at(source_path: Path) -> list[Path]:
    """递归查找所有文件名包含 @ 的文件"""
    files = []
    for file in source_path.rglob("*"):
        if file.is_file() and "@" in file.name:
            files.append(file)
    return files


def clean_at_name(file: Path) -> str:
    """提取 @ 之后的文件名"""
    return file.name.split("@", 1)[1]


def rename_file(src: Path, dst: Path, preview: bool = False) -> tuple[str, str | None]:
    """重命名文件，返回 (操作描述, 错误信息)"""
    if preview:
        return f"预览: {src.name} → {dst.name}", None

    if dst.exists() and dst != src:
        base = dst.stem
        ext = dst.suffix
        counter = 1
        while dst.exists():
            dst = dst.parent / f"{base}_{counter}{ext}"
            counter += 1

    try:
        src.rename(dst)
        suffix = f" (添加后缀 _{counter - 1})" if counter > 1 else ""
        return f"已重命名: {src.name} → {dst.name}{suffix}", None
    except Exception as e:
        return f"失败: {src.name}", str(e)


def main():
    parser = argparse.ArgumentParser(description="去除文件名中 @ 符号之前的内容")
    parser.add_argument("source_path", type=Path, help="源文件夹路径")
    parser.add_argument("--preview", action="store_true", help="仅预览，不实际重命名")
    args = parser.parse_args()

    if not args.source_path.exists():
        logger.error(f"路径不存在: {args.source_path}")
        return

    files = find_files_with_at(args.source_path)
    if not files:
        logger.warning("未找到包含 @ 符号的文件")
        return

    logger.info(f"找到 {len(files)} 个需要重命名的文件")

    success, skip, error = 0, 0, 0
    for file in files:
        new_name = clean_at_name(file)
        dst = file.parent / new_name
        msg, err = rename_file(file, dst, args.preview)

        if err:
            logger.error(f"✗ {msg} - {err}")
            error += 1
        else:
            logger.info(f"✓ {msg}")
            success += 1

    logger.info(f"完成！成功: {success}, 跳过: {skip}, 失败: {error}")


if __name__ == "__main__":
    main()