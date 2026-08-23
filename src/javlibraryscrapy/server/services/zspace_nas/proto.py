"""vendored from zspace_skill/nas/proto.py — 原样保留。

NAS HTTP 协议层：base URL + 公共 query 参数。
注意：``NAS_BASE`` 在 import 时基于 ``NAS_HOST`` 计算；调用方必须先设 env 再 import。
"""
import os

_NAS_HOST = os.environ.get("NAS_HOST", "")
NAS_BASE = os.environ.get("NAS_BASE", f"http://{_NAS_HOST}:5055" if _NAS_HOST else "")

# vendored patch (2026-08-23)：vendor 硬编码的 version / device / _l 是旧固件的值，
# NAS 升级后会从登录响应里取真实值（data.version / web UI device=PC / _l=zh_cn）。
# 用 env 变量覆盖，未设时回退到 vendor 原值以保持兼容。
_NAS_VERSION = os.environ.get("NAS_VERSION", "2.3.2026062201")
_NAS_DEVICE = os.environ.get("NAS_DEVICE", "linux")
_NAS_LANG = os.environ.get("NAS_LANG", "zh-CN")


def common_query(device_id: str) -> str:
    """axios 拦截器给所有请求追加的公共参数。

    注：token + nasid 由 ``NasClient`` 拼到 body 或 query（不同端点方式不同）；
    公共参数这一段不含 token/nasid。
    """
    return (
        f"?plat=web&version={_NAS_VERSION}"
        f"&device_id={device_id}&device={_NAS_DEVICE}&_l={_NAS_LANG}"
    )


def append_common_query(url: str, device_id: str) -> str:
    """给 NAS API URL 拼上公共参数（原 app.py:_append_common_query）。"""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{common_query(device_id).lstrip('?')}"