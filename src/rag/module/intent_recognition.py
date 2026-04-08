import json
from typing import Any, Dict
from langchain_core.prompts import ChatPromptTemplate
import re
import logging

from src.config import Config
from src.observability import log_event, summarize_value

logger = logging.getLogger(__name__)

def _strip_think_content(text: str) -> str:
    if text is None:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", str(text), flags=re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned)
    return cleaned.strip()

def _format_log_text(text: str, head: int = 180, tail: int = 180) -> str:
    return summarize_value(text, head=head, tail=tail)

def _log_llm_output(tag: str, cleaned_text: str) -> None:
    log_event(logger, logging.INFO, f"意图识别输出: {tag}", {"输出长度": len(cleaned_text), "输出预览": _format_log_text(cleaned_text)})

def _invoke_prompt(llm: Any, prompt: ChatPromptTemplate, **kwargs: Any) -> Any:
    prompt_text = prompt.format_prompt(**kwargs).to_string()
    return llm.invoke(prompt_text)

def _looks_like_no_search_needed(query: str) -> bool:
    no_search_keywords = ["你好", "你是谁", "怎么写", "等于多少", "是谁"]
    for keyword in no_search_keywords:
        if keyword in query:
            return True
    return False

class IntentRecognizer:

    def __init__(self, llm):
        self.config = Config()
        self.llm = llm

    def classify_intent(self, query:str) -> Dict[str, any]:
        """
        不再使用本地模型，使用规则 + LLM进行意图分类
        """
        if _looks_like_no_search_needed(query):
            return {
                "primary_intent": "no_search_needed",
                "confidence": 1.0,
                "needs_search": False
            }
        return self._llm_intent_recongnition(query)

    def _llm_intent_recongnition(self, query:str) -> Dict[str, any]:
        """
        使用LLM进行复杂意图的识别
        """
        prompt = ChatPromptTemplate.from_template("""
        你是一个专业的意图识别专家。请分析用户的查询，识别其主要意图类别。
        
        可选的意图类别：
        1. general_knowledge - 一般知识问题，如科学、历史、文化等
        2. current_events - 时事新闻、最新动态
        3. shopping - 购物、产品推荐、价格比较
        4. travel - 旅游、景点、酒店、交通
        5. no_search_needed - 不需要搜索的问题，如问候、简单计算、编程基础知识
        
        用户查询：{query}
        
        请以JSON格式输出结果，包含以下字段：
        - primary_intent: 主要意图类别
        - confidence: 置信度（0-1）
        - needs_search: 是否需要联网搜索（true/false）
        - keywords: 提取的关键词列表
        - rewritten_queries: 为提高搜索效果，重写1-3个相关查询
        
        只输出JSON，不要包含其他内容。
        """)

        response = _invoke_prompt(self.llm, prompt, query=query)
        content = response.content if hasattr(response, 'content') else response
        cleaned_content = _strip_think_content(content)
        cleaned_content = cleaned_content.replace("```json", "").replace("```", "").strip()
        _log_llm_output("intent_response", cleaned_content)
        
        try:
            return json.loads(cleaned_content)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"意图识别 JSON 解析失败: {e}, 原始输出: {cleaned_content[:200]}")
            return {
                "primary_intent": "general_knowledge",
                "confidence": 0.5,
                "needs_search": True,
                "keywords": [query],
                "rewritten_queries": [query],
            }
