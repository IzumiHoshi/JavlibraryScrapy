# javlibraryscrapy —— 画廊服务 + Scrapling 爬虫的容器镜像
#
# 关键点：
#   1. Scrapling 的 AsyncDynamicSession 依赖 playwright + patchright 的 chromium，
#      所以镜像里必须装浏览器本体和一堆系统库（--with-deps 负责后者）。
#   2. 浏览器装到 /ms-playwright（PLAYWRIGHT_BROWSERS_PATH），两个包共用同一份，
#      避免镜像里出现两套 ~400MB 的 chromium。
#   3. 依赖用 uv sync --frozen 装（走 uv.lock），保证可复现。
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TZ=Asia/Shanghai

# uv（官方静态二进制，不额外拉 Python）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# ---------- 依赖层（只在 pyproject/uv.lock 变化时失效）----------
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ---------- 浏览器层 ----------
# --with-deps 会 apt install chromium 需要的系统库（libnss3、libasound2 等）。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv run playwright install --with-deps chromium \
 && uv run patchright install chromium \
 && rm -rf /var/lib/apt/lists/*

# ---------- 项目代码层 ----------
COPY src ./src
COPY scripts ./scripts
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

# 容器内默认的数据落点，全部由 docker-compose 挂卷 + 环境变量覆盖。
# output/ 只放临时数据（日志、封面缓存、scratch）；持久化文件走 /data。
RUN mkdir -p /app/output /data
VOLUME ["/app/output"]

EXPOSE 8000

# 画廊服务。想跑别的入口时用 `docker compose run --rm app <cmd>` 覆盖。
CMD ["python", "-m", "javlibraryscrapy.cli.gallery", "--host", "0.0.0.0", "--port", "8000"]
