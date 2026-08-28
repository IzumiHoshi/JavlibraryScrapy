"""
工作流：下载目录 → 最终目录（按 release_date 推月份桶，<YYYY-MM>/<CARID> <title>/）

流程：
1. 从下载目录（含所有子目录）移动视频到最终目录的临时 ``_staging/`` 子目录
   （按大小过滤），移走后清理已空的原父目录。返回移走的文件路径列表。
2. 对刚移来的视频去 @ 前缀（精准处理，不扫整个 staging 目录）
3. 抓 JAVBus 元数据 → 视频按 ``<YYYY-MM>/<CARID> <title>/`` 整理到最终目录
   顶层（bucket_by_month=True）→ 写 NFO + 下 cover/samples
4. 清理 ``_staging/`` 残留（应该已经空了，作为兜底）

步骤 1/2/3 之间用内部变量传递"刚移来的文件路径列表"；最终输出按 release_date
月份桶布局，跟 ``LIBRARY_ROOT/<YYYY-MM>/...`` 一致。
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
from javlibraryscrapy.utils.car import find_car_bus

load_dotenv(_project_root / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".m4v", ".ts", ".mpg", ".mpeg", ".3gp"}
DEFAULT_MIN_SIZE_MB = 500

# 传给 ``find_car_bus`` 的厂牌白名单：JAVBus 上没这些厂牌的页面，跳过。
# 跟 ``javbuscar`` 默认值一致；放模块级避免每次调用重建列表。
_LIST_SUREN_CAR = ["LUXU", "MIUM"]

# step1 / step2 用的临时子目录：藏到点号开头，避免被 find_video_files /
# 用户脚本误处理；MovieExporter 跑完会清空它。
_STAGING_DIR = "_staging"


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


def step1_move_videos(
    download_path: Path,
    output_path: Path,
    min_size_mb: int,
    *,
    dry_run: bool = False,
) -> list[Path]:
    """步骤1：移动视频文件到 ``<output_path>/_staging/`` 顶层（保留原文件名）。

    staging 子目录是为了避免视频临时落到 ``output_path``（= 用户本地库）顶层
    污染月份桶布局；MovieExporter 在 step3 会从 staging 取视频，再按月份桶
    写到 ``output_path/<YYYY-MM>/<CARID> <title>/``，跑完兜底清空 staging。

    - 递归扫描下载根 + 所有子目录里的视频
    - 移动后若视频原父目录（及上层空目录）变空，自动删到下载根为止
    - 返回 ``[Path, ...]``：实际（或 dry-run 计划）移走的文件路径列表，传给
      step2 / step3 做精准处理（不扫整个 staging 目录）

    ``dry_run=True`` 时只打印计划，不实际 ``shutil.move``，也不清理原父目录
    （清理逻辑依赖实际移动后父目录变空的前提）。返回的列表是计划路径。
    """
    logger.info("=" * 50)
    logger.info("步骤1：移动视频文件" + ("（DRY-RUN）" if dry_run else ""))
    logger.info(f"  下载目录: {download_path}")
    logger.info(f"  最终目录: {output_path}")
    logger.info(f"  临时子目录: {_STAGING_DIR}/")
    logger.info(f"  最小文件大小: {min_size_mb} MB")
    logger.info("=" * 50)

    output_path.mkdir(parents=True, exist_ok=True)
    staging = output_path / _STAGING_DIR
    files = find_video_files(download_path, min_size_mb)

    if not files:
        # 没视频 → 不创建 _staging（避免污染 output 顶层）
        logger.warning("未找到符合条件的视频文件")
        return []

    # 有视频才创建 staging
    if not dry_run:
        staging.mkdir(parents=True, exist_ok=True)

    logger.info(f"找到 {len(files)} 个视频文件")
    moved_paths: list[Path] = []
    moved, skipped, failed, cleaned_dirs = 0, 0, 0, 0

    for src in files:
        dst = staging / src.name
        file_size_mb = src.stat().st_size / (1024 * 1024)
        # 在 move 之前记录原父目录：move 后 src 仍指向同一 Path 对象，
        # 但显式保留原 parent 更直观、避免对失效路径的歧义。
        original_parent = src.parent

        if dst.exists():
            logger.warning(f"跳过（已存在）: {src.name}")
            skipped += 1
            continue

        if dry_run:
            logger.info(
                f"[DRY-RUN] 计划移动: {src} → {dst} ({file_size_mb:.1f} MB)"
            )
            moved += 1
            moved_paths.append(dst)
            # 不实际移动 → 不清原父目录（清理依赖 move 副作用）
            continue

        try:
            shutil.move(str(src), str(dst))
            logger.info(f"已移动: {src.name} ({file_size_mb:.1f} MB)")
            moved += 1
            moved_paths.append(dst)
            # 移动成功 → 清理变空的原父目录（及上层空目录）
            cleaned_dirs += _cleanup_empty_parents(original_parent, stop_at=download_path)
        except Exception as e:
            logger.error(f"移动失败: {src.name} - {e}")
            failed += 1

    logger.info(
        f"{'计划' if dry_run else '移动'}完成: "
        f"{'会' if dry_run else ''}成功 {moved}, 跳过 {skipped}, 失败 {failed}, "
        f"清理空目录 {cleaned_dirs} 个"
    )
    return moved_paths


def step2_clean_at_prefix_for_paths(files: list[Path]) -> int:
    """步骤2：对指定文件列表去 ``@`` 前缀。

    只处理传入的 ``files``（step1 刚移来的视频），不扫整个 output 目录——
    避免误伤 output 下之前已整理好的 ``<CARID> <title>/`` 里的旧文件。

    文件名不含 ``@`` 时跳过；目标已存在时自动追加 ``_N`` 后缀。
    返回实际清理（重命名）成功的文件数。
    """
    logger.info("=" * 50)
    logger.info("步骤2：清理文件名 (@ 前缀)")
    logger.info(f"  待处理文件: {len(files)} 个")
    logger.info("=" * 50)

    cleaned, failed = 0, 0
    for src in files:
        # 移动后文件路径可能不再存在（被用户手动删了）；同时防御非文件
        if not src.is_file() or "@" not in src.name:
            continue
        new_name = src.name.split("@", 1)[1]
        dst = src.parent / new_name

        if dst.exists() and dst != src:
            base, ext = dst.stem, dst.suffix
            counter = 1
            while dst.exists():
                dst = dst.parent / f"{base}_{counter}{ext}"
                counter += 1

        try:
            src.rename(dst)
            logger.info(f"已重命名: {src.name} → {dst.name}")
            cleaned += 1
        except Exception as e:
            logger.error(f"重命名失败: {src.name} - {e}")
            failed += 1

    logger.info(f"清理完成: 成功 {cleaned}, 失败 {failed}")
    return cleaned


async def step3_scrape_from_paths(
    output_path: Path,
    source_paths: list[Path],
) -> bool:
    """步骤3：从 step1/2 处理后的视频文件列表构建 car_list，调用 MovieExporter 削刮。

    视频在 ``<output_path>/_staging/`` 顶层（被 step1 移来的）。MovieExporter 拿到
    ``car_list=[(car_id, video_path), ...]`` 后会用 ``bucket_by_month=True`` 把每
    部视频按 ``release_date`` 推到月份桶：

        <output_path>/<YYYY-MM>/<CARID> <title>/<CARID> <title>.<ext>
        <output_path>/<YYYY-MM>/<CARID> <title>/movie.nfo
        <output_path>/<YYYY-MM>/<CARID> <title>/poster.jpg / fanart.jpg / sample_NNN.jpg

    跟 ``LIBRARY_ROOT/<YYYY-MM>/`` 布局一致。

    与旧实现的差别：旧 ``step3_scrape`` 用 ``javbuscar(intermediate_path)`` 扫整个
    目录，会把 staging 下之前已整理好的 ``<CARID> <title>/`` 也一起扫一遍（重复
    抓取）。新实现用 ``find_car_bus`` 单文件提取车牌，只处理 step1/2 移来的视频。
    """
    logger.info("=" * 50)
    logger.info("步骤3：削刮 JAVBus")
    logger.info(f"  最终目录: {output_path}")
    logger.info(f"  临时子目录: {_STAGING_DIR}/（MovieExporter 取走后清空）")
    logger.info(f"  待处理视频: {len(source_paths)} 个")
    logger.info("=" * 50)

    cars: list[tuple[str, str]] = []
    skipped = 0
    for path in source_paths:
        if not path.is_file():
            # step1 已移走，但被外部脚本删了；不影响其他视频
            skipped += 1
            continue
        car_id = find_car_bus(path.name.upper(), _LIST_SUREN_CAR)
        if car_id:
            cars.append((car_id, str(path)))
        else:
            logger.warning(f"无法从文件名提取车牌：{path.name}")
            skipped += 1

    if not cars:
        logger.warning("未找到任何车牌")
        return False

    logger.info(f"找到 {len(cars)} 个车牌（跳过 {skipped} 个）")
    for car_id, path in cars:
        logger.info(f"  {car_id}: {path}")

    # MovieExporter 的 output_root = 用户本地库（不是 staging）：
    # bucket_by_month=True 让 save_dir 推到 <YYYY-MM>/<CARID> <title>/，最终落在
    # output_path 顶层。video 源路径（info["path"]）仍在 staging 里，MovieExporter
    # 从 staging 取视频移到月份桶。cover/sample 临时文件落到用户本地库顶层
    # （MovieExporter.root_dir = output_root），由 export_movies 末尾 _cleanup_temp_pngs
    # 清掉。
    #
    # 重要：不要把 staging 当 output_root —— 那样 save_dir 变成
    # ``staging/<bucket>/<CARID> <title>/``，会被下面的兜底清理 rmtree 掉 → 视频丢失！
    staging = output_path / _STAGING_DIR
    exporter = MovieExporter(
        output_root=output_path,
        move_video=True,
        download_samples=True,
        collect_magnets=True,
        magnets_index=output_path / "magnets.json",
        bucket_by_month=True,
    )
    stats = await exporter.export_movies(cars)

    # 兜底清理 staging：视频已被 MovieExporter 移到月份桶，cover/sample temp
    # 由 export_movies 末尾的 _cleanup_temp_pngs 清掉。staging 应该已经空了
    # （或只剩零星 cover temp/sample temp 残留），这里再扫一次兜底。
    if staging.exists():
        for leftover in staging.iterdir():
            try:
                if leftover.is_dir():
                    shutil.rmtree(leftover)
                else:
                    leftover.unlink()
            except OSError as e:
                logger.warning(f"清理 staging 残留失败 {leftover}: {e}")
        try:
            staging.rmdir()
        except OSError:
            pass

    logger.info(
        f"削刮完成：written={stats['written']}，failed={stats['failed']}，"
        f"magnets_ok={stats['magnets_collected']}"
    )
    return stats["written"] > 0


async def workflow(
    download_path: Path,
    output_path: Path,
    min_size_mb: int,
    dry_run: bool = False,
):
    """
    执行完整工作流

    Args:
        download_path: 下载目录（含所有子目录里的视频）
        output_path: 最终输出目录（每部影片 → <CARID> <title>/）
        min_size_mb: 最小文件大小 (MB)
        dry_run: 完全只读模式（步骤1 只打印计划不实际改文件 + 跳过步骤2/3）

    注意：CLI 不再有独立的 intermediate_path 步骤。视频临时落到 ``output_path``
    顶层（保留原名），step2/3 通过内部 ``moved_paths`` 列表精准处理这一批视频。
    """
    if not download_path.exists():
        logger.error(f"下载目录不存在: {download_path}")
        return

    output_path.mkdir(parents=True, exist_ok=True)

    logger.info("\n" + "=" * 60)
    logger.info("JAVBus 工作流开始" + ("（DRY-RUN）" if dry_run else ""))
    logger.info(f"下载目录:   {download_path}")
    logger.info(f"最终目录:   {output_path}")
    logger.info("=" * 60)

    # 步骤1：移动视频（dry_run=True 时只打印计划；返回移走的文件列表）
    moved_paths = step1_move_videos(
        download_path, output_path, min_size_mb, dry_run=dry_run,
    )
    if not moved_paths:
        logger.error("步骤1失败（无视频可处理），终止工作流")
        return

    if dry_run:
        logger.info("DRY-RUN 模式，跳过步骤2/3")
        return

    # 步骤2：只对刚移来的视频去 @ 前缀（精准处理，不扫整个 output 目录）
    step2_clean_at_prefix_for_paths(moved_paths)

    # 步骤3：抓元数据，把视频按 <CARID> <title>/ 整理
    await step3_scrape_from_paths(output_path, moved_paths)

    logger.info("\n" + "=" * 60)
    logger.info("工作流完成")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "JAVBus 工作流：从下载目录（含子目录）移动视频 → 去 @ 前缀 → 削刮 → "
            "按 release_date 月份桶写入最终目录 <output_path>/<YYYY-MM>/<CARID> <title>/"
        ),
    )
    parser.add_argument(
        "download_path", type=Path, help="下载目录（含子目录里的视频）",
    )
    parser.add_argument(
        "output_path", type=Path,
        help=(
            "最终输出目录（按月份桶：<YYYY-MM>/<CARID> <title>/；"
            "视频先临时落到 _staging/ 子目录，削刮完自动清空）"
        ),
    )
    parser.add_argument(
        "--min-size", type=int, default=DEFAULT_MIN_SIZE_MB,
        help=f"最小文件大小 (MB)，默认 {DEFAULT_MIN_SIZE_MB}",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="完全只读模式：步骤1 只打印计划不实际改文件，跳过步骤2/3",
    )
    args = parser.parse_args()

    asyncio.run(workflow(
        args.download_path,
        args.output_path,
        args.min_size,
        args.dry_run,
    ))


if __name__ == "__main__":
    main()