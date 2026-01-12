from src.config import Config
from src.rag.module.intent_recognition import IntentRecognizer
import requests
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

class MultiSourceSearcher:
    def __init__(self, llm):
        self.config = Config()
        self.intent_recognizer = IntentRecognizer(llm)

    def _get_engines_by_intent(self, intent: str) -> str:
        """
        根据意图类型选择合适的搜索引擎
        """
        engine_mapping = {
            "general_knowledge": "google,wikipedia",
            "current_events": "google,bing,news",
            "shopping": "google,bing,amazon",
            "travel": "google,bing,tripadvisor",
            "no_search_needed": ""
        }
        return engine_mapping.get(intent, "google,bing")

    def search(self, query: str, intent_info: Dict[str, any]) -> List[Dict[str, any]]:
        """
        根据意图选择不同的搜索策略进行搜索
        """
        results = []

        # 1. SearXNG基础搜索
        searxng_results = self._search_searxng(query, intent_info)
        results.extend(searxng_results)

        # 2. 根据意图类型添加特定来源 (Reserved for future expansion)

        sorted_results = sorted(results, key=lambda x: x['score'], reverse=True)

        return sorted_results[:self.config.SEARCH_RESULTS_COUNT]

    def _search_searxng(self, query: str, intent_info: Dict[str, any]) -> List[Dict[str, any]]:
        """
        使用SearXNG进行基础搜索
        """
        params = {
            "q": query,
            "format": "json",
            'language': 'zh',
            'time_range': 'month' if intent_info.get("primary_intent") == "current_events" else '',
            'engines': self._get_engines_by_intent(intent_info.get("primary_intent", "general_knowledge"))
        }

        try:
            # 去除末尾可能多余的空格
            searxng_url = self.config.SEARXNG_URL.strip()
            if not searxng_url.endswith("/search"):
                # 简单拼接，Config中通常是host:port
                 pass # requests will handle path if params are passed? No, SearXNG needs /search endpoint.
            
            # 确保URL正确 (Config默认是 http://localhost:8080)
            # 如果Config里没有/search，这里加上
            url = searxng_url if searxng_url.endswith("/search") else f"{searxng_url}/search"

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            search_results = data.get("results", [])
            processed_results = []

            for result in search_results:
                processed_results.append({
                    'title': result.get('title', ''),
                    'url': result.get('url', ''),
                    'content_snippet': result.get('content', ''),
                    'source': result.get('engine', 'unknown'),
                    'score': result.get('score', 0.5) or 0.5, # 防止None
                    'timestamp': result.get('publishedDate', ''),
                    'type': 'web'
                })

            return processed_results

        except requests.exceptions.RequestException as e:
            logger.error(f"SearXNG request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Error processing SearXNG results: {e}")
            return []
