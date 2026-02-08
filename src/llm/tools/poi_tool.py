from typing import Dict, Any, Optional
from src.rag.network.multi_source_search import MultiSourceSearcher


def search_poi(
    query: str,
    searcher: MultiSourceSearcher,
    city: Optional[str] = None,
    top_k: int = 5,
) -> Dict[str, Any]:
    print("\n 调用 search_poi 工具，查询：", query)
    search_query = f"{city} {query}".strip() if city else query
    intent_info = {"primary_intent": "travel", "needs_search": True}
    results = searcher.search(search_query, intent_info)
    print("searcher.search 拿到", len(results), "个结果 \n")
    return {"query": search_query, "results": results[:max(1, int(top_k))]}
