#!/usr/bin/env python
"""画廊服务入口（向后兼容 shim）。

实际实现已迁移到 ``scripts/gallery/`` 包（FastAPI 重构版本）。
保留本文件仅为兼容 ``uv run python scripts/gallery_server.py`` 这条旧命令；
新代码与文档见 ``scripts/gallery/main.py``。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让 ``import gallery.main`` 在直接运行 ``scripts/gallery_server.py`` 时也能找到包。
# （正常通过 ``python -m scripts.gallery.main`` 启动时，``scripts/__init__.py`` 不存在，
#  ``gallery`` 包靠 ``scripts/gallery/__init__.py`` 里手动加 sys.path 的代码来 import 兄弟模块。）
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from gallery.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())