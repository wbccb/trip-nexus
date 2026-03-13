from src.rag.module.intent_recognition import IntentRecognizer
from src.rag.network.multi_source_search import MultiSourceSearcher
from src.rag.module.quality_filter import QualityFilter
from src.rag.network.crawler import ContentCrawler
from src.rag.store.vector_store import VectorStore
from typing import Dict, Any, List, Optional
import time
from src.config import Config
import copy
import tiktoken
from langchain_core.prompts import PromptTemplate
import logging
import re

logger = logging.getLogger(__name__)


def _strip_think_content(text: Any) -> str:
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
    print(f"【RAG】{tag} cleaned_len={len(cleaned_text)} cleaned_preview={preview}")

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
        self._token_encoder = tiktoken.get_encoding("cl100k_base")

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

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0
        return len(self._token_encoder.encode(text))

    def _truncate_text_by_tokens(self, text: str, max_tokens: int) -> str:
        if not text:
            return ""
        if max_tokens <= 0:
            return ""
        tokens = self._token_encoder.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return self._token_encoder.decode(tokens[:max_tokens])

    def _context_token_budget(self, query: str, max_tokens: int) -> int:
        reserve = max(128, int(max_tokens * 0.2))
        query_tokens = self._count_tokens(query or "")
        return max(256, int(max_tokens - reserve - query_tokens))

    def _shrink_evidence_to_token_budget(self, evidence: Dict[str, Any], query: str, max_tokens: int) -> Dict[str, Any]:
        if not isinstance(evidence, dict) or not evidence:
            return evidence
        new_evidence = copy.deepcopy(evidence)
        summary_section = new_evidence.get("summary") or {}
        body_section = new_evidence.get("body") or {}
        summary_items = list(summary_section.get("items") or [])
        body_items = list(body_section.get("items") or [])
        budget = self._context_token_budget(query, max_tokens)
        context_text = self._build_context_text(summary_section, body_section)
        current_tokens = self._count_tokens(context_text)
        if current_tokens <= budget:
            return new_evidence
        min_body_tokens = 80
        min_summary_tokens = 40
        while current_tokens > budget and (body_items or summary_items):
            if body_items:
                last = body_items[-1]
                text = str(last.get("text") or "")
                text_tokens = self._count_tokens(text)
                if text_tokens > min_body_tokens:
                    new_max = max(min_body_tokens, int(text_tokens * 0.7))
                    new_text = self._truncate_text_by_tokens(text, new_max)
                    if new_text == text:
                        body_items.pop()
                    else:
                        last["text"] = new_text
                else:
                    body_items.pop()
            else:
                last = summary_items[-1]
                text = str(last.get("text") or "")
                text_tokens = self._count_tokens(text)
                if text_tokens > min_summary_tokens:
                    new_max = max(min_summary_tokens, int(text_tokens * 0.7))
                    new_text = self._truncate_text_by_tokens(text, new_max)
                    if new_text == text:
                        summary_items.pop()
                    else:
                        last["text"] = new_text
                else:
                    summary_items.pop()
            summary_section["items"] = summary_items
            body_section["items"] = body_items
            summary_section["candidates"] = []
            body_section["candidates"] = []
            context_text = self._build_context_text(summary_section, body_section)
            current_tokens = self._count_tokens(context_text)
        summary_section["used_chars"] = sum(len(str(it.get("text") or "")) for it in summary_items)
        body_section["used_chars"] = sum(len(str(it.get("text") or "")) for it in body_items)
        new_evidence["summary"] = summary_section
        new_evidence["body"] = body_section
        print(f"【RAG】Token压缩后Context tokens={current_tokens}, budget={budget}")
        return new_evidence

    def _summarize_text(self, text: str, max_chars: int) -> str:
        """
        对超长正文进行压缩摘要，空结果时回退为硬截断，确保不超 Evidence Budget。
        """
        if not text:
            return ""
        if max_chars <= 0:
            return ""
        if len(text) <= max_chars:
            return text
            
        print(f"【RAG】触发超长摘要: 原文长度={len(text)}, 目标长度={max_chars}")
        template = """请将下面内容压缩为不超过{max_chars}字，保留核心事实与关键数字。

内容：
{content}

压缩结果："""
        prompt = PromptTemplate(
            template=template,
            input_variables=["content", "max_chars"]
        )
        chain = prompt | self.llm
        response = chain.invoke({"content": text, "max_chars": max_chars})
        summary_raw = response.content if hasattr(response, "content") else response
        summary = _strip_think_content(summary_raw)
        _log_llm_output("summarize_response", summary)
        summary = str(summary).strip()
        if summary:
            return summary[:max_chars]
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
        budget = self.config.EVIDENCE_SUMMARY_MAX_CHARS
        max_item_chars = self.config.EVIDENCE_SUMMARY_ITEM_MAX_CHARS
        top_k = self.config.EVIDENCE_SUMMARY_TOP_K
        
        print(f"【RAG】构建Summary Evidence，拿到的配置信息为: Budget={budget}, MaxItem={max_item_chars}, TopK={top_k}")
        
        summary_items = []
        summary_candidates = []
        seen = set()
        used = 0
        skipped_dup = 0
        skipped_budget = 0
        
        for r in filtered_results[: max(top_k * 2, top_k)]:
            title = (r.get("title") or "").strip()
            snippet = (r.get("content_snippet") or "").strip()
            # 用标题 + 摘要去组装combined
            combined = f"{title}：{snippet}" if title and snippet else (title or snippet)
            combined = self._truncate_text(combined, max_item_chars)
            key = self._normalize_text(combined)
            if not combined or key in seen:
                if key in seen:
                    skipped_dup += 1
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
                skipped_budget += 1
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
            
        print(f"【RAG】用标题 + 摘要去组装combined，然后进行截断 => 构建出Summary: 数量(真正)={len(summary_items)}, Candidates(原始，部分可能要跳过)={len(summary_candidates)}, UsedChars={used}/{budget}, Skipped(Dup={skipped_dup}, Budget={skipped_budget})")
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
        
        print(f"【RAG】获取Body Evidence配置: token数量限制={budget}, 个数限制={top_n}, Chunk(Min={min_chunk_chars}, Max={max_chunk_chars})")
        
        used = 0
        skipped_short = 0
        skipped_dup = 0
        truncated_cnt = 0
        
        for doc in relevant_docs:
            text = (doc.page_content or "").strip()
            if len(text) < min_chunk_chars:
                skipped_short += 1
                continue
            key = self._normalize_text(text[:400])
            if key in seen:
                skipped_dup += 1
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
            
        print(f"【RAG】正文构建（还没进行llm压缩) => Body候选集构建完成: Candidates={len(candidates)}, Skipped(Short={skipped_short}, Dup={skipped_dup})")
        
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
                truncated_cnt += 1
                break
            if len(content) > remaining:
                # 如果内容大于剩余容量，则进行压缩
                content = self._summarize_text(content, remaining)
                truncated_cnt += 1
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
            
        print(f"【RAG】Body最终筛选完成（token+top_n限制）: Selected={len(selected)}, UsedChars={used}/{budget}, Truncated/BudgetBreak={truncated_cnt}")
        
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

    def run(self, query: str, intent_info: Optional[Dict[str, Any]] = None, generate_answer: bool = True) -> Dict[str, Any]:
        """
        执行完整的AI检索流程
        """
        start_time = time.time()
        print(f"【RAG】开始检索：{query}")
        
        # 清除旧的向量存储上下文
        self.vector_store.clear()

        # 1. 意图识别 (如果外部未传入，则进行识别)
        if not intent_info:
            intent_info = self.intent_recognizer.classify_intent(query)
            logger.info(f"Intent info: {intent_info}")
            print(f"【RAG】意图识别完成，是否需要检索：{intent_info.get('needs_search', True)}")
        else:
            print(f"【RAG】使用外部传入意图：{intent_info.get('primary_intent')} (Needs Search: {intent_info.get('needs_search')})")

        # 2. 判断是否需要检索
        if not intent_info.get('needs_search', True):
            print("【RAG】无需检索，直接生成回答")
            answer = None
            if generate_answer:
                answer = self._generate_direct_answer(query, intent_info)
            return {
                'query': query,
                'intent_info': intent_info,
                'search_results': [],
                'filtered_results': [],
                'answer': answer,
                'processing_time': time.time() - start_time,
                'needs_search': False
            }

        # 3. 多源搜索 (获取搜索结果摘要)
        logger.info("【RAG】准备开始SearchXNR搜索url列表")
        search_results = self.searcher.search(query, intent_info)
        if search_results:
            logger.info(f"SearchXNR得到: 首条={search_results[0]}, 末条={search_results[-1]}")
        else:
            logger.info("SearchXNR得到: 无结果")
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

        if crawled_contents:
            head = str(crawled_contents[0])[:200]
            tail = str(crawled_contents[-1])[-200:]
            logger.info(f"\n内容抓取（首/尾）: \n{head}\n...\n{tail}\n")
        
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
            print(f"【RAG】Summary 向量检索完成，候选片段数：{len(relevant_docs)} \n")
            
            # 依据 Top N 与 token长度预算 => 两个都得满足 => 构建 Body Evidence
            body_section = self._build_body_section(relevant_docs)


            # 按 Summary/Body Evidence 拼装最终上下文
            context_text = self._build_context_text(summary_section, body_section)
        else:
            # 如果抓取失败，回退到使用 Summary Evidence
            logger.warning("Crawling failed or empty, falling back to snippets")
            context_text = self._build_context_text(summary_section, body_section)

        print(
            "【RAG】证据构建完成\n"
            f"- Summary: {len(summary_section.get('items', []))} items, {summary_section.get('used_chars', 0)}/{summary_section.get('budget_chars', 0)} chars\n"
            f"- Body: {len(body_section.get('items', []))} items, {body_section.get('used_chars', 0)}/{body_section.get('budget_chars', 0)} chars\n"
            f"- Total Context: {len(context_text)} chars"
        )
        answer = None
        if generate_answer:
            logger.info("\n\n\n-------------准备LLM生成回答-------------------")
            answer = self._generate_rag_answer(query, context_text)
            print(f"【RAG】回答生成完成: {answer} \n")
        else:
            print("【RAG】已构建证据，等待人工复核后再生成回答")

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

    def generate_answer_from_evidence(self, query: str, evidence: Dict[str, Any]) -> str:
        """
        基于外部传入的证据（如用户人工筛选后的证据）生成回答。
        """
        max_tokens = int(self.config.MAX_CONTEXT_TOKENS or 4096)
        context_budget = self._context_token_budget(query, max_tokens)
        summary_section = evidence.get("summary", {})
        body_section = evidence.get("body", {})
        context_text = self._build_context_text(summary_section, body_section)
        context_tokens = self._count_tokens(context_text)
        print(f"【RAG】人工复核证据Context tokens={context_tokens}, budget={context_budget}")
        if context_tokens > context_budget:
            evidence = self._shrink_evidence_to_token_budget(evidence, query, max_tokens)
            summary_section = evidence.get("summary", {})
            body_section = evidence.get("body", {})
            context_text = self._build_context_text(summary_section, body_section)
            context_tokens = self._count_tokens(context_text)
            print(f"【RAG】压缩后Context tokens={context_tokens}, budget={context_budget}")
        return self._generate_rag_answer(query, context_text)

    def _generate_direct_answer(self, query: str, intent_info: Dict[str, Any]) -> str:
        """
        无需搜索直接回答
        """
        # 简单透传给LLM，或者使用特定的Prompt
        raw_response = self.llm.invoke(query)
        response_text = raw_response.content if hasattr(raw_response, "content") else raw_response
        cleaned_text = _strip_think_content(response_text)
        _log_llm_output("direct_answer_response", cleaned_text)
        return cleaned_text

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


        print(f"""\n\n\n=================================检索组装的prompt:\n{prompt.format(context=context, query=query)}\n===========================""")


        chain = prompt | self.llm
        raw_response = chain.invoke({"context": context, "query": query})
        response_text = raw_response.content if hasattr(raw_response, "content") else raw_response
        cleaned_text = _strip_think_content(response_text)
        _log_llm_output("rag_answer_response", cleaned_text)
        return cleaned_text
