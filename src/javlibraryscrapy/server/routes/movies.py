"""GET /api/movies —— 影片列表（含本地库匹配与封面代理 URL 改写）。"""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict

from fastapi import FastAPI, Request


def register(app: FastAPI) -> None:
    @app.get("/api/movies")
    async def movies(request: Request) -> Dict[str, Any]:
        state = request.app.state.gallery
        idx = state.library_index
        out_movies = []
        for m in state.movies:
            cover = m["cover_url"]
            if cover and state.image_proxy:
                cover = "/api/cover?url=" + urllib.parse.quote(cover, safe="")
            lib_match = idx.find_match(m["code"]) if idx else None
            out_movies.append(
                {
                    **m,
                    "cover": cover,
                    "javbus_url": state.javbus_url + m["code"],
                    "local_exists": lib_match is not None,
                    "library_folder": lib_match.folder if lib_match else None,
                }
            )

        job = state.job
        return {
            "movies": out_movies,
            "source": str(state.data_path),
            "output_dir": str(state.output_dir),
            "active_job": job.id if job and job.status == "running" else None,
            "library_configured": state.library_root is not None,
        }