"""极空间 NAS 路由：配置 + 状态 + 批量提交磁力 + 列出下载任务。

端点：
    GET    /api/zspace/config    —— 读取当前配置（密码遮蔽）
    POST   /api/zspace/config    —— 更新配置（空 password 保留原值）
    GET    /api/zspace/status    —— 是否启用 + host + 默认下载路径（前端按钮启用态）
    POST   /api/zspace/submit    —— 批量提交磁力到 NAS 下载器
    POST   /api/zspace/downloads —— 列出 NAS 当前下载任务（前端监控用）

设计要点
--------
- 配置存 ``output/zspace_config.json``（同 magnets.json），用户通过网页 UI 编辑。
  ``.env`` 的 ``ZSPACE_*`` 仍是初始种子（首次启动 / JSON 缺失时兜底）。
- ``status`` 不触发登录（只看 JSON），可热用于前端判断按钮是否可点。
- ``submit`` / ``downloads`` 走 :class:`ZSpaceClient` 单例（懒加载）；
  首次请求触发 RSA 登录，慢一点是预期。配置变更时自动重建内部 client。
- ``submit`` 串行提交每个 magnet：磁力提交是 NAS 后端写操作，并发可能触发
  它的反作弊限流。如果以后需要并发再加 ``asyncio.gather``。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..services.zspace import ZSpaceClient, ZSpaceError
from ..services.zspace_config import ZSpaceConfigStore

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
            "为空时使用当前配置的 download_path。"
        ),
    )


class ConfigBody(BaseModel):
    """POST /api/zspace/config 请求体。

    所有字段可选；只传改动的字段。
    - ``password`` 为空字符串 → 视为"不修改"（避免误清空）
    - 其它字符串字段为空 → 写 None
    """

    model_config = ConfigDict(extra="ignore")

    enabled: Optional[bool] = Field(default=None, description="启用 zspace 集成")
    host: Optional[str] = Field(default=None, max_length=128, description="极空间 IP")
    user: Optional[str] = Field(default=None, max_length=64, description="登录用户名")
    password: Optional[str] = Field(
        default=None,
        max_length=128,
        description="登录密码。空字符串 = 不修改；非空 = 替换",
    )
    device_id: Optional[str] = Field(
        default=None, max_length=64, description="device_id（32 字符 hex），空 = 自动生成"
    )
    download_path: Optional[str] = Field(
        default=None, max_length=512, description="NAS 下载目录"
    )


# --------------------------------------------------------------------------- #
# 单例管理
# --------------------------------------------------------------------------- #
def _get_store(request: Request) -> ZSpaceConfigStore:
    store: Optional[ZSpaceConfigStore] = getattr(request.app.state, "zspace_config_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="zspace 配置存储未初始化")
    return store


def _get_or_create_client(request: Request) -> ZSpaceClient:
    """懒加载 ZSpaceClient 单例，存在 ``request.app.state.zspace``。"""
    client: Optional[ZSpaceClient] = getattr(request.app.state, "zspace", None)
    if client is not None:
        return client
    store = _get_store(request)
    client = ZSpaceClient(store.get)
    request.app.state.zspace = client
    return client


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #
def register(app: FastAPI) -> None:
    @app.get("/api/zspace/config")
    async def get_config(request: Request) -> Dict[str, Any]:
        """读取当前配置（密码以 ``"********"`` 返回，前端永远看不到明文）。"""
        store = _get_store(request)
        cfg = store.get()
        return cfg.to_dict(mask_password=True)

    @app.post("/api/zspace/config")
    async def update_config(body: ConfigBody, request: Request) -> Dict[str, Any]:
        """更新配置并落盘。返回更新后的配置（密码遮蔽）。

        空 password 字段视为"保持原值"（避免 UI 提交时把已存密码意外清掉）；
        落盘失败时返回 500 + 可读 detail（不再静默 log 后假装成功）。
        """
        store = _get_store(request)
        # Pydantic 把没传的字段填 None，这里 only-include-非None 让 patch dict 干净
        patch = {k: v for k, v in body.model_dump(exclude_none=False).items() if v is not None}
        # 但 password 的 None/空 处理逻辑在 store.update 里（empty = keep）
        try:
            cfg = store.update(patch)
        except OSError as e:
            # 落盘失败 —— 不能让前端以为配置保存成功了（Sourcery #1）
            logger.exception("zspace 配置落盘失败")
            raise HTTPException(
                status_code=500,
                detail=f"无法写入配置文件：{e}",
            )
        # 改完配置让 client 下次调用时重建（_ensure_client 会自动检测）
        return cfg.to_dict(mask_password=True)

    @app.get("/api/zspace/status")
    async def status(request: Request) -> Dict[str, Any]:
        """返回 zspace 集成状态（前端按钮启用/禁用 + 默认路径回填）。"""
        store = _get_store(request)
        cfg = store.get()
        return {
            "configured": cfg.is_configured(),
            "enabled": cfg.enabled,
            "host": cfg.host,
            "user": cfg.user,
            "device_id_set": bool(cfg.device_id),
            "default_download_path": cfg.download_path,
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
        store = _get_store(request)
        cfg = store.get()
        if not cfg.is_configured():
            raise HTTPException(
                status_code=503,
                detail=(
                    "zspace 未启用或未配置完整"
                    "（请点页面「🛜 zspace」按钮填写 host / user / password）"
                ),
            )

        download_path = (body.download_path or cfg.download_path or "").strip()
        if not download_path:
            raise HTTPException(
                status_code=400,
                detail="download_path 为空，且配置中的 download_path 也未设置",
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
        store = _get_store(request)
        cfg = store.get()
        if not cfg.is_configured():
            raise HTTPException(
                status_code=503,
                detail="zspace 未启用或未配置完整",
            )
        client = _get_or_create_client(request)
        try:
            return await client.list_downloads()
        except ZSpaceError as e:
            # ZSpaceClient 已把 httpx 网络错误 / 解析错误 / 登录错误统一包装成 ZSpaceError，
            # 这里再统一映射成 502 让前端拿到 NAS 失败详情。
            raise HTTPException(status_code=502, detail=str(e))