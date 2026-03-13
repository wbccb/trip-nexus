import json
from typing import Dict
from functools import lru_cache
from sentence_transformers import SentenceTransformer, util
from langchain_core.prompts import ChatPromptTemplate
import re

from src.config import Config

try:
    import streamlit as st
    _st_cache_resource = st.cache_resource
except Exception:
    _st_cache_resource = None

if _st_cache_resource:
    @_st_cache_resource
    def _get_sentence_transformer(model_name: str) -> SentenceTransformer:
        return SentenceTransformer(model_name)
else:
    @lru_cache(maxsize=1)
    def _get_sentence_transformer(model_name: str) -> SentenceTransformer:
        return SentenceTransformer(model_name)


def _strip_think_content(text: str) -> str:
    if text is None:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", str(text), flags=re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned)
    return cleaned.strip()


def _format_log_text(text: str, head: int = 180, tail: int = 180) -> str:
    if text is None:
        return ""
    text_value = str(text)
    if len(text_value) <= head + tail + 5:
        return text_value
    return f"{text_value[:head]}....{text_value[-tail:]}"


def _log_llm_output(tag: str, cleaned_text: str) -> None:
    preview = _format_log_text(cleaned_text)
    print(f"[IntentRecognizer] {tag} cleaned_len={len(cleaned_text)} cleaned_preview={preview}")

class IntentRecognizer:
    def __init__(self, llm):
        self.config = Config()
        self.llm = llm
        self.intent_classifier = _get_sentence_transformer(self.config.SENTENCE_BERT_MODEL)

        # 预定义的意图类别和示例
        # TODO: 初步简单处理，后续需要优化这部分的内置意图
        self.intent_examples = {
            "general_knowledge": [
                "什么是量子计算？",
                "爱因斯坦的相对论是什么？",
                "太阳系有多少颗行星？"
            ],
            "current_events": [
                "今天有什么新闻？",
                "最新的科技趋势是什么？",
                "最近的体育赛事结果如何？"
            ],
            "shopping": [
                "最好的笔记本电脑推荐",
                "手机价格比较",
                "在哪里买便宜的机票？"
            ],
            "travel": [
                "巴黎旅游攻略",
                "日本签证要求",
                "最佳旅游季节"
            ],
            "no_search_needed": [
                "你好",
                "你是谁",
                "10的9次方等于多少",
                "Python怎么写for循环"
            ]
        }
        self.intent_embeddings = {}
        for intent, examples in self.intent_examples.items():
            embeddings = self.intent_classifier.encode(examples, convert_to_tensor=True)
            self.intent_embeddings[intent] = embeddings.mean(dim=0) # 为每个意图生成嵌入向量

    def classify_intent(self, query:str) -> Dict[str, any]:
        """
        先使用Sentence-BERT进行初步意图分类
        """
        query_embedding = self.intent_classifier.encode(query, convert_to_tensor=True)

        # 计算与每个意图类别的相似度
        similarities = {}
        for intent, embedding in self.intent_embeddings.items():
            similarity = util.cos_sim(query_embedding, embedding).item()
            similarities[intent] = similarity

        # 获取最高相似度的意图
        top_intent = max(similarities.items(), key=lambda x: x[1])

        # 如果相似度低于阀值
        if top_intent[1] < 0.6:
            return self._llm_intent_recongnition(query)

        return {
            "primary_intent": top_intent[0],
            "confidence": top_intent[1],
            "similarities": similarities,
            "needs_search": top_intent[0] != "no_search_needed"
        }

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

        chain = prompt | self.llm
        response = chain.invoke({"query": query})
        content = response.content if hasattr(response, 'content') else response
        cleaned_content = _strip_think_content(content)
        cleaned_content = cleaned_content.replace("```json", "").replace("```", "").strip()
        _log_llm_output("intent_response", cleaned_content)
        return json.loads(cleaned_content)
