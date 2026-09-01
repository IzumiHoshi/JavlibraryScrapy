"""极空间 NAS 路由：状态 + 批量提交磁力 + 列出下载任务 + 下载代码集。

端点
----
    GET    /api/zspace/status    —— 是否启用 + host + 默认下载路径（前端按钮启用态）
    POST   /api/zspace/submit    —— 批量提交磁力到 NAS 下载器
    POST   /api/zspace/downloads —— 列出 NAS 当前下载任务（前端监控用）
    GET    /api/zspace/codes     —— 已抓取的 car id 集合（downloading / completed），
                                    wanted 卡片标 NAS 状态用，带 30s 内存缓存

设计要点
--------
- 配置全部从 .env 的 ``ZSPACE_*`` 字段读取，运行时只读；改配置 = 改 .env + 重启。
- ``status`` 不触发登录（只看内存配置），可热用于前端判断按钮是否可点。
- ``submit`` / ``downloads`` 走 :class:`ZSpaceClient` 单例（懒加载）；
  首次请求触发 RSA 登录，慢一点是预期。重启服务时 ``app.state.zspace`` 会
  重建（lifespan 关闭 + 新实例），所以改了 .env 必须重启。
- ``submit`` 串行提交每个 magnet：磁力提交是 NAS 后端写操作，并发可能触发
  它的反作弊限流。如果以后需要并发再加 ``asyncio.gather``。
- ``submit`` 成功后 invalidate ``codes`` 缓存，让 wanted 页下次刷新看到新下载。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
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


# --------------------------------------------------------------------------- #
# 单例管理
# --------------------------------------------------------------------------- #
def _get_store(request: Request) -> ZSpaceConfigStore:
    store: Optional[ZSpaceConfigStore] = getattr(request.app.state, "zspace_config_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="zspace 配置存储未初始化")
    return store


class _DummyZSpaceClient:
    """未配置 zspace 时返回的空壳客户端。

    让 wanted 路由的 ``get_download_codes()`` 直接拿到空集，**完全避免
    RSA 登录尝试**（登录失败抛 ZSpaceError 会被路由 except 兜底，但首次
    延迟好几秒 —— 未配置用户根本不该承受这个开销）。

    只实现 status_filter 实际用到的 2 个方法，其它路由在未配置时会先 503
    拒绝（看 /api/zspace/submit /downloads），永远走不到这个 dummy。
    """

    async def get_download_codes(
        self, *, force_refresh: bool = False
    ):
        # 5 元组对应 (downloading, completed, progress, unknown_status_codes, all_codes)
        return set(), set(), {}, set(), set()

    def invalidate_download_codes_cache(self) -> None:
        pass


def _get_or_create_client(request: Request) -> ZSpaceClient:
    """懒加载 ZSpaceClient 单例，存在 ``request.app.state.zspace``。

    未配置（enabled=False 或缺 host/user/password）→ 返回 :class:`_DummyZSpaceClient`，
    不触发 RSA 登录；用户点「下载中」chip 立即拿到空集，无延迟。
    """
    client: Optional[ZSpaceClient] = getattr(request.app.state, "zspace", None)
    if client is not None:
        return client
    store = _get_store(request)
    cfg = store.get()
    if not cfg.is_configured():
        # 缓存 dummy 单例：避免每次 wanted 请求都重读 cfg.is_configured()
        request.app.state.zspace = _DummyZSpaceClient()
        logger.debug("zspace 未配置，返回 _DummyZSpaceClient（get_download_codes 立即返空集）")
        return request.app.state.zspace
    client = ZSpaceClient(store.get)
    request.app.state.zspace = client
    return client


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #
def register(app: FastAPI) -> None:
    @app.get("/api/zspace/status")
    async def status(request: Request) -> Dict[str, Any]:
        """返回 zspace 集成状态（前端按钮启用/禁用 + 默认路径回填）。

        只读内存配置，不触发 NAS 登录。
        """
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
                    "（请在 .env 配置 ZSPACE_HOST / ZSPACE_USER / ZSPACE_PASSWORD "
                    "并重启服务）"
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

        # 至少一项成功 → 让 codes 缓存失效，下次 /api/zspace/codes 重新拉
        # （让 wanted 卡片立刻看到新下载状态，不用等 30s 缓存过期）
        if ok_count > 0:
            try:
                _get_or_create_client(request).invalidate_download_codes_cache()
            except Exception:  # noqa: BLE001
                pass

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

    @app.get("/api/zspace/codes")
    async def list_download_codes(
        request: Request,
        refresh: bool = Query(default=False, description="强制刷新，跳过 30s 缓存"),
    ) -> Dict[str, Any]:
        """返回 NAS 下载任务里抽出的 car id 集合，wanted 卡片 NAS 徽章用。

        形如：
            {
              "configured": true,
              "downloading": ["ABF-340", "IPZZ-907"],
              "completed":   ["SNOS-334", "HMN-880"],
              "fetched_at": "2026-08-26T13:30:15"  # 缓存时间
            }

        - 未配置时返回 ``{"configured": false, ...空集}``（HTTP 200，让前端优雅降级）
        - NAS 出错时同样返回 200 + 空集（避免单次失败把整个 wanted 列表搞 502）
        """
        store = _get_store(request)
        cfg = store.get()
        if not cfg.is_configured():
            return {
                "configured": False,
                "downloading": [],
                "completed": [],
                "fetched_at": None,
                "error": None,
            }
        client = _get_or_create_client(request)
        try:
            # 5 元组：downloading / completed / progress / unknown_status_codes / all_codes。
            # /api/zspace/codes 端点不返进度（避免 payload 膨胀），所以丢第 3 个。
            # unknown_status_codes：NAS 上有但状态字段缺失/无法判断的车
            #   （如跨设备任务：手机 app 提交但当前 device_id 看不见，
            #   submit 时会被 NAS 业务码 N201000 "任务已添加" 拒收）。
            #   前端用这个集合显示 "📡 在 NAS（其他设备）" 徽章 +
            #   用户重复提交时给友好提示。
            # all_codes：所有可识别 car id 的并集（调试用 + 兜底显示）。
            (
                downloading,
                completed,
                _progress,
                unknown_status_codes,
                all_codes,
            ) = await client.get_download_codes(force_refresh=refresh)
        except ZSpaceError as e:
            # 单次 NAS 失败不应让整个 wanted 页 502 —— 降级为空集
            # （前端拿到空集 = 没 NAS 徽章，跟未配置一致）
            logger.warning(f"获取 NAS 下载代码集失败：{e}")
            return {
                "configured": True,
                "downloading": [],
                "completed": [],
                "unknown_status_codes": [],
                "all_codes": [],
                "fetched_at": None,
                "error": str(e),
            }
        return {
            "configured": True,
            "downloading": sorted(downloading),
            "completed": sorted(completed),
            "unknown_status_codes": sorted(unknown_status_codes),
            "all_codes": sorted(all_codes),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "error": None,
        }
