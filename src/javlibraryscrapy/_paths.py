"""包内共享路径常量。

集中项目根路径计算，避免各处散落 ``Path(__file__).resolve().parents[N]`` 这种
脆弱的写法。包被搬到子目录或被 vendored 进去时，只需改这一处。

- :data:`REPO_ROOT` —— 仓库根（``src/`` 的父目录，``output/``、``.env`` 在这里）
- :data:`PACKAGE_ROOT` —— Python 包根（``src/javlibraryscrapy/``，``templates/`` 在这里）

注意：装成 wheel 后这些路径仍指向源码 checkout；这跟原代码行为一致
（脚本仍假设从源码 checkout 运行，不假设安装后的 site-packages 布局）。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
PACKAGE_ROOT: Path = Path(__file__).resolve().parents[0]

__all__ = ["REPO_ROOT", "PACKAGE_ROOT"]