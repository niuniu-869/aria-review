"""运行配置, 从环境变量读取 (不引入 pydantic-settings 依赖, 保持轻量)。"""
import os
from pathlib import Path
from urllib.parse import urlparse

# 本地/测试: 加载 services/agent/.env (生产走 docker-compose env_file)。缺 python-dotenv 时跳过。
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ModuleNotFoundError:
    pass


def _validate_url(u: str) -> str:
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError(f"R_ANALYSIS_URL 非法 (需 http/https): {u!r}")
    return u


def _optional_url(u: str) -> str:
    if not u:
        return ""
    return _validate_url(u)


class Settings:
    r_analysis_url: str = _validate_url(os.environ.get("R_ANALYSIS_URL", "http://localhost:8001"))
    request_timeout: float = float(os.environ.get("R_REQUEST_TIMEOUT", "120"))
    # 数据接入 (OpenAlex 主题检索/参考文献反查 + 引用补全) 可达数十秒, 单独长超时
    ingest_timeout: float = float(os.environ.get("R_INGEST_TIMEOUT", "300"))
    health_timeout: float = float(os.environ.get("R_HEALTH_TIMEOUT", "5"))
    max_upload_bytes: int = int(os.environ.get("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    cors_origins: list[str] = [
        origin.strip()
        for origin in os.environ.get(
            "CORS_ORIGINS",
            "http://localhost:8080,http://localhost:5173",
        ).split(",")
        if origin.strip()
    ]
    # LLM (综述/AI 功能)。无 key 时回退到 FakeStreamClient (测试/本地)。
    deepseek_api_key: str = os.environ.get("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    review_records_limit: int = int(os.environ.get("REVIEW_RECORDS_LIMIT", "40"))
    # 三层领域数据层 (Library/Project/Corpus)
    database_url: str = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://bibliocn@localhost/bibliocn")
    test_database_url: str = os.environ.get(
        "TEST_DATABASE_URL", "postgresql+asyncpg://bibliocn@localhost/bibliocn_test")
    # MinerU 全文摄取 (阶段5-1)
    ocr_token: str = os.environ.get("OCR_AUTHORIZATION_TOKEN", "")
    mineru_base_url: str = os.environ.get("MINERU_BASE_URL", "https://mineru.net/api/v4")
    sciverse_base_url: str = _optional_url(os.environ.get("SCIVERSE_BASE_URL", "https://api.sciverse.space"))
    sciverse_api_token: str = os.environ.get("SCIVERSE_API_TOKEN", "")
    sciverse_timeout: float = float(os.environ.get("SCIVERSE_TIMEOUT", "60"))
    sciverse_content_chunk_chars: int = int(os.environ.get("SCIVERSE_CONTENT_CHUNK_CHARS", "7000"))
    sciverse_content_max_chars: int = int(os.environ.get("SCIVERSE_CONTENT_MAX_CHARS", "500000"))
    # 多源检索补强 (Frontier Review 集成)。缺 key 的源在可用源清单显式标"未配置"，
    # 不静默 return [] (codex P1：静默会让线上误判"没文献")。见 app/sources/。
    core_api_key: str = os.environ.get("CORE_API_KEY", "")
    openalex_mailto: str = os.environ.get("OPENALEX_MAILTO", "")  # OpenAlex polite-pool 标识 (免鉴权)
    unpaywall_email: str = os.environ.get("UNPAYWALL_EMAIL", "")  # Unpaywall /v2/{doi} 必填 email (免鉴权)
    semantic_scholar_api_key: str = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")  # 无 key 易 429
    crossref_mailto: str = os.environ.get("CROSSREF_MAILTO", "")  # Crossref polite-pool 标识 (免鉴权)
    # 多源统一超时/重试 + PDF 安全下载闸门 (§4.5)
    multisource_timeout: float = float(os.environ.get("MULTISOURCE_TIMEOUT", "30"))
    multisource_max_retries: int = int(os.environ.get("MULTISOURCE_MAX_RETRIES", "2"))
    multisource_per_source_limit: int = int(os.environ.get("MULTISOURCE_PER_SOURCE_LIMIT", "50"))
    multisource_total_cap: int = int(os.environ.get("MULTISOURCE_TOTAL_CAP", "200"))
    pdf_download_max_bytes: int = int(os.environ.get("PDF_DOWNLOAD_MAX_BYTES", str(50 * 1024 * 1024)))
    pdf_download_timeout: float = float(os.environ.get("PDF_DOWNLOAD_TIMEOUT", "60"))
    pdf_resolve_max_candidates: int = int(os.environ.get("PDF_RESOLVE_MAX_CANDIDATES", "25"))
    image_api_key: str = os.environ.get("IMAGE_API_KEY", "")
    image_base_url: str = os.environ.get("IMAGE_BASE_URL", "https://api.openai.com/v1")
    image_model: str = os.environ.get("IMAGE_MODEL", "gpt-image-1")
    image_size: str = os.environ.get("IMAGE_SIZE", "1024x1024")
    # 全文 Markdown 存储根目录
    corpora_dir: str = os.environ.get("BIBLIOCN_CORPORA_DIR", "/tmp/bibliocn_corpora")
    # 认证与计费 (Phase B)。生产必设 APP_SECRET_KEY (用于 session 签名 + BYOK Fernet 加密);
    # 未设时开发用派生占位并在启动告警 (见 auth.py)。
    app_secret_key: str = os.environ.get("APP_SECRET_KEY", "")
    session_ttl_days: int = int(os.environ.get("SESSION_TTL_DAYS", "14"))
    invite_required: bool = os.environ.get("INVITE_REQUIRED", "1") != "0"  # 注册需邀请码
    allow_registration: bool = os.environ.get("ALLOW_REGISTRATION", "1") != "0"  # 是否开放自助注册端点
    # 平台供给计费: 各任务类型消耗积分 (BYOK 走用户自带 key 不计费)。可用 env 覆盖单价。
    credit_cost_review: int = int(os.environ.get("CREDIT_COST_REVIEW", "10"))
    credit_cost_chat: int = int(os.environ.get("CREDIT_COST_CHAT", "2"))
    credit_cost_parse_per_page: int = int(os.environ.get("CREDIT_COST_PARSE_PER_PAGE", "1"))
    credit_cost_extract: int = int(os.environ.get("CREDIT_COST_EXTRACT", "3"))
    # CSRF: 允许的同源来源 (非 GET 请求校验 Origin/Referer)。默认复用 cors_origins。
    trusted_origins: list[str] = [
        o.strip() for o in os.environ.get("TRUSTED_ORIGINS", "").split(",") if o.strip()
    ]
    # 环境标识：production 时强制要求 APP_SECRET_KEY（见 auth.py 启动校验）。
    env: str = os.environ.get("BIBLIOCN_ENV", "development")
    # 会话 cookie Secure：""=按请求 scheme 自动；"1"=强制开；"0"=强制关。
    # 生产应设 "1"，或保持 auto 但确保反代正确透传 X-Forwarded-Proto。
    cookie_secure: str = os.environ.get("SESSION_COOKIE_SECURE", "")


settings = Settings()
