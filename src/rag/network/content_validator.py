import re
from collections import Counter
from typing import Any, Dict, List


NOISE_PATTERNS = [
    (re.compile(r"下载\s*app|打开\s*app|app内查看", re.IGNORECASE), "AUTO_PARSE_BLOCKED"),
    (re.compile(r"登录后查看|请先登录|登录即可", re.IGNORECASE), "AUTO_PARSE_LOGIN_REQUIRED"),
    (re.compile(r"验证码|人机验证|安全验证", re.IGNORECASE), "AUTO_PARSE_RISK_VERIFICATION"),
    (re.compile(r"会员专享|付费阅读|购买后查看|解锁全文", re.IGNORECASE), "AUTO_PARSE_PAYWALLED"),
]


def _split_paragraphs(content_text: str) -> List[str]:
    return [item.strip() for item in re.split(r"\n+", str(content_text or "")) if item.strip()]


def _calc_text_density(content_text: str) -> float:
    raw = str(content_text or "")
    if not raw:
        return 0.0
    valid_chars = sum([1 for ch in raw if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff")])
    return valid_chars / max(1, len(raw))


def _calc_chinese_ratio(content_text: str) -> float:
    raw = str(content_text or "")
    if not raw:
        return 0.0
    chinese_chars = sum([1 for ch in raw if "\u4e00" <= ch <= "\u9fff"])
    alpha_num = sum([1 for ch in raw if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff")])
    return chinese_chars / max(1, alpha_num)


def _calc_duplicate_paragraph_ratio(content_text: str) -> float:
    paragraphs = _split_paragraphs(content_text)
    if len(paragraphs) <= 1:
        return 0.0
    normalized = [re.sub(r"\s+", "", paragraph) for paragraph in paragraphs if paragraph]
    counter = Counter(normalized)
    duplicated = sum([count for count in counter.values() if count > 1])
    return duplicated / max(1, len(normalized))


def _detect_noise_error(content_text: str) -> str:
    text = str(content_text or "")
    for pattern, code in NOISE_PATTERNS:
        if pattern.search(text):
            return code
    return ""


def validate_content_quality(content_text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    # 这里的质量门禁服务于“能否直接入库并参与检索”，不是做学术意义上的文本质量评分。
    # 因此它更关注三个问题：有没有正文、正文是否像真实内容、正文是否被平台拦截/污染。
    # metadata 先保留为统一接口参数，即使当前只显式使用了部分字段，
    # 后续仍可以在不改函数签名的情况下按平台/风险/提取层做差异化阈值扩展。
    normalized_text = str(content_text or "").strip()
    char_count = len(normalized_text)
    density = _calc_text_density(normalized_text)
    chinese_ratio = _calc_chinese_ratio(normalized_text)
    duplicate_ratio = _calc_duplicate_paragraph_ratio(normalized_text)
    noise_error = _detect_noise_error(normalized_text)
    length_score = min(30.0, (char_count / 400.0) * 30.0)
    density_score = min(25.0, (density / 0.25) * 25.0)
    chinese_score = min(20.0, (chinese_ratio / 0.4) * 20.0)
    duplicate_score = max(0.0, 25.0 * (1.0 - min(1.0, duplicate_ratio / 0.6)))
    # 评分采用可解释的启发式信号组合，方便后续在 UI 上展示 quality_score，
    # 也方便按阈值快速定位“文本太短 / 重复太高 / 噪声太多”等问题。
    quality_score = int(round(max(0.0, min(100.0, length_score + density_score + chinese_score + duplicate_score))))
    if noise_error:
        return {
            "is_valid": False,
            "quality_score": quality_score,
            "error_code": noise_error,
            "failure_reason": noise_error,
            "content_lang": "zh" if chinese_ratio >= 0.3 else "unknown",
            "metrics": {
                "char_count": char_count,
                "text_density": density,
                "chinese_ratio": chinese_ratio,
                "duplicate_ratio": duplicate_ratio,
            },
        }
    # 小于 80 字时，通常不足以支撑后续向量检索与行程生成，因此直接按空正文失败处理。
    if char_count < 80:
        return {
            "is_valid": False,
            "quality_score": quality_score,
            "error_code": "AUTO_PARSE_EMPTY",
            "failure_reason": "content_too_short",
            "content_lang": "zh" if chinese_ratio >= 0.3 else "unknown",
            "metrics": {
                "char_count": char_count,
                "text_density": density,
                "chinese_ratio": chinese_ratio,
                "duplicate_ratio": duplicate_ratio,
            },
        }
    # 重复段落过多通常意味着抓到了折叠区、广告区或模板化页面，而不是可用正文。
    if duplicate_ratio >= 0.4:
        return {
            "is_valid": False,
            "quality_score": quality_score,
            "error_code": "AUTO_PARSE_DUPLICATED",
            "failure_reason": "duplicate_paragraph_ratio_too_high",
            "content_lang": "zh" if chinese_ratio >= 0.3 else "unknown",
            "metrics": {
                "char_count": char_count,
                "text_density": density,
                "chinese_ratio": chinese_ratio,
                "duplicate_ratio": duplicate_ratio,
            },
        }
    if quality_score < 40:
        return {
            "is_valid": False,
            "quality_score": quality_score,
            "error_code": "AUTO_PARSE_LOW_QUALITY",
            "failure_reason": "quality_score_below_threshold",
            "content_lang": "zh" if chinese_ratio >= 0.3 else "unknown",
            "metrics": {
                "char_count": char_count,
                "text_density": density,
                "chinese_ratio": chinese_ratio,
                "duplicate_ratio": duplicate_ratio,
            },
        }
    return {
        "is_valid": True,
        "quality_score": quality_score,
        "error_code": None,
        "failure_reason": "",
        "content_lang": "zh" if chinese_ratio >= 0.3 else "unknown",
        "metrics": {
            "char_count": char_count,
            "text_density": density,
            "chinese_ratio": chinese_ratio,
            "duplicate_ratio": duplicate_ratio,
        },
    }
