"""给 wanted JSON 里 ``_status=ready`` 但 ``magnet`` 为空的车补磁力。

背景：
    2026-08-23 那次 batch refresh 把 119 部车标 ``ready``，但其中 118 部的
    ``magnet`` 字段为空（只有 ABF-376 抓到了）。原因是当时 JAVBus 加了 age
    verify 拦截，那次 refresh 用的是直接 HTTP，没渲染 JS，绕过不了。

    现在项目里的 ``MovieExporter``（基于 Scrapling ``AsyncDynamicSession``）
    是 headless Chrome，能自动渲染 JS + 提交年龄验证表单 + 触发 showmag click。
    实测能拿到 magnet。

用法：
    python scripts/backfill_magnets.py                        # 默认 dry-run：只列出
    python scripts/backfill_magnets.py --yes                  # 真正写入 JSON
    python scripts/backfill_magnets.py --data <path>          # 指定非默认 JSON
    python scripts/backfill_magnets.py --batch-size 5         # 改批量大小（默认 10）
    python scripts/backfill_magnets.py --only ABF-376 MIAB-001  # 只补指定车牌
    python scripts/backfill_magnets.py --yes --reload          # 跑完通知 gallery 重读

退出码：成功 0；找不到文件 1。

行为：
    - 只补 ``_status=ready`` 且 ``magnet`` 为空的条目（不动其它状态的）
    - 抓到的 magnet 写回原条目的 ``magnet`` 字段
    - 每批跑完增量落盘（崩了最多丢一个 batch 的进度）
    - ``--reload`` 时落盘后 POST /api/wanted/reload 让 gallery 立即看到新数据
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from javlibraryscrapy._paths import REPO_ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("backfill_magnets")


def resolve_data_path(arg_path: Optional[str]) -> Path:
    """解析 wanted JSON 路径，优先级：CLI 参数 > .env 的 MOSTWANTED_INDEX > 项目 output/。"""
    if arg_path:
        return Path(arg_path).resolve()
    load_dotenv(REPO_ROOT / ".env", override=False)
    env_path = os.getenv("MOSTWANTED_INDEX", "").strip()
    if env_path:
        return Path(env_path).resolve()
    mw_root = os.getenv("MOSTWANTED_LIBRARY_ROOT", "").strip()
    if mw_root:
        return Path(mw_root) / "javlibrary_movies.json"
    return REPO_ROOT / "output" / "javlibrary_movies.json"


def load_movies(data_path: Path) -> List[Dict[str, Any]]:
    """读 wanted JSON；顶层必须是 list。"""
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"JSON 顶层不是 list：{data_path}")
    return data


def save_movies_atomic(data_path: Path, movies: List[Dict[str, Any]]) -> None:
    """原子写：tmp → rename。"""
    tmp = data_path.with_suffix(data_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(movies, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(data_path)


def select_targets(
    movies: List[Dict[str, Any]],
    only_codes: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """挑出 ``_status=ready`` 且 ``magnet`` 为空的条目。

    - 若给了 ``only_codes``，只挑这些 code（且仍要求 ready + 无 magnet）
    - 按 code 升序，便于排错时定位
    """
    only_set = {c.strip().upper() for c in (only_codes or [])}
    out = []
    for m in movies:
        code = (m.get("code") or "").strip().upper()
        if not code:
            continue
        if only_set and code not in only_set:
            continue
        if (m.get("_status") or "").strip() != "ready":
            continue
        if (m.get("magnet") or "").strip():
            continue
        out.append(m)
    out.sort(key=lambda m: m.get("code", ""))
    return out


async def backfill(
    data_path: Path,
    targets: List[Dict[str, Any]],
    *,
    yes: bool,
    batch_size: int,
    scratch_root: Path,
) -> Dict[str, int]:
    """批量抓 magnet 并写回。增量落盘。

    Returns 统计 dict：{ok, no_magnet, failed, skipped_existing}
    """
    # 延迟导入避免无网络环境也能跑 --help / --dry-run
    from javlibraryscrapy.scraping.exporter import MovieExporter

    # 输出根：临时目录。MovieExporter 会把 fanart.jpg / poster.jpg 落到这里；
    # 跑完会被 cleanup_temp_pngs 清掉 .png。.jpg 文件（fanart/poster/sample）会留着
    # —— 跑完统一清。
    scratch_root.mkdir(parents=True, exist_ok=True)

    # MovieExporter 配置：collect_magnets=True（写到 scratch 下的 magnets.json，
    # 同时填充 _magnet_results —— 我们从那里拿 magnet 写回主 JSON）
    exporter = MovieExporter(
        output_root=scratch_root,
        move_video=False,
        download_samples=False,
        collect_magnets=True,
        magnets_index=scratch_root / "magnets.json",  # 写到 scratch，不污染 library_root
    )

    stats = {"ok": 0, "no_magnet": 0, "failed": 0, "total": len(targets)}
    movies = load_movies(data_path)  # 重新读最新内容（避免和 in-flight 写入冲突）
    # 构造 code → entry 索引
    by_code = {(m.get("code") or "").strip().upper(): m for m in movies}

    if not targets:
        logger.info("没有需要补 magnet 的车 ✅")
        return stats

    logger.info(f"将处理 {len(targets)} 辆车（batch_size={batch_size}）")
    if not yes:
        # Dry-run 模式：只展示计划，不开 headless browser
        for i, m in enumerate(targets, 1):
            code = (m.get("code") or "").strip().upper()
            logger.info(f"  [{i}/{len(targets)}] {code}（{m.get('title','')[:40]}）")
        logger.info("\nDRY RUN：未实际抓取。加 --yes 真正执行。")
        return stats

    # 实际跑：分批
    for batch_start in range(0, len(targets), batch_size):
        batch = targets[batch_start : batch_start + batch_size]
        car_list = [(m["code"].strip().upper(), "") for m in batch]
        cover_urls = {
            m["code"].strip().upper(): (m.get("cover_url") or "").strip()
            for m in batch
            if m.get("cover_url")
        }
        logger.info(
            f"\n[{batch_start // batch_size + 1}/"
            f"{(len(targets) + batch_size - 1) // batch_size}] "
            f"抓 batch: {', '.join(c for c, _ in car_list)}"
        )

        try:
            await exporter.export_movies(car_list, cover_urls=cover_urls)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Batch 异常：{e}")
            stats["failed"] += len(batch)
            continue

        # 把 magnet 写回 in-memory movies，再落盘
        dirty = False
        for result in exporter._magnet_results:
            code = (result.get("code") or "").strip().upper()
            magnet = (result.get("magnet") or "").strip()
            status = result.get("status")
            entry = by_code.get(code)
            if entry is None:
                continue
            if status == "ok" and magnet:
                entry["magnet"] = magnet
                entry["_updated_at"] = __import__("datetime").datetime.now().isoformat(
                    timespec="seconds"
                )
                dirty = True
                stats["ok"] += 1
                logger.info(f"  ✅ {code}: 写入 magnet")
            elif status == "no_magnet":
                stats["no_magnet"] += 1
                logger.info(f"  ⚠ {code}: 页面无 magnet")
            else:
                stats["failed"] += 1
                logger.info(f"  ✗ {code}: {status}")

        if dirty:
            try:
                save_movies_atomic(data_path, movies)
                logger.info(f"  💾 已落盘 {data_path}")
            except OSError as e:  # noqa: BLE001
                logger.error(f"  落盘失败：{e}")

        # 重置 exporter 内部计数器
        exporter._magnet_results = []
        exporter._attempted_codes = set()
        exporter._written_codes = set()
        exporter._failed_codes = set()

    return stats


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="给 wanted JSON 里 ready 但 magnet 为空的车补磁力",
    )
    p.add_argument(
        "--data",
        type=str,
        default=None,
        help="wanted JSON 路径（默认从 .env 的 MOSTWANTED_INDEX 读取）",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="真正执行抓取 + 写盘（默认 dry-run）",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="每批处理的车数（默认 10；每批间隔由 MovieExporter 内置 sleep 控制）",
    )
    p.add_argument(
        "--only",
        nargs="+",
        default=None,
        help="只补指定车牌（空格分隔，如 --only ABF-376 MIAB-001）",
    )
    p.add_argument(
        "--reload",
        action="store_true",
        help="落盘后 POST /api/wanted/reload 让 gallery 立即重读（避免重启服务）",
    )
    p.add_argument(
        "--gallery-url",
        default="http://127.0.0.1:8000",
        help="gallery 服务根 URL（默认 http://127.0.0.1:8000）",
    )
    args = p.parse_args(argv)

    data_path = resolve_data_path(args.data)
    if not data_path.exists():
        logger.error(f"找不到 JSON：{data_path}")
        return 1

    try:
        movies = load_movies(data_path)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"读 JSON 失败：{e}")
        return 1

    targets = select_targets(movies, only_codes=args.only)
    logger.info(f"目标 JSON：{data_path}（共 {len(movies)} 辆车）")
    logger.info(f"待补 magnet：{len(targets)} 辆")

    # 临时 scratch 目录，跑完自动清理
    scratch_root = Path(tempfile.gettempdir()) / "javlibraryscrapy_backfill"
    try:
        stats = asyncio.run(
            backfill(
                data_path,
                targets,
                yes=args.yes,
                batch_size=args.batch_size,
                scratch_root=scratch_root,
            )
        )
    finally:
        # 清掉 scratch 下残留的临时 png（movie.nfo / poster.jpg 等会留着无伤大雅）
        try:
            for p in scratch_root.glob("*.png"):
                p.unlink()
        except OSError:
            pass

    if args.yes:
        logger.info(
            f"\n完成：ok={stats['ok']}，no_magnet={stats['no_magnet']}，"
            f"failed={stats['failed']}（total={stats['total']}）"
        )
        # 落盘后通知 gallery 服务重读
        if args.reload:
            reload_url = args.gallery_url.rstrip("/") + "/api/wanted/reload"
            logger.info(f"\n通知 gallery reload: POST {reload_url}")
            try:
                # 延迟导入：脚本默认不依赖 requests
                import urllib.request
                req = urllib.request.Request(
                    reload_url,
                    data=b"",  # 空 body
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                logger.info(f"  ✅ gallery reload 响应：{body[:200]}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"  ⚠ gallery reload 失败（不影响 JSON 已落盘）：{e}")
                logger.warning(f"    手动重启服务或调：curl -X POST {reload_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
