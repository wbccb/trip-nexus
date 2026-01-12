from src.config import Config
from src.rag.module.intent_recognition import IntentRecognizer
import requests
from typing import List, Dict
import logging
from bs4 import BeautifulSoup
from urllib.parse import unquote

logger = logging.getLogger(__name__)

class MultiSourceSearcher:
    def __init__(self, llm):
        self.config = Config()
        self.intent_recognizer = IntentRecognizer(llm)
        # 备用公共实例列表，当配置的实例不可用时尝试
        self.fallback_urls = [
            "https://searx.be",
            "https://search.ononoki.org",
            "https://searx.work",
            "https://search.rhscz.eu"
        ]

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

    def _search_duckduckgo_fallback(self, query: str) -> List[Dict[str, any]]:
        """
        作为最后的备选，直接抓取 DuckDuckGo HTML 版
        """
        url = "https://html.duckduckgo.com/html/"
        data = {'q': query}
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://html.duckduckgo.com/'
        }
        
        try:
            logger.info("Fallback to DuckDuckGo HTML search...")
            response = requests.post(url, data=data, headers=headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            results = []
            
            for result in soup.select('.result'):
                # 排除广告
                if 'result--ad' in result.get('class', []):
                    continue
                    
                title_elem = result.select_one('.result__title a')
                snippet_elem = result.select_one('.result__snippet')
                
                if title_elem:
                    link = title_elem.get('href', '')
                    # DuckDuckGo 的链接通常是 /l/?uddg=...
                    if '/l/?uddg=' in link:
                        try:
                            link = unquote(link.split('/l/?uddg=')[1].split('&')[0])
                        except:
                            pass
                    
                    results.append({
                        'title': title_elem.get_text(strip=True),
                        'url': link,
                        'content_snippet': snippet_elem.get_text(strip=True) if snippet_elem else "",
                        'source': 'duckduckgo_html',
                        'score': 0.4, # 备选源分数稍低
                        'type': 'web'
                    })
                    
                    if len(results) >= 5:
                        break
            
            logger.info(f"DuckDuckGo fallback retrieved {len(results)} results")
            return results
            
        except Exception as e:
            logger.error(f"DuckDuckGo fallback failed: {e}")
            return []

    def _search_searxng(self, query: str, intent_info: Dict[str, any]) -> List[Dict[str, any]]:
        """
        使用SearXNG进行基础搜索，支持故障转移
        """
        params = {
            "q": query,
            "format": "json",
            'language': 'zh',
            'time_range': 'month' if intent_info.get("primary_intent") == "current_events" else '',
            'engines': self._get_engines_by_intent(intent_info.get("primary_intent", "general_knowledge"))
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        # 构建尝试列表：配置的URL + 备用URL列表
        candidate_urls = []
        
        # 1. 添加配置的URL (处理可能的格式问题)
        config_url = self.config.SEARXNG_URL.strip()
        if "localhost" in config_url or "127.0.0.1" in config_url:
            # 如果配置是本地地址，且连接失败，我们希望能 fallback 到远程
            pass 
        candidate_urls.append(config_url)
        
        # 2. 添加备用URL
        for fallback in self.fallback_urls:
            # 避免重复
            if fallback not in config_url: 
                candidate_urls.append(fallback)
        
        # 3. 添加更多备用URL
        additional_fallbacks = [
            "https://searx.prvcy.eu",
            "https://search.bus-hit.me",
            "https://paulgo.io"
        ]
        for fallback in additional_fallbacks:
            if fallback not in candidate_urls:
                candidate_urls.append(fallback)

        for base_url in candidate_urls:
            try:
                # 构造完整URL
                if not base_url.endswith("/search"):
                    url = f"{base_url}/search"
                else:
                    url = base_url

                logger.info(f"Trying SearXNG instance: {base_url}")
                # 某些实例需要Cookie或Referer，这里简单模拟
                response = requests.get(url, params=params, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        search_results = data.get("results", [])
                        
                        if not search_results:
                            logger.warning(f"No results from {base_url}, trying next...")
                            continue # 结果为空可能也是实例问题，尝试下一个
                            
                        processed_results = []
                        for result in search_results:
                            processed_results.append({
                                'title': result.get('title', ''),
                                'url': result.get('url', ''),
                                'content_snippet': result.get('content', ''),
                                'source': result.get('engine', 'unknown'),
                                'score': result.get('score', 0.5) or 0.5,
                                'timestamp': result.get('publishedDate', ''),
                                'type': 'web'
                            })
                        
                        logger.info(f"Successfully retrieved {len(processed_results)} results from {base_url}")
                        return processed_results
                    except ValueError: # JSONDecodeError
                         logger.warning(f"SearXNG {base_url} returned invalid JSON")
                         continue
                else:
                    logger.warning(f"SearXNG {base_url} returned status {response.status_code}")
            
            except requests.exceptions.RequestException as e:
                logger.warning(f"SearXNG request failed for {base_url}: {e}")
                continue
            except Exception as e:
                logger.error(f"Error processing results from {base_url}: {e}")
                continue
        
        logger.error("All SearXNG instances failed. Trying DuckDuckGo fallback...")
        return self._search_duckduckgo_fallback(query)
