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
from src.observability import log_event, log_llm_end, log_llm_start, summarize_value

logger = logging.getLogger(__name__)


def _strip_think_content(text: Any) -> str:
    if text is None:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", str(text), flags=re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned)
    return cleaned.strip()


def _format_log_text(text: str, head: int = 180, tail: int = 180) -> str:
    return summarize_value(text, head=head, tail=tail)


def _log_llm_output(tag: str, cleaned_text: str) -> None:
    log_event(logger, logging.DEBUG, f"RAG LLM 输出: {tag}", {"输出长度": len(cleaned_text), "输出预览": _format_log_text(cleaned_text)})


def _invoke_prompt(llm: Any, prompt: PromptTemplate, **kwargs: Any) -> Any:
    prompt_text = prompt.format(**kwargs)
    return llm.invoke(prompt_text)


def _build_item_preview_fields(
    items: List[Dict[str, Any]],
    *,
    prefix: str,
    max_items: int = 3,
) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for index, item in enumerate(items[:max_items], start=1):
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or item.get("content") or item.get("content_snippet") or "").strip()
        combined = f"{title}：{text}" if title and text else (title or text)
        fields[f"{prefix}{index}"] = summarize_value(combined, head=100, tail=80)
    return fields


def _log_rag_step(message: str, data: Optional[Dict[str, Any]] = None) -> None:
    log_event(logger, logging.INFO, f"{message}\n----------------------", data)


class AIRetrievalPipeline:

    def __init__(self, llm):
        self.config = Config()
        self.llm = llm
        self.intent_recognizer = IntentRecognizer(llm)
        self.searcher = MultiSourceSearcher(llm)
        self.quality_filter = QualityFilter()
        self.crawler = ContentCrawler()
        # 向量库延迟初始化，避免非 RAG 路径在低内存实例上提前加载 embedding/chroma。
        self.vector_store: Optional[VectorStore] = None
        self._token_encoder = tiktoken.get_encoding("cl100k_base")

    def _get_vector_store(self) -> VectorStore:
        if self.vector_store is None:
            started_at = time.perf_counter()
            self.vector_store = VectorStore(collection_name="current_search_context")
            log_event(
                logger,
                logging.INFO,
                "RAG 向量存储初始化完成",
                {
                    "collection_name": getattr(self.vector_store, "collection_name", ""),
                    "client_mode": getattr(self.vector_store, "client_mode", ""),
                    "cost_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
                },
            )
        return self.vector_store

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
        log_event(logger, logging.INFO, "RAG 证据压缩完成", {"当前Token": current_tokens, "预算Token": budget})
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
            
        log_event(logger, logging.INFO, "RAG 触发长文本摘要", {"原文长度": len(text), "目标长度": max_chars})
        template = """请将下面内容压缩为不超过{max_chars}字，保留核心事实与关键数字。

内容：
{content}

压缩结果："""
        prompt = PromptTemplate(
            template=template,
            input_variables=["content", "max_chars"]
        )
        started_at = log_llm_start(
            logger,
            stage="RAG 长文本摘要",
            model=getattr(self.llm, "model", getattr(self.llm, "model_name", "未知模型")),
            prompt=template.format(content=summarize_value(text, 60, 60), max_chars=max_chars),
            extra={"目标长度": max_chars},
        )
        response = _invoke_prompt(self.llm, prompt, content=text, max_chars=max_chars)
        summary_raw = response.content if hasattr(response, "content") else response
        summary = _strip_think_content(summary_raw)
        _log_llm_output("summarize_response", summary)
        log_llm_end(logger, stage="RAG 长文本摘要", started_at=started_at, output=summary)
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
        
        _log_rag_step("RAG Step 6.1 摘要证据构建开始", {"预算字符": budget, "单条上限": max_item_chars, "TopK": top_k})
        
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
            
        summary_log_payload = {
            "摘要条数": len(summary_items),
            "候选条数": len(summary_candidates),
            "已用字符": f"{used}/{budget}",
            "去重跳过": skipped_dup,
            "预算跳过": skipped_budget,
        }
        summary_log_payload.update(_build_item_preview_fields(summary_items, prefix="摘要证据", max_items=3))
        _log_rag_step("RAG Step 6.1 摘要证据构建完成", summary_log_payload)
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
        
        _log_rag_step("RAG Step 6.2 正文证据构建开始", {"预算字符": budget, "TopN": top_n, "最小分块": min_chunk_chars, "最大分块": max_chunk_chars})
        
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
            
        body_candidate_payload = {
            "候选条数": len(candidates),
            "过短跳过": skipped_short,
            "重复跳过": skipped_dup,
        }
        body_candidate_payload.update(_build_item_preview_fields(candidates, prefix="正文候选", max_items=3))
        _log_rag_step("RAG Step 6.2 正文候选构建完成", body_candidate_payload)
        
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
            
        body_selected_payload = {
            "入选条数": len(selected),
            "已用字符": f"{used}/{budget}",
            "截断次数": truncated_cnt,
        }
        body_selected_payload.update(_build_item_preview_fields(selected, prefix="正文证据", max_items=3))
        _log_rag_step("RAG Step 6.2 正文证据筛选完成", body_selected_payload)
        
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
        _log_rag_step("RAG Step 0 检索开始", {"查询": query})
        
        # 清除旧的向量存储上下文
        self._get_vector_store().clear()

        # 1. 意图识别 (如果外部未传入，则进行识别)
        if not intent_info:
            intent_info = self.intent_recognizer.classify_intent(query)
            _log_rag_step("RAG Step 1 意图识别完成", {"主意图": intent_info.get("primary_intent"), "需要检索": intent_info.get("needs_search", True)})
        else:
            _log_rag_step("RAG Step 1 使用外部意图", {"主意图": intent_info.get("primary_intent"), "需要检索": intent_info.get("needs_search")})

        # 2. 判断是否需要检索
        if not intent_info.get('needs_search', True):
            _log_rag_step("RAG Step 2 跳过检索", {"原因": "needs_search=False"})
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
        _log_rag_step("RAG Step 3 多源搜索开始", {"查询": query})
        search_results = self.searcher.search(query, intent_info)
        search_log_payload = {"结果数": len(search_results)}
        search_log_payload.update(_build_item_preview_fields(search_results, prefix="搜索结果", max_items=3))
        _log_rag_step("RAG Step 3 多源搜索完成", search_log_payload)

        # 4. 质量过滤 (基于摘要重排序)
        _log_rag_step("RAG Step 4 质量过滤开始", {"输入结果数": len(search_results)})
        filtered_results = self.quality_filter.filter_and_rank(search_results, query)
        filter_log_payload = {"保留数": len(filtered_results)}
        filter_log_payload.update(_build_item_preview_fields(filtered_results, prefix="过滤结果", max_items=3))
        _log_rag_step("RAG Step 4 质量过滤完成", filter_log_payload)

        # 5. 内容抓取 (Deep Fetch)
        # 取 Top K 进行抓取
        urls_to_fetch = [r['url'] for r in filtered_results[:self.config.DETAIL_FETCH_TOP_K]]
        _log_rag_step("RAG Step 5 内容抓取开始", {"抓取URL数": len(urls_to_fetch)})
        crawled_contents = self.crawler.fetch_urls(urls_to_fetch)
        crawl_log_payload = {"页面数": len(crawled_contents)}
        crawl_log_payload.update(_build_item_preview_fields(crawled_contents, prefix="抓取结果", max_items=2))
        _log_rag_step("RAG Step 5 内容抓取完成", crawl_log_payload)


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
            self._get_vector_store().add_documents(documents)
            
            # 将联网检索到的正文存入向量库后，检索相关片段作为 Body Evidence 候选（抓取正文的高相关段落）
            relevant_docs = self._get_vector_store().similarity_search(query, k=self.config.EVIDENCE_BODY_CANDIDATE_K)
            _log_rag_step("RAG Step 6 向量检索完成", {"候选片段数": len(relevant_docs)})
            
            # 依据 Top N 与 token长度预算 => 两个都得满足 => 构建 Body Evidence
            body_section = self._build_body_section(relevant_docs)


            # 按 Summary/Body Evidence 拼装最终上下文
            context_text = self._build_context_text(summary_section, body_section)
        else:
            # 如果抓取失败，回退到使用 Summary Evidence
            logger.warning("Crawling failed or empty, falling back to snippets")
            context_text = self._build_context_text(summary_section, body_section)

        _log_rag_step(
            "RAG Step 6 证据构建完成",
            {
                "成功": bool(summary_section.get("items") or body_section.get("items")),
                "摘要条数": len(summary_section.get("items", [])),
                "正文条数": len(body_section.get("items", [])),
                "上下文长度": len(context_text),
                **_build_item_preview_fields(summary_section.get("items", []), prefix="摘要内容", max_items=3),
                **_build_item_preview_fields(body_section.get("items", []), prefix="正文内容", max_items=3),
            },
        )
        answer = None
        if generate_answer:
            logger.debug("\n\n\n-------------准备LLM生成回答-------------------")
            answer = self._generate_rag_answer(query, context_text)
            log_event(logger, logging.DEBUG, "RAG 回答生成完成", {"回答预览": answer})
        else:
            log_event(logger, logging.DEBUG, "RAG 已构建证据，等待人工复核")

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
        log_event(logger, logging.INFO, "人工复核证据预算", {"当前Token": context_tokens, "预算Token": context_budget})
        if context_tokens > context_budget:
            evidence = self._shrink_evidence_to_token_budget(evidence, query, max_tokens)
            summary_section = evidence.get("summary", {})
            body_section = evidence.get("body", {})
            context_text = self._build_context_text(summary_section, body_section)
            context_tokens = self._count_tokens(context_text)
            log_event(logger, logging.INFO, "人工复核证据压缩完成", {"当前Token": context_tokens, "预算Token": context_budget})
        return self._generate_rag_answer(query, context_text)

    def _generate_direct_answer(self, query: str, intent_info: Dict[str, Any]) -> str:
        """
        无需搜索直接回答
        """
        # 简单透传给LLM，或者使用特定的Prompt
        started_at = log_llm_start(
            logger,
            stage="RAG 直接回答",
            model=getattr(self.llm, "model", getattr(self.llm, "model_name", "未知模型")),
            prompt=query,
            extra={"主意图": intent_info.get("primary_intent")},
        )
        raw_response = self.llm.invoke(query)
        response_text = raw_response.content if hasattr(raw_response, "content") else raw_response
        cleaned_text = _strip_think_content(response_text)
        _log_llm_output("direct_answer_response", cleaned_text)
        log_llm_end(logger, stage="RAG 直接回答", started_at=started_at, output=cleaned_text)
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


        log_event(logger, logging.DEBUG, "RAG 回答提示词已构建", {"提示词预览": prompt.format(context=context, query=query)})


        started_at = log_llm_start(
            logger,
            stage="RAG 回答生成",
            model=getattr(self.llm, "model", getattr(self.llm, "model_name", "未知模型")),
            prompt=prompt.format(context=summarize_value(context, 60, 60), query=query),
        )
        raw_response = _invoke_prompt(self.llm, prompt, context=context, query=query)
        response_text = raw_response.content if hasattr(raw_response, "content") else raw_response
        cleaned_text = _strip_think_content(response_text)
        _log_llm_output("rag_answer_response", cleaned_text)
        log_llm_end(logger, stage="RAG 回答生成", started_at=started_at, output=cleaned_text)
        return cleaned_text
