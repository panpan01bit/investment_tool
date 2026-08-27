"""集中式配置：所有密钥 / token 只从环境变量或 .env 读取，源码零凭据。

命名规则：
- 新配置使用 `INVESTLAB_` 前缀；
- 为兼容旧 macro-bot 的 .env，保留若干旧变量名作为别名（KIMI_API_KEY、TAVILY_API_KEY、
  TUSHARE_TOKEN、FEISHU_* 已废弃不再读取）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 与仓库根目录相关的默认路径（src/investlab/config.py → 上两级 = 仓库根）
REPO_ROOT = Path(__file__).resolve().parents[2]

ENV_FILE_CANDIDATES = [
    REPO_ROOT / ".env",
    Path.home() / ".investlab" / ".env",
]


def _find_dotenv() -> Path | None:
    for p in ENV_FILE_CANDIDATES:
        if p.is_file():
            return p
    return None


def load_env(env_file: str | os.PathLike[str] | None = None, override: bool = False) -> Path | None:
    """加载 .env（优先仓库根目录，其次 ~/.investlab/.env）。显式传入路径优先。"""
    path = Path(env_file) if env_file else _find_dotenv()
    if path and path.is_file():
        load_dotenv(path, override=override)
        return path
    return None


def _env(*names: str, default: str = "") -> str:
    """按顺序取第一个非空环境变量；用于新名字 + 旧别名兼容。"""
    for n in names:
        v = os.environ.get(n)
        if v is not None and v.strip() != "":
            return v.strip()
    return default


def _bool(*names: str, default: bool = False) -> bool:
    raw = _env(*names, default=str(default)).lower()
    return raw in ("1", "true", "yes", "on")


def _path(*names: str, default: str) -> Path:
    return Path(_env(*names, default=default)).expanduser()


@dataclass(frozen=True)
class Settings:
    """不可变全局配置。通过 get_settings() 单例访问。"""

    # ---- 目录布局（本地数据不入库 git）----
    data_dir: Path
    vault_path: Path
    reports_inbox: Path

    # ---- LLM（OpenAI 兼容协议，默认指向 Kimi/Moonshot）----
    llm_base_url: str
    llm_api_key: str = field(repr=False, default="")
    llm_model: str = ""       # 快速模型：摘要 / 标签 / 普通问答
    llm_think_model: str = ""  # 思考模型：深度分析 / 简报结论
    llm_vision_model: str = ""  # 视觉模型：图表理解，留空则跳过视觉步骤

    # ---- 数据源 token（均可选）----
    tushare_token: str = field(repr=False, default="")
    fred_api_key: str = field(repr=False, default="")

    # ---- 搜索（DuckDuckGo 免费内置；Tavily 可选增强）----
    tavily_api_key: str = field(repr=False, default="")
    search_max_results: int = 8
    search_timeout_s: int = 20

    # ---- 行情回退链 ----
    quotes_primary: str = "eastmoney"  # eastmoney | tencent

    # ---- 本地 API 服务（预留线上部署）----
    api_host: str = "127.0.0.1"
    api_port: int = 8300
    cors_origins: list[str] = field(default_factory=lambda: [
        "http://localhost:5173", "http://127.0.0.1:5173",
    ])
    auth_disabled: bool = True
    admin_username: str = "admin"
    admin_password: str = field(repr=False, default="")
    internal_secret: str = field(repr=False, default="")

    # ---- 行为开关 ----
    enable_quant_net: bool = True   # 是否允许量化模块联网取数（离线演示可关）
    report_vision_enabled: bool = True  # 券商报告图表走视觉模型
    report_engine: str = "pymupdf"  # 研报解析引擎: pymupdf | mineru（需安装 MinerU）
    log_level: str = "INFO"

    # ---- 推送通知（ntfy / Bark，均可选）----
    notify_ntfy_topic: str = field(repr=False, default="")
    notify_ntfy_server: str = "https://ntfy.sh"
    notify_bark_url: str = field(repr=False, default="")
    notify_allow_private: bool = False  # 允许推送到自建内网服务器

    @property
    def briefings_dir(self) -> Path:
        return self.data_dir / "briefings"

    @property
    def reports_library_dir(self) -> Path:
        return self.data_dir / "reports" / "library"

    @property
    def cache_db(self) -> Path:
        return self.data_dir / "cache.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def news_dir(self) -> Path:
        return self.data_dir / "news"

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.briefings_dir,
            self.reports_library_dir,
            self.logs_dir,
            self.news_dir,
            self.reports_inbox,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def token_status(self) -> dict[str, bool]:
        """供前端/doctor 展示哪些能力已配置可用。"""
        return {
            "llm": bool(self.llm_api_key),
            "vision": bool(self.llm_api_key and self.llm_vision_model),
            "tushare": bool(self.tushare_token),
            "fred": bool(self.fred_api_key),
            "tavily": bool(self.tavily_api_key),
            "admin_auth": bool(not self.auth_disabled and self.admin_password),
        }


def build_settings(environ: dict[str, str] | None = None) -> Settings:
    """从（默认或注入的）环境构造 Settings；便于测试注入。"""

    def ev(*names: str, default: str = "") -> str:
        if environ is not None:
            for n in names:
                v = environ.get(n)
                if v is not None and v.strip() != "":
                    return v.strip()
            return default
        return _env(*names, default=default)

    data_dir = _path("INVESTLAB_DATA_DIR", default=str(REPO_ROOT / "data"))
    settings = Settings(
        data_dir=data_dir,
        vault_path=_path(
            "INVESTLAB_OBSIDIAN_VAULT", "OBSIDIAN_VAULT_PATH",
            default=str(data_dir / "vault"),
        ),
        reports_inbox=_path(
            "INVESTLAB_REPORTS_INBOX", default=str(data_dir / "reports" / "inbox"),
        ),
        llm_base_url=ev(
            "INVESTLAB_LLM_BASE_URL", "KIMI_BASE_URL", "LLM_BASE_URL",
            default="https://api.moonshot.cn/v1",
        ),
        llm_api_key=ev("INVESTLAB_LLM_API_KEY", "KIMI_API_KEY", "LLM_API_KEY"),
        llm_model=ev(
            "INVESTLAB_LLM_MODEL", "KIMI_MODEL", "LLM_MODEL",
            default="kimi-k2.5",
        ),
        llm_think_model=ev(
            "INVESTLAB_LLM_THINKING_MODEL", "KIMI_STRONG_MODEL",
            default="kimi-k2-thinking",
        ),
        llm_vision_model=ev(
            "INVESTLAB_LLM_VISION_MODEL", "LLM_VISION_MODEL", default=""
        ),
        tushare_token=ev("TUSHARE_TOKEN"),
        fred_api_key=ev("FRED_API_KEY"),
        tavily_api_key=ev("TAVILY_API_KEY"),
        search_max_results=int(ev("INVESTLAB_SEARCH_MAX_RESULTS", default="8")),
        search_timeout_s=int(ev("INVESTLAB_SEARCH_TIMEOUT", default="20")),
        quotes_primary=ev("INVESTLAB_QUOTES_PRIMARY", default="eastmoney"),
        api_host=ev("INVESTLAB_API_HOST", default="127.0.0.1"),
        api_port=int(ev("INVESTLAB_API_PORT", default="8300")),
        cors_origins=[
            o.strip()
            for o in ev(
                "INVESTLAB_CORS_ORIGINS", "APP_CORS_ORIGINS",
                default="http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if o.strip()
        ],
        auth_disabled=_bool("INVESTLAB_AUTH_DISABLED", default=True),
        admin_username=ev("INVESTLAB_ADMIN_USERNAME", "ADMIN_USERNAME", default="admin"),
        admin_password=ev("INVESTLAB_ADMIN_PASSWORD", "ADMIN_PASSWORD"),
        internal_secret=ev("INVESTLAB_INTERNAL_SECRET", "INTERNAL_SECRET"),
        enable_quant_net=_bool("INVESTLAB_QUANT_NET", default=True),
        report_vision_enabled=_bool("INVESTLAB_REPORT_VISION", default=True),
        report_engine=ev("INVESTLAB_REPORT_ENGINE", default="pymupdf").lower(),
        log_level=ev("INVESTLAB_LOG_LEVEL", default="INFO"),
        notify_ntfy_topic=ev("INVESTLAB_NOTIFY_NTFY_TOPIC"),
        notify_ntfy_server=ev("INVESTLAB_NOTIFY_NTFY_SERVER",
                              default="https://ntfy.sh"),
        notify_bark_url=ev("INVESTLAB_NOTIFY_BARK_URL"),
        notify_allow_private=_bool("INVESTLAB_NOTIFY_ALLOW_PRIVATE", default=False),
    )
    return settings


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    global _settings
    if _settings is None or refresh:
        load_env()
        _settings = build_settings()
        _settings.ensure_dirs()
    return _settings


def reset_settings_for_testing(environ: dict[str, str]) -> Settings:
    """测试专用：注入隔离环境。"""
    global _settings
    _settings = build_settings(environ)
    _settings.ensure_dirs()
    return _settings
