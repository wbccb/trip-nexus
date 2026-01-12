import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # SearXNG配置
    SEARXNG_URL = os.getenv("SEARXNG_URL", "https://search.rhscz.eu/")

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

    # 质量过滤阈值
    RELEVANCE_THRESHOLD = float(os.getenv("RELEVANCE_THRESHOLD", "0.7"))
    AUTHORITY_THRESHOLD = float(os.getenv("AUTHORITY_THRESHOLD", "0.8"))

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