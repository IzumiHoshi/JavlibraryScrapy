"""从 MOSTWANTED_LIBRARY_ROOT 的 folder 反推 wanted 数据，写回 javlibrary_movies.json。

背景：
- 2026-08-19 commit 45e0d9a 误删了 javlibrary_movies.json
- 重启 refresh 后 JSON 只剩 40 部（远端新抓的）
- NFS 上的 73 个 <CODE> <title>/ folder 没被 JSON 记录
- 这些 folder 是过去抓过的产物，code 仍在 JAVLibrary Most Wanted 列表中

行为：
- 扫 mw_root 下所有 <CODE> <title>/ folder
- 已存在于 JSON 的 code 跳过（保留最新数据）
- 不存在的 code 写入 JSON，_status=ready / _bucket=unknown / missing_in_remote=True
  - release_date 留空（folder mtime 不可信——NFS 拷贝丢失原 mtime）
  - 下次 refresh_wanted 触发时 merge_wanted 会看到 release_date 为空 → 加进 needs_javbus
    → 重新抓 JavBus 补回真实 release_date

用法：
    uv run python scripts/restore_wanted_from_folders.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# 让脚本能 import 项目模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("restore_wanted")


def _load_dotenv() -> None:
    """简易 .env 读取（不依赖项目 Settings，方便单文件运行）。"""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        # 不覆盖已有环境变量
        if k and k not in os.environ:
            os.environ[k] = v


def _find_movie_folders(mw_root: Path) -> list[str]:
    """列出所有 ``<CODE> <title>`` 形式的 folder 名。"""
    out = []
    for entry in mw_root.iterdir():
        if not entry.is_dir():
            continue
        if " " not in entry.name:
            continue
        # 第一段像车牌
        first = entry.name.split(" ", 1)[0]
        if first and all(c.isalnum() or c in "-_" for c in first):
            out.append(entry.name)
    return out


def restore(
    mw_root: Path,
    json_path: Path,
    *,
    dry_run: bool = False,
) -> int:
    """返回新增的 entry 数。"""
    if not mw_root.exists() or not mw_root.is_dir():
        logger.error(f"MW_ROOT 不存在：{mw_root}")
        return 0
    if not json_path.exists():
        logger.error(f"JSON 不存在：{json_path}")
        return 0

    data = json.loads(json_path.read_text(encoding="utf-8"))
    existing_codes = {m["code"].upper() for m in data if m.get("code")}

    now = datetime.now().isoformat(timespec="seconds")
    added = 0
    for folder_name in sorted(_find_movie_folders(mw_root)):
        code = folder_name.split(" ", 1)[0].upper()
        title = folder_name.split(" ", 1)[1]
        if code in existing_codes:
            continue
        # release_date 留空：NFS mtime 不可信，下次 refresh 自动重抓 JavBus 补
        new_entry = {
            "id": f"restored-{code}",
            "code": code,
            "title": title,
            "cover_url": "",
            "release_date": "",
            "_status": "ready",
            "_bucket": "unknown",
            "_seen_at": now,
            "_updated_at": now,
            "missing_in_remote": True,
            "_restored_from_folder": True,
        }
        data.append(new_entry)
        existing_codes.add(code)
        added += 1
        logger.info(f"  + {code} ({title[:40]})")

    logger.info(f"原有 {len(data) - added} 部，新增 {added} 部")
    if added == 0:
        logger.info("无新增，无需写盘")
        return 0

    if dry_run:
        logger.info("[DRY-RUN] 不写盘")
        return added

    # 原子写
    tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(json_path)
    logger.info(f"✅ 已写入 {json_path}（共 {len(data)} 部）")
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="只列出会补的 entry，不写盘")
    parser.add_argument("--mw-root", type=Path, default=None, help="覆盖 .env 的 MOSTWANTED_LIBRARY_ROOT")
    parser.add_argument("--json", type=Path, default=None, help="覆盖 .env 的 JSON 路径（默认 <mw_root>/javlibrary_movies.json）")
    args = parser.parse_args(argv)

    _load_dotenv()
    mw_root = args.mw_root or os.environ.get("MOSTWANTED_LIBRARY_ROOT")
    if not mw_root:
        logger.error("未设置 MOSTWANTED_LIBRARY_ROOT（请传 --mw-root 或在 .env 配置）")
        return 2
    mw_root = Path(mw_root)
    json_path = args.json or (mw_root / "javlibrary_movies.json")

    restore(mw_root, json_path, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())