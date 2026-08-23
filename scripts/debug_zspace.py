"""调试 zspace 集成：直接打 NAS 协议层看原始响应。

不走 UI，直接调 :class:`ZSpaceClient`，把登录 / 列表 / 提交三步的
原始返回都打出来。错误类型 / NAS 业务码 / HTTP 状态码全显示，
方便定位是网络层、登录层还是下载层的问题。

用法::

    uv run python scripts/debug_zspace.py

会先读 ``output/zspace_config.json`` 里的 host / user / password / device_id，
然后：
1. list_downloads() —— 验证登录态 + token 续期
2. submit_magnet() —— 验证下载目录权限（用假 magnet，避免污染真实下载）
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from javlibraryscrapy.server.config import Settings  # noqa: E402
from javlibraryscrapy.server.services.zspace import ZSpaceClient, ZSpaceError  # noqa: E402
from javlibraryscrapy.server.services.zspace_config import ZSpaceConfigStore  # noqa: E402


def _mask(s: str | None, keep: int = 4) -> str:
    if not s:
        return "<empty>"
    if len(s) <= keep * 2:
        return "***"
    return s[:keep] + "…" + s[-keep:]


async def main() -> int:
    settings = Settings()
    store = ZSpaceConfigStore(output_dir=ROOT / "output", settings=settings)
    cfg = store.get()

    print("=" * 60)
    print("当前 zspace 配置（敏感字段遮蔽）")
    print("=" * 60)
    print(f"  enabled        = {cfg.enabled}")
    print(f"  host           = {cfg.host}")
    print(f"  user           = {cfg.user}")
    print(f"  password       = {_mask(cfg.password)}")
    print(f"  device_id      = {_mask(cfg.device_id)}")
    print(f"  download_path  = {cfg.download_path}")
    print(f"  is_configured  = {cfg.is_configured()}")
    print()

    if not cfg.is_configured():
        print("⚠ 配置未启用或不完整，请在页面「🛜 zspace」按钮里填。")
        return 1

    client = ZSpaceClient(store.get)
    rc = 0
    try:
        # Step 1: 验证登录 + 列表
        print("=" * 60)
        print("Step 1: list_downloads() —— 验证登录态")
        print("=" * 60)
        try:
            r = await client.list_downloads()
            print(f"  返回 = {json.dumps(r, ensure_ascii=False, indent=2)[:800]}")
            if isinstance(r, dict):
                code = str(r.get("code"))
                if code == "200":
                    print("  ✓ 登录 OK")
                else:
                    print(f"  ✗ NAS 业务码 = {code}（期望 200）")
                    rc = 1
        except ZSpaceError as e:
            print(f"  ✗ 登录失败：{e}")
            rc = 1
        except Exception as e:
            print(f"  ✗ 异常 {type(e).__name__}: {e}")
            rc = 1
        print()

        # Step 2: 验证下载路径 + 提交权限（假 magnet，不会下载）
        if rc == 0:
            print("=" * 60)
            print(f"Step 2: submit_magnet() —— 验证目录 {cfg.download_path}")
            print("=" * 60)
            fake_magnet = (
                "magnet:?xt=urn:btih:"
                "0123456789abcdef0123456789abcdef01234567"
                "&dn=debug_test_no_real_download"
            )
            try:
                r = await client.submit_magnet(fake_magnet, cfg.download_path)
                print(f"  返回 = {json.dumps(r, ensure_ascii=False, indent=2)[:800]}")
                if isinstance(r, dict):
                    code = str(r.get("code"))
                    if code == "200":
                        print("  ✓ 提交 OK（任务已加）")
                    else:
                        print(f"  ✗ NAS 业务码 = {code} msg = {r.get('msg')}")
                        rc = 1
            except ZSpaceError as e:
                print(f"  ✗ 提交失败：{e}")
                rc = 1
            except Exception as e:
                print(f"  ✗ 异常 {type(e).__name__}: {e}")
                rc = 1

    finally:
        await client.aclose()

    print()
    print("=" * 60)
    print("常见错误码速查")
    print("=" * 60)
    print("  N001208  token 失效（自动重登，正常）")
    print("  N001414  设备需短信验证 → 用浏览器登录后复制 device_id 填进配置")
    print("  N001403  下载 app 没权限访问该目录（团队空间最常见）")
    print("  N001xxx  其它业务码 → 看 msg 字段")
    print("  ConnectError  NAS 不可达 → 检查 IP/端口 5055/防火墙")
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
