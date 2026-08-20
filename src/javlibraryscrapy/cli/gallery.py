"""画廊服务入口：解析 CLI 参数 → 构造 FastAPI app → uvicorn 启动。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import List, Optional

import uvicorn

# 必须早于 argparse default 表达式：原 gallery_server.py 显式 load_dotenv(.env)
# 后再解析命令行，这样 --library-root 的默认值才能拿到 .env 里的 LIBRARY_ROOT。
from dotenv import load_dotenv  # noqa: E402

from javlibraryscrapy.server.app import create_app, local_ip_address  # noqa: E402
from javlibraryscrapy.server.config import ROOT, load_settings  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

logger = logging.getLogger("gallery.main")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="影片画廊本地服务器（FastAPI 重构版）",
    )
    # --data 默认路径：若 .env 的 MOSTWANTED_LIBRARY_ROOT 设了，
    # 把 javlibrary_movies.json 也放到那里（与每部影片的 cover/samples 同根目录）；
    # 否则退回 output/javlibrary_movies.json（保持旧行为）。
    _mw_root = os.getenv("MOSTWANTED_LIBRARY_ROOT", "").strip()
    _default_data = (
        str(Path(_mw_root) / "javlibrary_movies.json")
        if _mw_root
        else str(ROOT / "output" / "javlibrary_movies.json")
    )
    p.add_argument(
        "--data",
        default=_default_data,
        help=(
            "影片数据文件（JSON 或 CSV，默认 "
            f"{_default_data}；MOSTWANTED_LIBRARY_ROOT 设了则改为那里）"
        ),
    )
    p.add_argument(
        "--output-dir",
        default=str(ROOT / "output"),
        help="结果输出目录（默认 output/）",
    )
    p.add_argument(
        "--library-root",
        default=os.getenv("LIBRARY_ROOT", "").strip() or None,
        help="本地影片库根目录（默认从 .env 的 LIBRARY_ROOT 读取，未配置则禁用本地库功能）",
    )
    p.add_argument(
        "--library-index",
        default=str(ROOT / "output" / "library_index.json"),
        help="本地库索引路径（默认 output/library_index.json）",
    )
    p.add_argument(
        "--no-rescan-on-startup",
        action="store_true",
        help="启动时不自动扫描本地库（仅当索引缺失或 root 不一致时才会扫描）",
    )
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="监听地址（默认 0.0.0.0，允许局域网访问）",
    )
    p.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    p.add_argument(
        "--image-proxy",
        choices=["auto", "on", "off"],
        default="auto",
        help="封面是否经服务端代理拉取（auto：配置了代理时启用）",
    )
    p.add_argument(
        "--open-browser",
        action="store_true",
        help="启动后自动打开浏览器（默认不打开）",
    )
    return p.parse_args(argv)


def _validate_library_root(p: Optional[str]) -> Optional[Path]:
    if not p:
        return None
    root = Path(p).resolve()
    if not root.exists():
        logger.error(f"LIBRARY_ROOT 不存在：{root}")
        sys.exit(1)
    if not root.is_dir():
        logger.error(f"LIBRARY_ROOT 不是目录：{root}")
        sys.exit(1)
    return root


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    args = parse_args(argv)

    library_root = _validate_library_root(args.library_root)
    settings = load_settings()
    # CLI 参数覆盖 .env 中的 LIBRARY_ROOT
    if library_root is not None:
        settings.library_root = library_root
    if args.library_index:
        settings.library_index = Path(args.library_index).resolve()

    data_path = Path(args.data).resolve()
    output_dir = Path(args.output_dir).resolve()

    app = create_app(
        settings=settings,
        data_path=data_path,
        output_dir=output_dir,
        image_proxy_mode=args.image_proxy,
        no_rescan_on_startup=args.no_rescan_on_startup,
    )

    # 启动 banner（保留原服务的输出风格）
    state = app.state.gallery
    local_url = f"http://127.0.0.1:{args.port}"
    display_url = (
        f"http://{local_ip_address()}:{args.port}"
        if args.host == "0.0.0.0"
        else f"http://{args.host}:{args.port}"
    )
    logger.info(f"影片画廊已启动：{local_url}")
    if args.host == "0.0.0.0":
        logger.info(f"局域网访问地址：{display_url}")
    logger.info(
        f"共 {len(state.movies)} 部影片，磁力抓取代理：{'开启' if state.proxy else '关闭'}，"
        f"封面代理：{'开启' if state.image_proxy else '关闭'}"
    )
    if library_root:
        if len(state.library_index) > 0:
            logger.info(
                f"本地库已就绪：{len(state.library_index)} 部（{library_root}），"
                f"上次扫描 {state.library_scanned_at}"
            )
        else:
            logger.info(f"本地库已配置但索引为空：{library_root}，可在页面上点击「刷新库」")
    logger.info("按 Ctrl+C 停止服务")

    if args.open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(local_url)).start()

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,  # 原 stdlib 服务也没开 access log；保持安静
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())