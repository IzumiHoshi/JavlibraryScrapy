"""极空间 NAS 协议层 vendored 包。

源代码：https://github.com/coracoo/zspace_skill/tree/main/nas
vendored at: 2026-08-22, commit <latest main>

为什么 vendor 而非依赖：
- zspace_skill 是个 Claude skill（mcp_server），不是 PyPI 包，不能 `pip install`。
- 这层只依赖 httpx + cryptography，加上 RSA 公钥 + 协议常量，独立 vendoring 干净。

未做修改：保持 zspace_skill/nas/ 原样。如上游更新需手动同步。
"""
from .auth import (
    NAS_PUBKEY_PEM,
    NAS_DEVICE_ID_DEFAULT,
    encrypt_field,
    resolve_device_id,
)
from .proto import NAS_BASE, common_query, append_common_query
from .client import NasClient

__all__ = [
    "NAS_PUBKEY_PEM",
    "NAS_DEVICE_ID_DEFAULT",
    "encrypt_field",
    "resolve_device_id",
    "NAS_BASE",
    "common_query",
    "append_common_query",
    "NasClient",
]