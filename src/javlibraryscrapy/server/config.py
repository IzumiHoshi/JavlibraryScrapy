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

# 项目根目录统一在 javlibraryscrapy/_paths.py 算好
from javlibraryscrapy._paths import REPO_ROOT as ROOT


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

    # ---- 极空间 NAS 集成（/api/zspace/*）----
    # 把 wanted 抓取得到的 magnet 推到极空间下载器。需要先在极空间后台「下载」
    # app 启用下载服务（启用后 qbittorrent-nox/aria2c/xunlei 会启动）。
    zspace_enabled: bool = Field(
        default=False,
        description="启用极空间下载集成。开启前先在 NAS 后台启用「下载」app。",
    )
    zspace_host: Optional[str] = Field(
        default=None,
        description="极空间内网 IP，如 192.168.1.100（API 端口固定 5055）。",
    )
    zspace_user: Optional[str] = Field(
        default=None,
        description="极空间登录用户名（注册手机号）。",
    )
    zspace_password: Optional[str] = Field(
        default=None,
        description="极空间登录密码（>=8 字符；走 RSA 公钥加密传输）。",
    )
    zspace_device_id: Optional[str] = Field(
        default=None,
        description=(
            "极空间 device_id，32 字符 hex。首次会因 N001414 触发短信验证，"
            "验证后从浏览器 cookie 复制 device_id 填到这里，可避免每次重验证。"
            "留空则按 zspace_skill/nas/auth.py 自动从机器指纹生成。"
        ),
    )
    zspace_download_path: str = Field(
        default="/sata14/my/data/zvideo/JAV",
        description=(
            "极空间默认下载目录（NAS 文件系统路径，/pool/my/data/.../）。"
            "pool 名取决于你的存储池（常见 sata14/nvme19），可从 /auth/login 响应的"
            " sp_perms 查到。修改前先在 NAS 后台确认目录存在且可写。"
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