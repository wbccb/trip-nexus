import os
from dotenv import load_dotenv

load_dotenv()

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

class Config:
    # SearXNG配置
    SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

    # Embedding 模型配置
    SENTENCE_BERT_MODEL = os.getenv("SENTENCE_BERT_MODEL", "all-MiniLM-L6-v2")
    MINILM_MODEL = os.getenv("MINILM_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

    # LLM 配置
    LLM_MODEL = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

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

    # 业务配置
    MAX_SHORT_TERM_MESSAGES = int(os.getenv('MAX_SHORT_TERM_MESSAGES', '10'))
    MAX_CONTEXT_TOKENS = int(os.getenv('MAX_CONTEXT_TOKENS', '4096'))
    SESSION_EXPIRY_HOURS = int(os.getenv('SESSION_EXPIRY_HOURS', '2'))
    CORE_ENTITIES_EXPIRY_HOURS = int(os.getenv('CORE_ENTITIES_EXPIRY_HOURS', '24'))
