import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from src.config import Config
from src.observability import DomainConcurrencyLimiter, get_global_recorder

logger = logging.getLogger(__name__)

NOISE_TEXT_PATTERNS = [
    re.compile(r"^(登录|注册|打开app|下载app|分享|举报|收藏|点赞|评论|转发|展开全文|阅读全文|显示更多)$", re.IGNORECASE),
    re.compile(r"^(赞同|喜欢|收藏|评论)\s*\d*$", re.IGNORECASE),
]


class ContentCrawler:
    def __init__(self, max_workers: int = 5, timeout: int = 10):
        self.config = Config()
        self.max_workers = max_workers
        self.timeout = timeout
        self._limiter = DomainConcurrencyLimiter(
            max_global=self.config.CRAWL_GLOBAL_CONCURRENCY,
            max_per_domain=self.config.CRAWL_DOMAIN_CONCURRENCY,
        )
        self._metrics = get_global_recorder()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"
        }

    def fetch_urls(self, urls: List[str]) -> List[Dict[str, Any]]:
        start_ts = time.time()
        results: List[Dict[str, Any]] = []
        if not urls:
            return results
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url = {}
            for url in urls:
                future = executor.submit(self._fetch_single_with_limit, url)
                future_to_url[future] = url
            for future in as_completed(future_to_url):
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
        elapsed_ms = int((time.time() - start_ts) * 1000)
        self._metrics.record("crawl_complete", {"elapsed_ms": elapsed_ms, "results": len(results)})
        return results

    def fetch_url_with_fallback(self, url: str, source_platform: str = "unknown") -> Optional[Dict[str, Any]]:
        # 自动解析按 L1 -> L2 -> L3 逐层退化：
        # L1 通用 HTML 提取成本最低；L2 用平台 selector 提升命中率；L3 浏览器兜底处理动态页。
        # 返回时会把命中的 extractor_layer 带回去，供预处理提示与调试面板展示。
        if not self._limiter.acquire(url, timeout=self.config.CRAWL_REQUEST_TIMEOUT_SECONDS):
            return None
        try:
            l1_payload = self._fetch_single(url)
            if l1_payload and len(str(l1_payload.get("content") or "").strip()) >= 80:
                l1_payload["extractor_layer"] = "l1_html"
                return l1_payload
            l2_payload = self._fetch_platform_l2(url, source_platform)
            if l2_payload and len(str(l2_payload.get("content") or "").strip()) >= 80:
                l2_payload["extractor_layer"] = "l2_platform"
                return l2_payload
            l3_payload = self._fetch_browser_l3(url)
            if l3_payload and str(l3_payload.get("content") or "").strip():
                l3_payload["extractor_layer"] = "l3_browser"
                return l3_payload
            return l1_payload or l2_payload or l3_payload
        finally:
            self._limiter.release(url)

    def _fetch_single_with_limit(self, url: str) -> Optional[Dict[str, Any]]:
        if not self._limiter.acquire(url, timeout=self.config.CRAWL_REQUEST_TIMEOUT_SECONDS):
            self._metrics.record("crawl_limited", {"url": url})
            return None
        try:
            return self._fetch_single(url)
        finally:
            self._limiter.release(url)

    def _fetch_single(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            response = requests.get(url, headers=self.headers, timeout=self.config.CRAWL_REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            if response.encoding == "ISO-8859-1":
                response.encoding = response.apparent_encoding
            # L1 是最低成本的通用正文提取，适合博客、攻略站、媒体站等开放网页。
            return self._parse_html(response.text, url)
        except Exception as e:
            logger.warning(f"Error fetching {url}: {e}")
            return None

    def _fetch_platform_l2(self, url: str, source_platform: str) -> Optional[Dict[str, Any]]:
        try:
            response = requests.get(url, headers=self.headers, timeout=self.config.CRAWL_REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            if response.encoding == "ISO-8859-1":
                response.encoding = response.apparent_encoding
            # L2 仍基于 requests + HTML，只是在已知平台上启用更激进的 selector 覆盖，
            # 目标是提高知乎/B站/小红书/微博公开页首屏正文的命中率。
            return self._parse_html_platform(response.text, url, source_platform)
        except Exception as e:
            logger.warning(f"Error in l2 extractor {url}: {e}")
            return None

    def _fetch_browser_l3(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=self.config.CRAWL_REQUEST_TIMEOUT_SECONDS * 1000)
                # 很多社交站点正文默认折叠在“展开全文/显示更多”之后，
                # 这里先做轻量滚动和常见按钮点击，再回落到统一 HTML 解析。
                self._expand_page_content(page)
                html = page.content()
                browser.close()
            parsed = self._parse_html(html, url)
            if parsed:
                parsed["extractor_layer"] = "l3_browser"
            return parsed
        except Exception as e:
            logger.warning(f"Error in l3 browser extractor {url}: {e}")
            return None

    def _parse_html(self, html: str, url: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        # 先尽量移除导航、脚本、表单等明显噪声节点，避免正文抽取被页面骨架污染。
        for element in soup(["script", "style", "nav", "footer", "header", "iframe", "noscript", "svg", "form", "aside"]):
            element.decompose()
        title = self._extract_title(soup)
        description = self._extract_description(soup)
        author = self._extract_author(soup)
        published_at = self._extract_published_at(soup)
        tags = self._extract_tags(soup)
        lines = self._collect_candidate_lines(
            soup,
            [
                "article p",
                "article li",
                "article blockquote",
                "main p",
                "main li",
                "main blockquote",
                "[role='main'] p",
                "[role='main'] li",
                ".article-content p",
                ".article-content li",
                ".entry-content p",
                ".entry-content li",
                ".post-content p",
                ".post-content li",
                ".rich_media_content p",
                ".rich_media_content li",
            ],
        )
        if not lines:
            # 如果没命中常见正文容器，再退回通用段落抽取，保证未知站点也至少能拿到一份 best effort 文本。
            lines = self._collect_candidate_lines(
                soup,
                ["p", "h1", "h2", "h3", "h4", "h5", "li", "blockquote"],
            )
        content = "\n".join(lines)
        return {
            "url": url,
            "title": title,
            "description": description,
            "author": author,
            "published_at": published_at,
            "tags": tags,
            "content": content,
            "raw_html": html[:1000],
            "extractor_layer": "l1_html",
        }

    def _parse_html_platform(self, html: str, url: str, source_platform: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "header", "iframe", "noscript", "svg", "form", "aside"]):
            element.decompose()
        selectors = {
            "zhihu": [
                "article p",
                "article li",
                ".RichText p",
                ".RichText li",
                ".Post-RichText p",
                ".Post-RichText li",
                ".RichContent-inner p",
                ".RichContent-inner li",
                ".QuestionRichText p",
                ".QuestionRichText li",
                ".ContentItem AnswerItem p",
                ".ContentItem AnswerItem li",
            ],
            "bilibili": [
                ".desc-info-text",
                ".desc-info-text p",
                "#v_desc p",
                ".video-desc p",
                ".video-desc span",
                ".opus-module-content p",
                ".opus-module-content span",
                ".article-content p",
                ".article-content li",
            ],
            "xiaohongshu": [
                ".note-content p",
                ".note-content span",
                ".content p",
                ".content span",
                ".desc p",
                ".desc span",
                ".note-scroller p",
                ".note-scroller span",
                "article p",
                "article li",
            ],
            "weibo": [
                ".detail_wbtext_4CRf9",
                ".detail_wbtext_4CRf9 p",
                ".wbpro-feed-content p",
                ".wbpro-feed-content span",
                ".vue-recycle-scroller__item-wrapper p",
                ".vue-recycle-scroller__item-wrapper span",
                ".Feed_body_3R0rO p",
                ".Feed_body_3R0rO span",
            ],
        }
        # L2 的目标不是完全替代 L1，而是在已知平台上扩大正文 selector 覆盖，
        # 解决知乎/微博/小红书/B站这类站点 DOM 结构差异较大的问题。
        title = self._extract_title(soup)
        description = self._extract_description(soup)
        author = self._extract_author(soup)
        published_at = self._extract_published_at(soup)
        tags = self._extract_tags(soup)
        candidate_lines = self._collect_candidate_lines(soup, selectors.get(str(source_platform or "unknown"), []))
        if not candidate_lines:
            candidate_lines = self._collect_candidate_lines(
                soup,
                ["article p", "article li", "main p", "main li", "p", "h1", "h2", "h3", "li", "blockquote"],
            )
        content = "\n".join(candidate_lines[:120])
        return {
            "url": url,
            "title": title,
            "description": description,
            "author": author,
            "published_at": published_at,
            "tags": tags,
            "content": content,
            "raw_html": html[:1000],
            "extractor_layer": "l2_platform",
        }

    def _expand_page_content(self, page: Any) -> None:
        try:
            page.wait_for_timeout(300)
            for ratio in (0.35, 0.7, 1.0):
                page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {ratio});")
                page.wait_for_timeout(250)
            for selector in [
                "text=展开全文",
                "text=展开更多",
                "text=阅读全文",
                "text=显示更多",
                "text=更多",
                "text=read more",
                "text=show more",
            ]:
                try:
                    locator = page.locator(selector).first
                    if locator.count() > 0:
                        locator.click(timeout=600)
                        page.wait_for_timeout(250)
                except Exception:
                    continue
        except Exception:
            return

    def _extract_title(self, soup: BeautifulSoup) -> str:
        return (
            self._extract_meta_content(soup, "property", ["og:title"])
            or self._extract_meta_content(soup, "name", ["twitter:title"])
            or (soup.title.string.strip() if soup.title and soup.title.string else "")
            or self._extract_first_text(soup, ["h1", "article h1", "main h1"])
        )

    def _extract_description(self, soup: BeautifulSoup) -> str:
        return (
            self._extract_meta_content(soup, "property", ["og:description"])
            or self._extract_meta_content(soup, "name", ["description", "twitter:description"])
        )

    def _extract_author(self, soup: BeautifulSoup) -> str:
        return (
            self._extract_meta_content(soup, "name", ["author"])
            or self._extract_meta_content(soup, "property", ["article:author"])
            or self._extract_first_text(
                soup,
                [".author", ".AuthorInfo-name", "[rel='author']", ".up-name", ".user-name", ".note-user-nickname"],
            )
        )

    def _extract_published_at(self, soup: BeautifulSoup) -> str:
        return (
            self._extract_meta_content(soup, "property", ["article:published_time"])
            or self._extract_meta_content(soup, "name", ["pubdate", "publishdate", "date"])
            or self._extract_first_text(soup, ["time", ".time", ".publish-time", ".ContentItem-time"])
        )

    def _extract_tags(self, soup: BeautifulSoup) -> List[str]:
        values: List[str] = []
        for selector in [".tag", ".topic", ".TopicLink", "[rel='tag']", ".note-tag", ".video-tag"]:
            for node in soup.select(selector):
                text = self._normalize_text(node.get_text(" ", strip=True))
                if text and text not in values:
                    values.append(text)
        return values[:10]

    def _extract_meta_content(self, soup: BeautifulSoup, attr_name: str, attr_values: List[str]) -> str:
        for attr_value in attr_values:
            node = soup.find("meta", attrs={attr_name: attr_value})
            content = self._normalize_text(node.get("content") if node else "")
            if content:
                return content
        return ""

    def _extract_first_text(self, soup: BeautifulSoup, selectors: List[str]) -> str:
        for selector in selectors:
            try:
                node = soup.select_one(selector)
            except Exception:
                node = None
            if not node:
                continue
            text = self._normalize_text(node.get_text(" ", strip=True))
            if text:
                return text
        return ""

    def _collect_candidate_lines(self, soup: BeautifulSoup, selectors: List[str]) -> List[str]:
        candidate_lines: List[str] = []
        seen = set()
        for selector in selectors:
            try:
                nodes = soup.select(selector)
            except Exception:
                continue
            for node in nodes:
                text = self._normalize_text(node.get_text(" ", strip=True))
                if not self._is_valid_candidate_text(text):
                    continue
                # 不同 selector 经常会命中同一块正文，按归一化文本去重可以显著减少重复段落。
                dedupe_key = re.sub(r"\s+", "", text).lower()
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                candidate_lines.append(text)
        return candidate_lines

    def _normalize_text(self, text: Any) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip()

    def _is_valid_candidate_text(self, text: str) -> bool:
        # 这里只做轻量过滤，避免把“展开全文/点赞/评论数”之类 UI 文案当正文。
        # 更严格的质量判断交给 content_validator，保持抓取层和质量门禁职责分离。
        if len(text) < 18:
            return False
        for pattern in NOISE_TEXT_PATTERNS:
            if pattern.search(text):
                return False
        return True
