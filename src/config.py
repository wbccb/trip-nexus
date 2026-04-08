import os
from dotenv import load_dotenv


def _load_runtime_env() -> str:
    """按运行环境只加载开发或生产配置，避免再混入默认 .env。"""
    environment = os.getenv("ENVIRONMENT", "development").strip().lower() or "development"
    env_file = ".env.production" if environment == "production" else ".env.development"
    # 运行时配置统一只从目标环境文件加载；系统环境变量仍然保留更高优先级。
    load_dotenv(env_file)
    return environment


# 在模块导入阶段先完成环境变量装载，保证后续 Config 读取的是同一套来源。
ENVIRONMENT = _load_runtime_env()

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODEL_CACHE_DIR = os.getenv("MODEL_CACHE_DIR", os.path.join(PROJECT_ROOT, "model_cache"))
os.environ.setdefault("MODEL_CACHE_DIR", MODEL_CACHE_DIR)
HF_HOME = os.getenv("HF_HOME", os.path.join(MODEL_CACHE_DIR, "huggingface"))
os.environ.setdefault("HF_HOME", HF_HOME)
SENTENCE_TRANSFORMERS_HOME = os.getenv(
    "SENTENCE_TRANSFORMERS_HOME",
    os.path.join(MODEL_CACHE_DIR, "sentence_transformers"),
)
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", SENTENCE_TRANSFORMERS_HOME)
os.environ.pop("TRANSFORMERS_CACHE", None)
HUGGINGFACE_HUB_CACHE = os.getenv("HUGGINGFACE_HUB_CACHE", os.path.join(HF_HOME, "hub"))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", HUGGINGFACE_HUB_CACHE)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

class Config:
    ENVIRONMENT = ENVIRONMENT
    SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "")
    SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASSWORD", "")
    LOG_LEVEL = os.getenv("TRIPNEXUS_LOG_LEVEL", "WARNING").upper()
    SUPER_ADMIN_EMAILS = os.getenv("SUPER_ADMIN_EMAILS", "")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "tripnexus-dev-secret")
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "120"))
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = int(os.getenv("PASSWORD_RESET_TOKEN_EXPIRE_MINUTES", "30"))
    RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "60"))
    AUTH_RATE_LIMIT_MAX_REQUESTS = int(os.getenv("AUTH_RATE_LIMIT_MAX_REQUESTS", "20"))
    ADMIN_RATE_LIMIT_MAX_REQUESTS = int(os.getenv("ADMIN_RATE_LIMIT_MAX_REQUESTS", "120"))
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "25"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "")

    # SearXNG配置
    SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

    # Embedding 模型配置
    EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai_compatible")
    EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "")
    EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3")
    EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
    SENTENCE_BERT_MODEL = os.getenv("SENTENCE_BERT_MODEL", "all-MiniLM-L6-v2")
    MINILM_MODEL = os.getenv("MINILM_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

    # LLM 基础配置（兼容旧版简单 OpenAI 场景）
    LLM_MODEL = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

    # LLM 调用链-分析阶段（第 1 次调用：实体抽取 + 意图识别）
    # 默认采用 openai_compatible 提供方，方便直接使用远端 /v1 网关
    ANALYSIS_PROVIDER = os.getenv("ANALYSIS_PROVIDER", "openai_compatible")
    # 支持使用逗号分隔配置多个 Base URL，第一个作为默认值
    _ANALYSIS_BASE_URL_RAW = os.getenv(
        "ANALYSIS_BASE_URL",
        "",
    )
    ANALYSIS_BASE_URL_OPTIONS = [
        u.strip() for u in _ANALYSIS_BASE_URL_RAW.split(",") if u.strip()
    ] or ["http://localhost:11434"]
    ANALYSIS_BASE_URL = ANALYSIS_BASE_URL_OPTIONS[0]
    # 支持使用逗号分隔配置多个模型名称，第一个作为默认值
    _ANALYSIS_MODEL_NAME_RAW = os.getenv("ANALYSIS_MODEL_NAME", "deepseek-r1:7b")
    ANALYSIS_MODEL_NAME_OPTIONS = [
        m.strip() for m in _ANALYSIS_MODEL_NAME_RAW.split(",") if m.strip()
    ] or ["deepseek-r1:7b"]
    ANALYSIS_MODEL_NAME = ANALYSIS_MODEL_NAME_OPTIONS[0]
    ANALYSIS_API_KEY = os.getenv("ANALYSIS_API_KEY", "")
    ANALYSIS_TEMPERATURE = float(os.getenv("ANALYSIS_TEMPERATURE", "0.7"))

    # LLM 调用链-生成阶段（第 2 次调用：行程生成 / 修改）
    # 默认同样采用 openai_compatible 提供方，与分析阶段保持一致
    GENERATION_PROVIDER = os.getenv("GENERATION_PROVIDER", "openai_compatible")
    # 同样支持逗号分隔多个 Base URL，第一个作为默认值
    _GENERATION_BASE_URL_RAW = os.getenv(
        "GENERATION_BASE_URL",
        "",
    )
    GENERATION_BASE_URL_OPTIONS = [
        u.strip() for u in _GENERATION_BASE_URL_RAW.split(",") if u.strip()
    ] or ["http://localhost:11434"]
    GENERATION_BASE_URL = GENERATION_BASE_URL_OPTIONS[0]
    # 同样支持逗号分隔配置多个模型名称，第一个作为默认值
    _GENERATION_MODEL_NAME_RAW = os.getenv("GENERATION_MODEL_NAME", "deepseek-r1:7b")
    GENERATION_MODEL_NAME_OPTIONS = [
        m.strip() for m in _GENERATION_MODEL_NAME_RAW.split(",") if m.strip()
    ] or ["deepseek-r1:7b"]
    GENERATION_MODEL_NAME = GENERATION_MODEL_NAME_OPTIONS[0]
    GENERATION_API_KEY = os.getenv("GENERATION_API_KEY", "")
    GENERATION_TEMPERATURE = float(os.getenv("GENERATION_TEMPERATURE", "0.7"))

    # 向量存储配置
    CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
    FAISS_INDEX_PATH = os.getenv("FAISS_INDEX_PATH", "./faiss_index")

    # 搜索参数
    SEARCH_RESULTS_COUNT = int(os.getenv("SEARCH_RESULTS_COUNT", "10"))
    RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "5"))
    DETAIL_FETCH_TOP_K = int(os.getenv("DETAIL_FETCH_TOP_K", "3"))
    # RAG 分块与证据预算参数
    RAG_CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "800"))  # 文本分块大小，影响向量检索粒度
    RAG_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "160"))  # 分块重叠长度，降低语义切割损失
    EVIDENCE_SUMMARY_MAX_CHARS = int(os.getenv("EVIDENCE_SUMMARY_MAX_CHARS", "1200"))  # 摘要区总预算
    EVIDENCE_BODY_MAX_CHARS = int(os.getenv("EVIDENCE_BODY_MAX_CHARS", "2400"))  # 正文区总预算
    EVIDENCE_SUMMARY_TOP_K = int(os.getenv("EVIDENCE_SUMMARY_TOP_K", "5"))  # 摘要区最多保留条数
    EVIDENCE_BODY_TOP_N = int(os.getenv("EVIDENCE_BODY_TOP_N", "6"))  # 正文区最多保留段落数
    EVIDENCE_BODY_CANDIDATE_K = int(os.getenv("EVIDENCE_BODY_CANDIDATE_K", "10"))  # 正文候选检索数量
    EVIDENCE_SUMMARY_ITEM_MAX_CHARS = int(os.getenv("EVIDENCE_SUMMARY_ITEM_MAX_CHARS", "280"))  # 单条摘要最大长度
    EVIDENCE_CHUNK_MAX_CHARS = int(os.getenv("EVIDENCE_CHUNK_MAX_CHARS", "700"))  # 单段正文最大长度
    EVIDENCE_CHUNK_MIN_CHARS = int(os.getenv("EVIDENCE_CHUNK_MIN_CHARS", "80"))  # 单段正文最小长度

    # 质量过滤阈值
    RELEVANCE_THRESHOLD = float(os.getenv("RELEVANCE_THRESHOLD", "0.7"))
    AUTHORITY_THRESHOLD = float(os.getenv("AUTHORITY_THRESHOLD", "0.8"))

    # 工具缓存配置
    TOOL_CACHE_TTL_SECONDS = int(os.getenv("TOOL_CACHE_TTL_SECONDS", "300"))
    TOOL_CACHE_MAX_SIZE = int(os.getenv("TOOL_CACHE_MAX_SIZE", "512"))
    TOOL_CIRCUIT_FAILURE_THRESHOLD = int(os.getenv("TOOL_CIRCUIT_FAILURE_THRESHOLD", "3"))  # 工具熔断失败阈值
    TOOL_CIRCUIT_COOLDOWN_SECONDS = int(os.getenv("TOOL_CIRCUIT_COOLDOWN_SECONDS", "30"))  # 工具熔断冷却时间
    TOOL_CALL_MAX_DURATION_SECONDS = float(os.getenv("TOOL_CALL_MAX_DURATION_SECONDS", "0"))  # 工具单次执行时间上限

    # 搜索并发与熔断配置
    SEARCH_INSTANCE_CONCURRENCY = int(os.getenv("SEARCH_INSTANCE_CONCURRENCY", "3"))
    SEARCH_INSTANCE_TIMEOUT_SECONDS = int(os.getenv("SEARCH_INSTANCE_TIMEOUT_SECONDS", "10"))
    SEARCH_GLOBAL_TIMEOUT_SECONDS = int(os.getenv("SEARCH_GLOBAL_TIMEOUT_SECONDS", "12"))
    SEARCH_CIRCUIT_FAILURE_THRESHOLD = int(os.getenv("SEARCH_CIRCUIT_FAILURE_THRESHOLD", "3"))
    SEARCH_CIRCUIT_COOLDOWN_SECONDS = int(os.getenv("SEARCH_CIRCUIT_COOLDOWN_SECONDS", "30"))

    # 抓取并发与超时配置
    CRAWL_GLOBAL_CONCURRENCY = int(os.getenv("CRAWL_GLOBAL_CONCURRENCY", "10"))
    CRAWL_DOMAIN_CONCURRENCY = int(os.getenv("CRAWL_DOMAIN_CONCURRENCY", "2"))
    CRAWL_REQUEST_TIMEOUT_SECONDS = int(os.getenv("CRAWL_REQUEST_TIMEOUT_SECONDS", "10"))
    CRAWL_GLOBAL_TIMEOUT_SECONDS = int(os.getenv("CRAWL_GLOBAL_TIMEOUT_SECONDS", "15"))

    # Redis 配置
    REDIS_URL = os.getenv('REDIS_URL', '')
    REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.getenv('REDIS_PORT', '6379'))
    REDIS_DB = int(os.getenv('REDIS_DB', '0'))
    REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', None)

    # MySQL 配置
    MYSQL_HOST = os.getenv('MYSQL_HOST', 'localhost')
    MYSQL_PORT = int(os.getenv('MYSQL_PORT', '3306'))
    MYSQL_USER = os.getenv('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '')
    MYSQL_DATABASE = os.getenv('MYSQL_DATABASE', 'chat_context')
    MYSQL_SSL_CA = os.getenv('MYSQL_SSL_CA', '')

    # 认证数据库配置
    AUTH_DB_BACKEND = os.getenv('AUTH_DB_BACKEND', 'mysql' if ENVIRONMENT == 'production' else 'sqlite')

    # 业务配置
    TRIP_CONTEXT_STORAGE_TYPE = os.getenv('TRIP_CONTEXT_STORAGE_TYPE', 'test') # prod | test
    MAX_SHORT_TERM_MESSAGES = int(os.getenv('MAX_SHORT_TERM_MESSAGES', '10'))
    MAX_CONTEXT_TOKENS = int(os.getenv('MAX_CONTEXT_TOKENS', '4096'))
    SESSION_EXPIRY_HOURS = int(os.getenv('SESSION_EXPIRY_HOURS', '2'))
    CORE_ENTITIES_EXPIRY_HOURS = int(os.getenv('CORE_ENTITIES_EXPIRY_HOURS', '24'))
    AGENT_TIME_BUDGET_SECONDS = float(os.getenv("AGENT_TIME_BUDGET_SECONDS", "0"))  # Agent 执行时间预算
