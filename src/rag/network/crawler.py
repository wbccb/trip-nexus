import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

class ContentCrawler:
    def __init__(self, max_workers: int = 5, timeout: int = 10):
        self.max_workers = max_workers
        self.timeout = timeout
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'
        }

    def fetch_urls(self, urls: List[str]) -> List[Dict[str, str]]:
        """
        并发抓取多个URL的内容
        """
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url = {executor.submit(self._fetch_single, url): url for url in urls}
            for future in future_to_url:
                url = future_to_url[future]
                try:
                    content = future.result()
                    if content:
                        results.append(content)
                except Exception as e:
                    logger.error(f"Failed to fetch {url}: {e}")
        return results

    def _fetch_single(self, url: str) -> Optional[Dict[str, str]]:
        """
        抓取单个URL并解析
        """
        try:
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
            response.raise_for_status()
            
            # 检测编码
            if response.encoding == 'ISO-8859-1':
                response.encoding = response.apparent_encoding
                
            return self._parse_html(response.text, url)
        except Exception as e:
            logger.warning(f"Error fetching {url}: {e}")
            return None

    def _parse_html(self, html: str, url: str) -> Dict[str, str]:
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
