"""页面路由：GET /, /wanted, /library —— 统一返回 ``static/index.html``。

前端静态资源（CSS / JS 模块）由 ``app.py`` 的 ``VersionedStaticFiles`` 挂载
在 ``/static/`` 提供；本模块负责把 SPA 入口 HTML 吐回去，并在 HTML 里把
``/static/...`` 链接替换成 ``/static/<dir>/<name>.<hash>.<ext>`` 版本化 URL。

为什么需要版本化：
- 浏览器看到 URL 变了 → 必定发新请求（不是 304），不需要 Cache-Control 头
- 手机浏览器也可以稳定地拿到新文件（不像 If-Modified-Since 那样可能被忽略）
- 改完 CSS/JS 后用户直接刷新 → 立刻生效

版本化策略：hash = 文件 mtime 低 32 位的 8 字符 hex。mtime 变了 hash 就变
→ URL 变了 → 浏览器重新拉。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from javlibraryscrapy._paths import PACKAGE_ROOT

logger = logging.getLogger("gallery.pages")

INDEX_PATH = PACKAGE_ROOT / "static" / "index.html"
STATIC_DIR = PACKAGE_ROOT / "static"

# 匹配 ``/static/<非空白引号字符>+`` —— 抓 href/src 等属性值。
_STATIC_URL_RE = re.compile(r'(?P<attr>href|src)="(?P<url>/static/[^"]+)"')


def _versioned_url(rel_path: str) -> str:
    """``js/wanted.js`` → ``/static/js/wanted.5a1b2c3d.js``。

    文件不存在时原样返回（开发态偶尔发生，避免 HTML 渲染挂掉）。
    """
    file_path = STATIC_DIR / rel_path
    if not file_path.exists():
        return f"/static/{rel_path}"
    mtime = int(file_path.stat().st_mtime) & 0xFFFFFFFF
    hash_hex = format(mtime, "08x")
    p = Path(rel_path)
    new_name = f"{p.stem}.{hash_hex}{p.suffix}"
    parent = p.parent.as_posix()
    if parent and parent != ".":
        return f"/static/{parent}/{new_name}"
    return f"/static/{new_name}"


def _rewrite_html(html: str) -> str:
    """把 HTML 里所有 ``/static/...`` 引用替换为版本化 URL。"""

    def replace(m: re.Match) -> str:
        attr = m.group("attr")
        url = m.group("url")
        # 取 ``/static/`` 之后的部分作为相对 static_dir 的路径
        rel = url[len("/static/"):]
        # 只对直接子文件做版本化；其他深路径（理论上没有）原样保留
        return f'{attr}="{_versioned_url(rel)}"'

    return _STATIC_URL_RE.sub(replace, html)


def _build_importmap() -> str:
    """生成 ES module importmap，把所有 ``/static/js/*.js`` 映射到版本化 URL。

    为什么需要：
    HTML 里只显式引用 ``main.js``（被 _rewrite_html 加 hash）；
    但 main.js 内部 ``import './wanted.js'`` 是**裸相对路径**，浏览器命中
    disk cache / NAS 反代缓存就会拿到旧文件。

    importmap 在 import 解析阶段把 ``/static/js/wanted.js`` 重写成
    ``/static/js/wanted.<hash>.js`` —— 跟 HTML 引用 main.js 一脉相承。

    注意：importmap 必须在所有 ``<script type="module">`` 之前。
    """
    js_dir = STATIC_DIR / "js"
    if not js_dir.exists():
        return ""
    imports: dict[str, str] = {}
    for js_file in sorted(js_dir.glob("*.js")):
        rel = js_file.relative_to(STATIC_DIR).as_posix()
        versioned = _versioned_url(rel)
        # key 用绝对路径（浏览器 import 时解析出来的 URL 形式）
        imports[f"/static/{rel}"] = versioned
    if not imports:
        return ""
    return json.dumps({"imports": imports}, ensure_ascii=False)


def register(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    @app.get("/wanted", response_class=HTMLResponse)
    @app.get("/library", response_class=HTMLResponse)
    async def _page() -> HTMLResponse:
        html = INDEX_PATH.read_text(encoding="utf-8")
        html = _rewrite_html(html)

        # importmap 必须出现在首个 module script 之前。插入到 </head> 闭合前最稳。
        importmap_json = _build_importmap()
        if importmap_json:
            importmap_tag = (
                f'<script type="importmap">{importmap_json}</script>\n'
                '  '
                # 多一个缩进，跟原文件 head 块对齐
            )
            html = html.replace("</head>", f"{importmap_tag}</head>", 1)

        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )