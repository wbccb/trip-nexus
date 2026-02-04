import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Any
import logging
import time
from src.config import Config
from src.observability import DomainConcurrencyLimiter, MetricsRecorder, ErrorCodes, build_error_payload, normalize_exception, get_global_recorder

logger = logging.getLogger(__name__)

class ContentCrawler:
    def __init__(self, max_workers: int = 5, timeout: int = 10):
        # 初始化配置
        self.config = Config()
        # 初始化并发与超时参数
        self.max_workers = max_workers
        self.timeout = timeout
        # 初始化并发限制器
        self._limiter = DomainConcurrencyLimiter(
            max_global=self.config.CRAWL_GLOBAL_CONCURRENCY,
            max_per_domain=self.config.CRAWL_DOMAIN_CONCURRENCY,
        )
        # 初始化指标记录器
        self._metrics = get_global_recorder()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'
        }

    def fetch_urls(self, urls: List[str]) -> List[Dict[str, Any]]:
        """
        并发抓取多个URL的内容
        """
        # 记录抓取开始时间
        start_ts = time.time()
        # 初始化结果列表
        results: List[Dict[str, Any]] = []
        # 空列表时直接返回
        if not urls:
            return results
        # 建立线程池执行抓取任务
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url = {}
            for url in urls:
                future = executor.submit(self._fetch_single_with_limit, url)
                future_to_url[future] = url
            # 按完成顺序处理结果
            for future in as_completed(future_to_url):
                # 全局超时检查
                if (time.time() - start_ts) > self.config.CRAWL_GLOBAL_TIMEOUT_SECONDS:
                    logger.warning("Crawl global timeout reached, returning partial results")
                    self._metrics.record("crawl_timeout", {"scope": "global"})
                    break
                url = future_to_url[future]
                try:
                    content = future.result(timeout=self.config.CRAWL_REQUEST_TIMEOUT_SECONDS)
                    if content:
                        results.append(content)
                        self._metrics.record("crawl_success", {"url": url})
                except Exception as e:
                    logger.error(f"Failed to fetch {url}: {e}")
                    self._metrics.record("crawl_failed", {"url": url, "error": str(e)})
        # 记录抓取完成指标
        elapsed_ms = int((time.time() - start_ts) * 1000)
        self._metrics.record("crawl_complete", {"elapsed_ms": elapsed_ms, "results": len(results)})
        # 返回抓取结果
        return results

    def _fetch_single_with_limit(self, url: str) -> Optional[Dict[str, Any]]:
        # 获取并发许可，失败则直接返回
        if not self._limiter.acquire(url, timeout=self.config.CRAWL_REQUEST_TIMEOUT_SECONDS):
            self._metrics.record("crawl_limited", {"url": url})
            return None
        try:
            # 执行单次抓取
            return self._fetch_single(url)
        finally:
            # 释放并发许可
            self._limiter.release(url)

    def _fetch_single(self, url: str) -> Optional[Dict[str, Any]]:
        """
        抓取单个URL并解析
        """
        try:
            response = requests.get(url, headers=self.headers, timeout=self.config.CRAWL_REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            
            # 检测编码
            if response.encoding == 'ISO-8859-1':
                response.encoding = response.apparent_encoding
                
            return self._parse_html(response.text, url)
        except Exception as e:
            logger.warning(f"Error fetching {url}: {e}")
            return None

    def _parse_html(self, html: str, url: str) -> Dict[str, Any]:
        """
        使用BeautifulSoup解析HTML，提取主要文本
        """
        soup = BeautifulSoup(html, 'html.parser')
        
        # 移除无关标签
        for element in soup(['script', 'style', 'nav', 'footer', 'header', 'iframe', 'noscript']):
            element.decompose()

        # 提取标题
        title = soup.title.string.strip() if soup.title else ""
        
        # 提取正文
        # 这里做一个简单的提取，获取所有段落文本
        # 可以根据需要增强，例如识别主内容区
        lines = []
        for p in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'li']):
            text = p.get_text().strip()
            if len(text) > 10: # 过滤太短的行
                lines.append(text)
        
        content = "\n".join(lines)
        
        return {
            "url": url,
            "title": title,
            "content": content,
            "raw_html": html[:1000] # 只保留部分原始HTML用于调试，避免过大
        }
