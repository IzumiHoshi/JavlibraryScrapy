"""FastAPI 实现的影片画廊本地服务器。

模块布局：
- ``config``：从 ``.env`` 读取的配置（沿用原画廊的 PROXY/JAVBUS_URL 等）
- ``models``：请求/响应的 Pydantic 模型（保留原服务的 JSON shape）
- ``services``：业务逻辑（任务、扫描、本地库、封面）
- ``routes``：HTTP 路由层（仅做请求解析 + 响应包装）
- ``main``：入口
"""

# 把 scripts/ 目录加入 sys.path，使得兄弟模块（library_scanner、
# library_refresher、javbus_scrapling）可以以顶层模块名 import。
# 原 gallery_server.py 在 scripts/ 下运行时，Python 会隐式把 scripts/
# 放到 sys.path[0]；新代码需要显式做这件事（因为我们现在是嵌套包）。
from __future__ import annotations

import sys
from pathlib import Path as _Path

_SCRIPTS_DIR = _Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from .app import create_app  # noqa: E402

__all__ = ["create_app"]