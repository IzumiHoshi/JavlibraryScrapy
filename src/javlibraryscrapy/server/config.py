"""配置：从项目根目录的 .env 读取画廊所需的设置。

原画廊把这些散在 ``GalleryApp.__init__`` 里；这里统一用 Pydantic，
后续在路由里通过 ``Depends(get_settings)`` 拿到。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录。本文件位于 src/javlibraryscrapy/server/config.py，
# parents[3] 指向仓库根（src 的上一级），用于加载 .env 等。
ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv_once() -> None:
    """只加载一次项目根 .env（idempotent）。"""
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


_load_dotenv_once()


class Settings(BaseSettings):
    """画廊服务配置。

    字段语义对齐原 ``GalleryApp`` 读取的环境变量，保持向后兼容。
    """

    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 抓取 ----
    javbus_url: str = Field(
        default="https://www.javbus.com/",
        description="JAVBus 视频页 URL 前缀（用于拼接 code）。",
    )
    javbus_base_url: str = Field(
        default="https://www.javbus.com",
        description="用于解析相对封面 URL 的 JAVBus 基础 URL。",
    )

    # ---- 代理 ----
    proxy_enabled: bool = Field(
        default=False,
        description="控制封面代理的 auto 模式；磁力抓取始终启用 PROXY。",
    )
    proxy: Optional[str] = Field(
        default=None,
        description="HTTP/HTTPS/SOCKS5 代理地址；留空表示不使用代理。",
    )

    # ---- 下载/请求 ----
    user_agent: str = Field(
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    download_timeout: int = Field(default=10, ge=1, description="封面下载超时（秒）。")
    verify_ssl: bool = Field(default=False, description="是否校验 HTTPS 证书。")

    # ---- 本地库 ----
    library_root: Optional[Path] = Field(
        default=None,
        description="本地影片库根目录；None 表示禁用本地库功能。",
    )
    library_index: Path = Field(
        default=ROOT / "output" / "library_index.json",
        description="本地库索引输出路径。",
    )

    # ---- 最想要列表（Most Wanted）----
    mostwanted_library_root: Optional[Path] = Field(
        default=None,
        alias="MOSTWANTED_LIBRARY_ROOT",
        description=(
            "JAVLibrary「最想要」列表的数据根目录："
            "javlibrary_movies.json + 每部影片的 cover/samples 文件夹都放在这里；"
            "None 表示仍走默认 output/。"
        ),
    )

    # ---- Scrapling 透传（原服务只透传给 JavbusSpider；这里保留供将来的 env 注入） ----
    scrapling_load_dom: bool = Field(default=True, alias="SCRAPLING_LOAD_DOM")
    scrapling_network_idle: bool = Field(default=True, alias="SCRAPLING_NETWORK_IDLE")
    scrapling_disable_resources: bool = Field(default=True, alias="SCRAPLING_DISABLE_RESOURCES")
    scrapling_headless: bool = Field(default=True, alias="SCRAPLING_HEADLESS")
    scrapling_timeout: int = Field(default=30000, alias="SCRAPLING_TIMEOUT")

    @field_validator("javbus_url")
    @classmethod
    def _ensure_trailing_slash(cls, v: str) -> str:
        return v if v.endswith("/") else v + "/"

    @field_validator("proxy")
    @classmethod
    def _strip_proxy(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        return s or None

    @field_validator("library_root", mode="before")
    @classmethod
    def _coerce_library_root(cls, v):
        if v in (None, ""):
            return None
        return v


def load_settings() -> Settings:
    """构造 Settings 实例（每次调用都重新读取 .env，方便测试覆盖）。"""
    return Settings()