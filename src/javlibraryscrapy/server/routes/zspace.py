"""极空间 NAS 路由：状态查询 + 批量提交磁力 + 列出下载任务。

端点：
    GET  /api/zspace/status     —— 是否启用 + host + 默认下载路径
    POST /api/zspace/submit     —— 批量提交磁力到 NAS 下载器
    POST /api/zspace/downloads  —— 列出 NAS 当前下载任务（前端监控用）

设计要点
--------
- ``status`` 不触发登录（只看 settings），可热用于前端判断按钮是否可点。
- ``submit`` / ``downloads`` 走 :class:`ZSpaceClient` 单例（懒加载）；
  首次请求触发 RSA 登录，慢一点是预期。
- ``submit`` 串行提交每个 magnet：磁力提交是 NAS 后端写操作，并发可能触发
  它的反作弊限流。如果以后需要并发再加 ``asyncio.gather``。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..services.zspace import ZSpaceClient, ZSpaceError, is_configured

logger = logging.getLogger("gallery.zspace_routes")


# --------------------------------------------------------------------------- #
# 请求体模型
# --------------------------------------------------------------------------- #
class MagnetItem(BaseModel):
    """单个磁力提交项。``code`` 仅用于日志/前端展示，NAS 不关心。"""

    model_config = ConfigDict(extra="ignore")

    code: str = Field(..., min_length=1, max_length=64, description="车牌，日志/UI 用")
    magnet: str = Field(
        ...,
        min_length=10,
        description="完整 magnet 链接（magnet:?xt=urn:btih:...）",
    )


class SubmitBody(BaseModel):
    """POST /api/zspace/submit 请求体。"""

    model_config = ConfigDict(extra="ignore")

    items: List[MagnetItem] = Field(..., min_length=1, max_length=300)
    download_path: Optional[str] = Field(
        default=None,
        description=(
            "NAS 下载目录（/pool/my/data/.../）。"
            "为空时使用 settings.zspace_download_path。"
        ),
    )


# --------------------------------------------------------------------------- #
# 单例管理（放在 module 内而不是 app.state，避免 app.py 改动）
# --------------------------------------------------------------------------- #
def _get_or_create_client(request: Request) -> ZSpaceClient:
    """懒加载 ZSpaceClient 单例，存在 ``request.app.state.zspace``。"""
    client: Optional[ZSpaceClient] = getattr(request.app.state, "zspace", None)
    if client is not None:
        return client
    settings = request.app.state.settings
    client = ZSpaceClient(settings)
    request.app.state.zspace = client
    return client


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #
def register(app: FastAPI) -> None:
    @app.get("/api/zspace/status")
    async def status(request: Request) -> Dict[str, Any]:
        """返回 zspace 集成状态（前端按钮启用/禁用 + 默认路径回填）。"""
        settings = request.app.state.settings
        return {
            "configured": is_configured(settings),
            "enabled": bool(settings.zspace_enabled),
            "host": settings.zspace_host or None,
            "user": settings.zspace_user or None,
            "device_id_set": bool(settings.zspace_device_id),
            "default_download_path": settings.zspace_download_path,
        }

    @app.post("/api/zspace/submit")
    async def submit(body: SubmitBody, request: Request) -> Dict[str, Any]:
        """批量提交磁力到 NAS 下载器，逐项返回结果。

        任何一项失败不影响其它项（前端可单独 retry）。
        单项结构：
        ``{"code", "magnet", "ok", "status_code", "msg", "data", "error"}``
        - ``ok``: NAS 业务码 == "200"
        - ``status_code``: NAS API 返回的 code 字段（字符串，如 "200"/"N0xxxx"）
        - ``error``: 仅登录/网络失败时有
        """
        settings = request.app.state.settings
        if not is_configured(settings):
            raise HTTPException(
                status_code=503,
                detail=(
                    "zspace 未启用或未配置完整"
                    "（.env 需要 ZSPACE_ENABLED=true + ZSPACE_HOST/USER/PASSWORD）"
                ),
            )

        download_path = (body.download_path or settings.zspace_download_path or "").strip()
        if not download_path:
            raise HTTPException(
                status_code=400,
                detail="download_path 为空，且 settings.zspace_download_path 也未设置",
            )

        client = _get_or_create_client(request)
        results: List[Dict[str, Any]] = []
        ok_count = 0
        for it in body.items:
            try:
                resp = await client.submit_magnet(it.magnet, download_path)
            except ZSpaceError as e:
                # 登录/网络层失败 —— 整批基本都跑不了，后续项直接复用同一错误
                logger.warning(f"提交磁力失败 {it.code}: {e}")
                results.append({
                    "code": it.code,
                    "magnet": it.magnet,
                    "ok": False,
                    "error": str(e),
                })
                continue
            except Exception as e:  # noqa: BLE001
                logger.warning(f"提交磁力未知异常 {it.code}: {e}")
                results.append({
                    "code": it.code,
                    "magnet": it.magnet,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                })
                continue

            code = str(resp.get("code")) if isinstance(resp, dict) else ""
            ok = code == "200"
            if ok:
                ok_count += 1
            entry: Dict[str, Any] = {
                "code": it.code,
                "magnet": it.magnet,
                "ok": ok,
                "status_code": code or None,
                "msg": resp.get("msg") if isinstance(resp, dict) else None,
            }
            if isinstance(resp, dict) and "data" in resp:
                entry["data"] = resp["data"]
            if not isinstance(resp, dict):
                # 极少见：nas.post 抛错前不会到这里；保留 raw 兜底
                entry["raw"] = str(resp)[:300]
            results.append(entry)

        return {
            "download_path": download_path,
            "total": len(body.items),
            "ok_count": ok_count,
            "results": results,
        }

    @app.post("/api/zspace/downloads")
    async def list_downloads(request: Request) -> Dict[str, Any]:
        """列出当前 NAS 下载任务（POST ``/downloader/list`` body ``{}``）。"""
        settings = request.app.state.settings
        if not is_configured(settings):
            raise HTTPException(status_code=503, detail="zspace 未配置")
        client = _get_or_create_client(request)
        try:
            return await client.list_downloads()
        except ZSpaceError as e:
            raise HTTPException(status_code=502, detail=str(e))