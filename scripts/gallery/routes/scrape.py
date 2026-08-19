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

        # Q4 决策：本地库已存在的车牌自动跳过（不入 magnets_links.txt）
        idx = state.library_index
        skipped = []
        scrape_codes = []
        for code in codes:
            if idx and idx.find_match(code):
                skipped.append(code)
            else:
                scrape_codes.append(code)

        if not scrape_codes:
            # 原服务：200 + body 含 error 字段；这里保持一致以便前端兼容
            return {
                "error": f"全部 {len(codes)} 个车牌本地已存在，无需抓取",
                "skipped": skipped,
            }

        try:
            job = state.start_job(scrape_codes, lambda j: start_scrape_job(
                j, state.output_dir, state.proxy, state.library_index
            ))
            job.skipped = skipped
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))

        return {
            "job_id": job.id,
            "total": len(scrape_codes),
            "skipped": skipped,
        }

    @app.get("/api/job/{job_id}")
    async def get_job(job_id: str, request: Request) -> Dict[str, Any]:
        state = request.app.state.gallery
        job = state.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job.snapshot()