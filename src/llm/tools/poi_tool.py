from typing import Dict, Any, Optional
from src.rag.network.multi_source_search import MultiSourceSearcher


def search_poi(
    query: str,
    searcher: MultiSourceSearcher,
    city: Optional[str] = None,
    top_k: int = 5,
) -> Dict[str, Any]:
    search_query = f"{city} {query}".strip() if city else query
    intent_info = {"primary_intent": "travel", "needs_search": True}
    results = searcher.search(search_query, intent_info)
    return {"query": search_query, "results": results[:max(1, int(top_k))]}
