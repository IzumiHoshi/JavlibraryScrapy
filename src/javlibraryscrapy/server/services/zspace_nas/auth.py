"""vendored from zspace_skill/nas/auth.py — 仅 ``resolve_device_id`` 加了 Windows 兜底。

NAS 登录加密层（RSA-PKCS1v15 + base64）。

Patched from upstream
---------------------
``resolve_device_id`` 在 Linux 下回退到 ``os.uname().nodename``，
但 Windows 没有 ``/etc/machine-id`` 也没有 ``os.uname``，会抛 ``AttributeError``。
本机服务跑在 Windows，所以加了 ``socket.gethostname()`` 兜底。其他代码原样保留。
"""
import base64
import hashlib
import os
import socket
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

# 注：这是 NAS 公开端点 /zspace/system/private/pubkey 返回的 RSA 公钥，
# 用于登录加密，非私钥/密码。更换 NAS 固件版本时可能需要更新。
#
# vendored patch (2026-08-23)：从 NAS 实际 /zspace/system/private/pubkey 拉取替换，
# 原值报 N001200 "账号格式不对"——本机 NAS 实际公钥与上游 vendored 版本不一致。
# 故障排查表对应：N001200 = "RSA 用错公钥"。
NAS_PUBKEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwRqvmlOHSc60J/p727sX
QJ+E+NCuaQJwWR7sMJ0jecYy9NU5ayuSgi3D1Ux001xPdW10mMl0Xw1VNAl3I9P/
bYgrsqesU/thmkkl93RwKKb49HgWYjFYF0gWiQE/6I6+Utsdo9fSUfcS/vSZijl6
Q/pvtWTERhpWi8GnaIVVujvpXJHrzblWb25IC2gWjkPrfBofkobYwbl65Ua18o6Y
9js6uL8Ji7CJrXcYul9PXDDHkhwTozT1pY9BjTEOpV9uLnVzQYCP4mRd2OT6Ydzb
/J4z/oKqeTuf7YQTCpImGK8RPuwf7PV9IHra4N1nXWvOiiz5NgvR7ebAPw06KTrV
KwIDAQAB
-----END PUBLIC KEY-----"""

_PUBKEY = load_pem_public_key(NAS_PUBKEY_PEM)

NAS_DEVICE_ID_DEFAULT = "<your_device_id_32_hex>"


def encrypt_field(plain: str) -> str:
    """RSA-PKCS1v15 + base64. NAS /auth/login 要求."""
    cipher = _PUBKEY.encrypt(plain.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(cipher).decode("ascii")


def resolve_device_id() -> str:
    """获取 32 字符 device_id。

    优先级：
    1.  env `NAS_DEVICE_ID`（32 字符） — 用户明确指定
    2.  `NAS_DEVICE_ID` 非 32 字符 → 从机器指纹自动生成持久化值，
        存到 `~/.cache/zspace-mcp/device_id` 下次复用
    """
    did = os.environ.get("NAS_DEVICE_ID", "").strip()
    if len(did) == 32:
        return did

    # 自动生成：从 /etc/machine-id + NAS_HOST hash，32 字符 hex
    cache_dir = Path.home() / ".cache" / "zspace-mcp"
    cache_file = cache_dir / "device_id"
    if cache_file.exists():
        cached = cache_file.read_text().strip()
        if len(cached) == 32:
            return cached

    host = os.environ.get("NAS_HOST", "unknown")
    try:
        machine = Path("/etc/machine-id").read_text().strip()
    except Exception:
        try:
            machine = Path("/var/lib/dbus/machine-id").read_text().strip()
        except Exception:
            try:
                machine = os.uname().nodename  # Linux
            except AttributeError:
                # Windows 兜底：socket.gethostname() 返回计算机名（如 DESKTOP-ABC123）
                machine = socket.gethostname()
    seed = f"{machine}:{host}"
    did = hashlib.sha256(seed.encode()).hexdigest()[:32]

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(did)
    return did