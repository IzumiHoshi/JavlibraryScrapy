"""POST /api/scrape + GET /api/job/{id} —— 磁力抓取任务。

保留原行为：
- code 正则 ``[A-Z0-9_-]{2,32}``、最多 300 个
- 本地库已存在的 code 被服务端过滤（写入 magnets.json 但不入 magnets_links.txt）
- 已运行中再提交返回 409
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request

from ..services.jobs import start_scrape_job
from ..services.library import MAX_CODES_PER_JOB, CARID_RE

logger = logging.getLogger("gallery.scrape")


def register(app: FastAPI) -> None:
    @app.post("/api/scrape")
    async def scrape(request: Request) -> Dict[str, Any]:
        state = request.app.state.gallery
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="请求体不是合法 JSON")

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="缺少 codes 列表")
        raw_codes = payload.get("codes")
        if not isinstance(raw_codes, list):
            raise HTTPException(status_code=400, detail="缺少 codes 列表")

        codes = []
        for item in raw_codes:
            if not isinstance(item, str):
                continue
            code = item.strip().upper()
            # 车牌只允许字母、数字、连字符与下划线，防止拼进 URL 时被注入
            if code and CARID_RE.fullmatch(code) and code not in codes:
                codes.append(code)

        if not codes:
            raise HTTPException(status_code=400, detail="没有有效的车牌")
        if len(codes) > MAX_CODES_PER_JOB:
            raise HTTPException(
                status_code=400,
                detail=f"一次最多抓取 {MAX_CODES_PER_JOB} 个车牌",
            )

        # 三类处理：
        #   1) 本地库已存在     → 入 skipped（status=local_skip，不入 magnets_links.txt）
        #   2) wanted 已有 magnet → 不跑 JavBus；塞进 job.extra_cached，write 时合并到
        #                              magnets.json + magnets_links.txt（让 NAS 批量发送仍能拿到）
        #   3) 其他            → 正常抓 JavBus（写入 scrape_only_codes → job.codes）
        idx = state.library_index
        wanted = request.app.state.wanted
        skipped: list[str] = []
        scrape_only_codes: list[str] = []
        cached_entries: List[Dict[str, Any]] = []
        for code in codes:
            if idx and idx.find_match(code):
                skipped.append(code)
                continue
            entry = wanted.get(code) if wanted else None
            if entry and (entry.get("magnet") or "").strip():
                cached_entries.append({
                    "code": code,
                    "status": "ok",
                    "title": entry.get("title", ""),
                    "magnet": entry["magnet"],
                    "release_date": entry.get("release_date", ""),
                    "actors": entry.get("actors", ""),
                })
            else:
                scrape_only_codes.append(code)

        if not scrape_only_codes and not cached_entries:
            return {
                "error": f"全部 {len(codes)} 个车牌本地已存在，无需抓取",
                "skipped": skipped,
            }

        try:
            # job.codes 只装要真正抓的——MagnetSpider 不会重复处理 cached
            job = state.start_job(scrape_only_codes, lambda j: start_scrape_job(
                j, state.output_dir, state.proxy, state.library_index
            ))
            job.skipped = skipped
            job.extra_cached = cached_entries
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))

        return {
            "job_id": job.id,
            # 端点用户感知的 total = 真抓 + cached（用于前端 toast）
            "total": len(scrape_only_codes) + len(cached_entries),
            "scrape_total": len(scrape_only_codes),
            "cached_count": len(cached_entries),
            "skipped": skipped,
        }

    @app.get("/api/job/{job_id}")
    async def get_job(job_id: str, request: Request) -> Dict[str, Any]:
        state = request.app.state.gallery
        job = state.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job.snapshot()