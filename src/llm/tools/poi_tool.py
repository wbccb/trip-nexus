from typing import Dict, Any, Optional
from src.rag.network.multi_source_search import MultiSourceSearcher
import logging
from src.observability import log_event

logger = logging.getLogger(__name__)


def search_poi(
    query: str,
    searcher: Any,
    city: Optional[str] = None,
    top_k: int = 5,
    defer_answer: bool = False,
) -> Dict[str, Any]:
    search_query = f"{city} {query}".strip() if city else query
    log_event(logger, logging.INFO, "POI 搜索开始", {"查询": search_query, "TopK": top_k, "延迟回答": defer_answer})
    
    # 兼容 AIRetrievalPipeline (RAG)
    if hasattr(searcher, 'run'):
        log_event(logger, logging.INFO, "POI 搜索走 RAG Pipeline", {"查询": search_query})
        # 构造默认意图，避免 Pipeline 内部重复识别
        # POI 搜索肯定是 travel 且需要搜索
        intent_info = {
            "primary_intent": "travel",
            "needs_search": True,
            "confidence": 1.0,
            "source": "agent_tool_call"
        }
        rag_result = searcher.run(search_query, intent_info=intent_info, generate_answer=not defer_answer)
        
        # 提取搜索结果，优先使用 filtered_results (质量更高)，其次 search_results
        # 注意：Agent 可能期望 results 是 list[dict]，包含 title, url, content_snippet 等字段
        results = rag_result.get('filtered_results') or rag_result.get('search_results') or []
        evidence = rag_result.get('evidence')
        rag_answer = rag_result.get('answer')
        
        # 日志已经在 rag_main.py 中打印
    else:
        # MultiSourceSearcher
        log_event(logger, logging.INFO, "POI 搜索走 MultiSourceSearcher", {"查询": search_query})
        intent_info = {"primary_intent": "travel", "needs_search": True}
        results = searcher.search(search_query, intent_info)
        evidence = None
        rag_answer = None
        
    result = {
        "query": search_query,
        "results": results[:max(1, int(top_k))],
        "evidence": evidence,
        "rag_answer": rag_answer
    }
    log_event(logger, logging.INFO, "POI 搜索完成", {"查询": search_query, "结果数": len(result.get("results") or [])})
    return result
