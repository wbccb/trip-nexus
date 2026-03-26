from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests


TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "fbclid",
    "gclid",
    "spm",
    "share_token",
    "share_source",
}
SHORT_LINK_HOSTS = {"xhslink.com", "b23.tv", "t.cn", "dwz.cn"}


def infer_source_platform(source_url: str) -> str:
    # 平台识别结果会同时影响前端风险提示、后端 extractor selector 选择、以及 metadata 落库字段。
    hostname = str(urlparse(str(source_url or "")).hostname or "").lower()
    if "xiaohongshu.com" in hostname or "xhslink.com" in hostname:
        return "xiaohongshu"
    if "weibo.com" in hostname or "weibo.cn" in hostname or hostname.endswith("t.cn"):
        return "weibo"
    if "zhihu.com" in hostname or "zhuanlan.zhihu.com" in hostname:
        return "zhihu"
    if "bilibili.com" in hostname or "b23.tv" in hostname:
        return "bilibili"
    return "unknown"


def map_source_risk_level(source_platform: str) -> str:
    # 这里的风险等级不是安全风险，而是“自动解析失败概率”的经验分级，
    # 供前端决定是否优先引导用户走手动/OCR 导入。
    mapping = {
        "unknown": "low",
        "bilibili": "medium",
        "zhihu": "medium",
        "xiaohongshu": "high",
        "weibo": "high",
    }
    return mapping.get(str(source_platform or "unknown"), "low")


def normalize_url(raw_url: str) -> str:
    # 规范化的目标是得到“可比较”的 URL：
    # 统一 https、去掉 fragment、清洗常见追踪参数，减少同一来源被重复导入的概率。
    parsed = urlparse(str(raw_url or "").strip())
    scheme = "https"
    netloc = str(parsed.netloc or "").strip().lower()
    path = parsed.path or "/"
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    filtered_pairs = [(key, value) for key, value in query_pairs if str(key).lower() not in TRACKING_QUERY_KEYS]
    normalized_query = urlencode(filtered_pairs, doseq=True)
    return urlunparse((scheme, netloc, path, parsed.params, normalized_query, ""))


def resolve_short_url(normalized_url: str, timeout: int = 5) -> Dict[str, Optional[str]]:
    parsed = urlparse(str(normalized_url or "").strip())
    hostname = str(parsed.hostname or "").lower()
    if hostname not in SHORT_LINK_HOSTS:
        return {"resolved_url": normalized_url, "resolve_error_code": None}
    # v0.0.6 只做“合规的公开短链解跳”。
    # 能拿到最终 URL 就继续后续平台识别；拿不到时返回错误码给前端做提示，而不是做更激进的绕过。
    try:
        # 短链只需要知道最终落点，优先用 HEAD，避免在预处理阶段下载整页正文。
        response = requests.head(
            normalized_url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": "TripNexus/1.0"},
        )
        resolved_url = str(response.url or "").strip() or normalized_url
        return {"resolved_url": normalize_url(resolved_url), "resolve_error_code": None}
    except requests.TooManyRedirects:
        return {"resolved_url": None, "resolve_error_code": "URL_RESOLVE_LOOP"}
    except requests.Timeout:
        return {"resolved_url": None, "resolve_error_code": "URL_RESOLVE_TIMEOUT"}
    except Exception:
        return {"resolved_url": normalized_url, "resolve_error_code": None}


def preprocess_url(raw_url: str, timeout: int = 5) -> Dict[str, Any]:
    # 预处理链路只负责产出一套稳定的 URL 元信息，
    # 后续 preprocess 接口与 ingest 接口都基于这套字段继续工作。
    normalized_url = normalize_url(raw_url)
    resolve_payload = resolve_short_url(normalized_url, timeout=timeout)
    resolved_url = resolve_payload.get("resolved_url") or normalized_url
    source_platform = infer_source_platform(str(resolved_url))
    return {
        "normalized_url": normalized_url,
        "resolved_url": resolved_url,
        "source_platform": source_platform,
        "source_risk_level": map_source_risk_level(source_platform),
        "resolve_error_code": resolve_payload.get("resolve_error_code"),
    }
