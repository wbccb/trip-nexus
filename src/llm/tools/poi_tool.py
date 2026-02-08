from typing import Dict, Any, Optional
from src.rag.network.multi_source_search import MultiSourceSearcher


def search_poi(
    query: str,
    searcher: Any,
    city: Optional[str] = None,
    top_k: int = 5,
) -> Dict[str, Any]:
    print("\n 调用 search_poi 工具，查询：", query)
    search_query = f"{city} {query}".strip() if city else query
    
    # 兼容 AIRetrievalPipeline (RAG)
    if hasattr(searcher, 'run'):
        print(f"【POI】使用 RAG Pipeline 进行搜索 (query={search_query})")
        # 构造默认意图，避免 Pipeline 内部重复识别
        # POI 搜索肯定是 travel 且需要搜索
        intent_info = {
            "primary_intent": "travel",
            "needs_search": True,
            "confidence": 1.0,
            "source": "agent_tool_call"
        }
        rag_result = searcher.run(search_query, intent_info=intent_info)
        
        # 提取搜索结果，优先使用 filtered_results (质量更高)，其次 search_results
        # 注意：Agent 可能期望 results 是 list[dict]，包含 title, url, content_snippet 等字段
        results = rag_result.get('filtered_results') or rag_result.get('search_results') or []
        evidence = rag_result.get('evidence')
        
        # 日志已经在 rag_main.py 中打印
    else:
        # MultiSourceSearcher
        print(f"【POI】使用 MultiSourceSearcher 进行搜索 (query={search_query})")
        intent_info = {"primary_intent": "travel", "needs_search": True}
        results = searcher.search(search_query, intent_info)
        evidence = None
        
    print(f"【POI】搜索完成，拿到 {len(results)} 个结果 \n")
    return {"query": search_query, "results": results[:max(1, int(top_k))], "evidence": evidence}
