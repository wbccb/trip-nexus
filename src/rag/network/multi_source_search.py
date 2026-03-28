from src.config import Config
from src.rag.module.intent_recognition import IntentRecognizer
import requests
from typing import List, Dict, Any, Optional
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from urllib.parse import unquote
from src.observability import CircuitBreaker, MetricsRecorder, ErrorCodes, build_error_payload, normalize_exception, get_global_recorder

logger = logging.getLogger(__name__)

class MultiSourceSearcher:
    def __init__(self, llm):
        # 初始化配置参数
        self.config = Config()
        # 初始化意图识别器
        self.intent_recognizer = IntentRecognizer(llm)
        # 初始化熔断器，用于避免持续访问异常实例
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=self.config.SEARCH_CIRCUIT_FAILURE_THRESHOLD,
            cooldown_seconds=self.config.SEARCH_CIRCUIT_COOLDOWN_SECONDS,
        )
        # 初始化指标记录器
        self._metrics = get_global_recorder()
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
        # 记录搜索开始时间
        start_ts = time.time()
        # 初始化结果列表
        results: List[Dict[str, Any]] = []

        # 1. SearXNG基础搜索
        searxng_results = self._search_searxng(query, intent_info)
        results.extend(searxng_results)

        # 2. 根据意图类型添加特定来源 (Reserved for future expansion)

        # 记录搜索完成耗时
        elapsed_ms = int((time.time() - start_ts) * 1000)
        self._metrics.record("search_complete", {"elapsed_ms": elapsed_ms, "results": len(results)})
        sorted_results = sorted(results, key=lambda x: x['score'], reverse=True)

        return sorted_results[:self.config.SEARCH_RESULTS_COUNT]

    def _search_duckduckgo_fallback(self, query: str) -> List[Dict[str, Any]]:
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
            response = requests.post(url, data=data, headers=headers, timeout=self.config.SEARCH_INSTANCE_TIMEOUT_SECONDS)
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
            
            logger.debug(f"DuckDuckGo fallback 拿到 {len(results)} results")
            # 记录 fallback 成功指标
            self._metrics.record("search_fallback_success", {"engine": "duckduckgo_html", "results": len(results)})
            return results
            
        except Exception as e:
            logger.error(f"DuckDuckGo fallback failed: {e}")
            # 记录 fallback 失败指标
            self._metrics.record("search_fallback_failed", {"engine": "duckduckgo_html", "error": str(e)})
            return []

    def _search_searxng(self, query: str, intent_info: Dict[str, Any]) -> List[Dict[str, Any]]:
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

        # 并发尝试候选实例，先成功先返回
        return self._search_searxng_concurrent(candidate_urls, params, headers)
        
    def _search_searxng_concurrent(
        self,
        candidate_urls: List[str],
        params: Dict[str, Any],
        headers: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        # 记录开始时间，用于全局超时控制
        start_ts = time.time()
        # 过滤掉熔断中的实例
        filtered_urls = [u for u in candidate_urls if self._circuit_breaker.allow(u)]
        # 若全部熔断则直接进入 fallback
        if not filtered_urls:
            logger.debug("All SearXNG instances are circuit-open. Trying DuckDuckGo fallback...")
            return self._search_duckduckgo_fallback(params.get("q") or "")
        # 限制并发数量
        concurrency = max(1, int(self.config.SEARCH_INSTANCE_CONCURRENCY))
        # 逐个提交并发任务
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_url = {}
            for base_url in filtered_urls:
                future = executor.submit(self._fetch_searxng, base_url, params, headers)
                future_to_url[future] = base_url
            # 按完成顺序处理
            for future in as_completed(future_to_url):
                base_url = future_to_url[future]
                # 检查全局超时
                if (time.time() - start_ts) > self.config.SEARCH_GLOBAL_TIMEOUT_SECONDS:
                    logger.debug("SearXNG global timeout reached, aborting remaining instances")
                    self._metrics.record("search_timeout", {"scope": "global"})
                    break
                try:
                    results = future.result(timeout=self.config.SEARCH_INSTANCE_TIMEOUT_SECONDS)
                    # 成功拿到结果则立即返回
                    if results:
                        return results
                except Exception as e:
                    logger.debug(f"SearXNG instance failed: {base_url}, error={e}")
                    self._circuit_breaker.record_failure(base_url)
                    self._metrics.record("search_instance_failed", {"base_url": base_url, "error": str(e)})
                    continue
        logger.debug("All SearXNG instances failed. Trying DuckDuckGo fallback...")
        return self._search_duckduckgo_fallback(params.get("q") or "")

    def _fetch_searxng(
        self,
        base_url: str,
        params: Dict[str, Any],
        headers: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        # 构造完整URL
        if not base_url.endswith("/search"):
            url = f"{base_url}/search"
        else:
            url = base_url
        # 记录实例尝试日志
        logger.debug(f"Trying SearXNG instance: {base_url}")
        # 发起请求，使用实例超时配置
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=self.config.SEARCH_INSTANCE_TIMEOUT_SECONDS,
        )
        # 非 200 直接返回失败
        if response.status_code != 200:
            logger.debug(f"SearXNG {base_url} returned status {response.status_code}")
            self._circuit_breaker.record_failure(base_url)
            self._metrics.record("search_instance_failed", {"base_url": base_url, "status": response.status_code})
            return []
        try:
            data = response.json()
        except ValueError as e:
            logger.debug(f"SearXNG {base_url} returned invalid JSON")
            self._circuit_breaker.record_failure(base_url)
            self._metrics.record("search_instance_failed", {"base_url": base_url, "error": "invalid_json"})
            return []
        search_results = data.get("results", [])
        if not search_results:
            logger.debug(f"No results from {base_url}, trying next...")
            self._circuit_breaker.record_failure(base_url)
            self._metrics.record("search_instance_failed", {"base_url": base_url, "error": "empty_results"})
            return []
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
        # 记录实例成功指标
        self._circuit_breaker.record_success(base_url)
        self._metrics.record("search_instance_success", {"base_url": base_url, "results": len(processed_results)})
        logger.debug(f"Successfully retrieved {len(processed_results)} results from {base_url}")
        return processed_results
