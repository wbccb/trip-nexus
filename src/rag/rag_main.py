from src.rag.module.intent_recognition import IntentRecognizer
from src.rag.network.multi_source_search import MultiSourceSearcher
from src.rag.module.quality_filter import QualityFilter
from src.rag.network.crawler import ContentCrawler
from src.rag.store.vector_store import VectorStore
from typing import Dict, Any, List
import time
from src.config import Config
from langchain_core.prompts import PromptTemplate
import logging
import re

logger = logging.getLogger(__name__)

class AIRetrievalPipeline:
    def __init__(self, llm):
        self.config = Config()
        self.llm = llm
        self.intent_recognizer = IntentRecognizer(llm)
        self.searcher = MultiSourceSearcher(llm)
        self.quality_filter = QualityFilter()
        self.crawler = ContentCrawler()
        # 使用 ephemeral_collection 或者每次清除
        self.vector_store = VectorStore(collection_name="current_search_context")

    def _normalize_text(self, text: str) -> str:
        """
        归一化文本，用于去重与一致性比较，避免空白与大小写带来的重复。
        """
        return re.sub(r"\s+", " ", text or "").strip().lower()

    def _truncate_text(self, text: str, max_chars: int) -> str:
        """
        按字符长度截断文本，用于严格控制 Evidence Budget 上限。
        """
        if max_chars <= 0:
            return ""
        return (text or "")[:max_chars]

    def _summarize_text(self, text: str, max_chars: int) -> str:
        """
        对超长正文进行压缩摘要，失败时回退为硬截断，确保不超 Evidence Budget。
        """
        if not text:
            return ""
        if max_chars <= 0:
            return ""
        if len(text) <= max_chars:
            return text
        template = """请将下面内容压缩为不超过{max_chars}字，保留核心事实与关键数字。

内容：
{content}

压缩结果："""
        prompt = PromptTemplate(
            template=template,
            input_variables=["content", "max_chars"]
        )
        chain = prompt | self.llm
        try:
            response = chain.invoke({"content": text, "max_chars": max_chars})
            summary = response.content if hasattr(response, "content") else response
            summary = str(summary).strip()
            if summary:
                return summary[:max_chars]
        except Exception as e:
            logger.warning(f"Summary failed: {e}")
        return self._truncate_text(text, max_chars)

    def _build_summary_section(self, filtered_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        构建 Summary Evidence：按 Top K + token字符预算截断（两个条件都得满足），提供去重后的候选清单。

        输出字段约定（供前端证据可视化使用）：
        - source：原始 URL
        - engine：搜索引擎/来源标识（如 searxng 返回的 engine）
        - confidence/score：重排/相关度分数（0-1 之间时可视为置信度）
        - timestamp：搜索结果发布时间（如 searxng publishedDate），可能为空
        """
        summary_items = []
        summary_candidates = []
        seen = set()
        budget = self.config.EVIDENCE_SUMMARY_MAX_CHARS
        max_item_chars = self.config.EVIDENCE_SUMMARY_ITEM_MAX_CHARS
        top_k = self.config.EVIDENCE_SUMMARY_TOP_K
        used = 0
        for r in filtered_results[: max(top_k * 2, top_k)]:
            title = (r.get("title") or "").strip()
            snippet = (r.get("content_snippet") or "").strip()
            # 用标题 + 摘要去组装combined
            combined = f"{title}：{snippet}" if title and snippet else (title or snippet)
            combined = self._truncate_text(combined, max_item_chars)
            key = self._normalize_text(combined)
            if not combined or key in seen:
                continue
            seen.add(key)
            confidence = r.get("score")
            timestamp = r.get("timestamp")
            summary_candidates.append({
                "type": "summary",
                "source": r.get("url"),
                "engine": r.get("source"),
                "title": title,
                "text": combined,
                "score": confidence,
                "confidence": confidence,
                "timestamp": timestamp,
            })
            if used + len(combined) > budget or len(summary_items) >= top_k:
                continue
            summary_items.append({
                "source": r.get("url"),
                "engine": r.get("source"),
                "title": title,
                "text": combined,
                "score": confidence,
                "confidence": confidence,
                "timestamp": timestamp,
            })
            used += len(combined)
        return {
            "items": summary_items,
            "candidates": summary_candidates,
            "used_chars": used,
            "budget_chars": budget
        }

    def _build_body_section(self, relevant_docs: List[Any]) -> Dict[str, Any]:
        """
        构建 Body Evidence：候选段落去重后按 Top N 选取，超长段落自动摘要并裁剪预算。

        说明：
        - Body Evidence 主要来自抓取正文的相似度检索结果；
        - confidence/score/timestamp/engine 等信息来自写入向量库时的 metadata（按 URL 回填）。
        """
        candidates = []
        selected = []
        seen = set()
        budget = self.config.EVIDENCE_BODY_MAX_CHARS
        top_n = self.config.EVIDENCE_BODY_TOP_N
        max_chunk_chars = self.config.EVIDENCE_CHUNK_MAX_CHARS
        min_chunk_chars = self.config.EVIDENCE_CHUNK_MIN_CHARS
        used = 0
        for doc in relevant_docs:
            text = (doc.page_content or "").strip()
            if len(text) < min_chunk_chars:
                continue
            key = self._normalize_text(text[:400])
            if key in seen:
                continue
            seen.add(key)
            meta = doc.metadata or {}
            confidence = meta.get("score")
            timestamp = meta.get("timestamp")
            candidates.append({
                "type": "body",
                "source": meta.get("source"),
                "title": meta.get("title"),
                "text": text,
                "score": confidence,
                "confidence": confidence,
                "timestamp": timestamp,
            })
        for cand in candidates:
            if len(selected) >= top_n:
                # 如果选择数量超过了top_n，则break
                break
            content = cand["text"]
            if len(content) > max_chunk_chars:
                # 如果内容太长，则进行压缩
                content = self._summarize_text(content, max_chunk_chars)
            remaining = budget - used
            if remaining <= 0:
                # 如果目前token已经用完，则break
                break
            if len(content) > remaining:
                # 如果内容大于剩余容量，则进行压缩
                content = self._summarize_text(content, remaining)
            if not content:
                continue
            selected.append({
                "source": cand.get("source"),
                "title": cand.get("title"),
                "text": content,
                "score": cand.get("score"),
                "confidence": cand.get("confidence"),
                "timestamp": cand.get("timestamp"),
            })
            used += len(content)
        return {
            "items": selected,
            "candidates": candidates,
            "used_chars": used,
            "budget_chars": budget
        }

    def _build_context_text(self, summary_section: Dict[str, Any], body_section: Dict[str, Any]) -> str:
        """
        将 Summary/Body Evidence 拼装为 LLM 可读的上下文文本。
        """
        summary_items = summary_section.get("items", [])
        body_items = body_section.get("items", [])
        summary_text = "\n".join([f"- {item['text']}" for item in summary_items]) if summary_items else "无"
        body_text = "\n\n".join([item["text"] for item in body_items]) if body_items else "无"
        return f"【摘要证据】\n{summary_text}\n\n【正文证据】\n{body_text}"

    def run(self, query: str) -> Dict[str, Any]:
        """
        执行完整的AI检索流程
        """
        start_time = time.time()
        print(f"【RAG】开始检索：{query}")
        
        # 清除旧的向量存储上下文
        self.vector_store.clear()

        # 1. 意图识别
        intent_info = self.intent_recognizer.classify_intent(query)
        logger.info(f"Intent info: {intent_info}")
        print(f"【RAG】意图识别完成，是否需要检索：{intent_info.get('needs_search', True)}")

        # 2. 判断是否需要检索
        if not intent_info.get('needs_search', True):
            print("【RAG】无需检索，直接生成回答")
            return {
                'query': query,
                'intent_info': intent_info,
                'search_results': [],
                'filtered_results': [],
                'answer': self._generate_direct_answer(query, intent_info),
                'processing_time': time.time() - start_time,
                'needs_search': False
            }

        # 3. 多源搜索 (获取搜索结果摘要)
        logger.info("准备开始SearchXNR搜索url列表")
        search_results = self.searcher.search(query, intent_info)
        logger.info(f"SearchXNR得到: {search_results}")
        print(f"【RAG】搜索完成，结果数：{len(search_results)}")

        logger.info("-------------准备质量过滤-------------------")

        

        # 4. 质量过滤 (基于摘要重排序)
        filtered_results = self.quality_filter.filter_and_rank(search_results, query)
        logger.info(f"质量过滤 {len(filtered_results)} 结果")
        print(f"【RAG】质量过滤完成，保留数：{len(filtered_results)}")

        logger.info("-------------准备内容抓取-------------------")

        # 5. 内容抓取 (Deep Fetch)
        # 取 Top K 进行抓取
        urls_to_fetch = [r['url'] for r in filtered_results[:self.config.DETAIL_FETCH_TOP_K]]
        crawled_contents = self.crawler.fetch_urls(urls_to_fetch)
        logger.info(f"内容抓取 {len(crawled_contents)} pages")
        print(f"【RAG】内容抓取完成，页面数：{len(crawled_contents)}")

        logger.info(f"\n内容抓取内容: \n {crawled_contents}\n")

        
        logger.info("-------------准备向量化存储与检索-------------------")


        # 6. 向量化存储与检索 (RAG)
        summary_section = self._build_summary_section(filtered_results)
        body_section = {"items": [], "candidates": [], "used_chars": 0, "budget_chars": self.config.EVIDENCE_BODY_MAX_CHARS}
        context_text = ""
        if crawled_contents:
            # 存入向量数据库
            # 将抓取的内容转为 Document 格式
            url_meta: Dict[str, Dict[str, Any]] = {}
            for r in filtered_results:
                url = r.get("url")
                if not url:
                    continue
                # 用 URL 作为 key，把“摘要检索阶段”的元信息回填到正文证据上，
                # 以满足前端“来源/置信度/时间戳”统一展示的需求。
                url_meta[str(url)] = {
                    "score": r.get("score"),
                    "timestamp": r.get("timestamp"),
                    "engine": r.get("source"),
                }
            documents = []
            for content in crawled_contents:
                meta = url_meta.get(str(content.get("url") or ""), {})
                documents.append({
                    "content": content["content"],
                    "metadata": {
                        "source": content["url"],
                        "title": content["title"],
                        "score": meta.get("score"),
                        "timestamp": meta.get("timestamp"),
                        "engine": meta.get("engine"),
                    }
                })
            self.vector_store.add_documents(documents)
            
            # 将联网检索到的正文存入向量库后，检索相关片段作为 Body Evidence 候选（抓取正文的高相关段落）
            relevant_docs = self.vector_store.similarity_search(query, k=self.config.EVIDENCE_BODY_CANDIDATE_K)
            # 依据 Top N 与 token长度预算 => 两个都得满足 => 构建 Body Evidence
            body_section = self._build_body_section(relevant_docs)


            # 按 Summary/Body Evidence 拼装最终上下文
            context_text = self._build_context_text(summary_section, body_section)
        else:
            # 如果抓取失败，回退到使用 Summary Evidence
            logger.warning("Crawling failed or empty, falling back to snippets")
            context_text = self._build_context_text(summary_section, body_section)

        logger.info("-------------准备LLM生成回答-------------------")
        print(
            "【RAG】证据构建完成，摘要/正文条目数："
            f"{len(summary_section.get('items', []))}/{len(body_section.get('items', []))}"
        )


        # 7. 生成回答
        answer = self._generate_rag_answer(query, context_text)
        print("【RAG】回答生成完成")

        processing_time = time.time() - start_time

        return {
            'query': query,
            'intent_info': intent_info,
            'search_results': search_results,
            'filtered_results': filtered_results,
            'crawled_contents': crawled_contents, # 可选：返回抓取内容供前端展示
            'evidence': {
                'summary': summary_section,
                'body': body_section,
                'budget': {
                    'summary_max_chars': self.config.EVIDENCE_SUMMARY_MAX_CHARS,
                    'body_max_chars': self.config.EVIDENCE_BODY_MAX_CHARS
                }
            },
            'answer': answer,
            'processing_time': processing_time,
            'needs_search': True
        }

    def _generate_direct_answer(self, query: str, intent_info: Dict[str, Any]) -> str:
        """
        无需搜索直接回答
        """
        # 简单透传给LLM，或者使用特定的Prompt
        return self.llm.invoke(query)

    def _generate_rag_answer(self, query: str, context: str) -> str:
        """
        基于上下文生成回答
        """
        template = """基于以下参考信息回答用户的问题。如果参考信息不足以回答问题，请说明。

参考信息：
{context}

用户问题：
{query}

回答："""
        
        prompt = PromptTemplate(
            template=template,
            input_variables=["context", "query"]
        )


        print(f"""\n\n\n=========检索完成，组装的prompt:\n{prompt.format(context=context, query=query)}\n\n=========""")


        chain = prompt | self.llm
        return chain.invoke({"context": context, "query": query})
