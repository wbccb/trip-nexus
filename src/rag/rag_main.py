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

    def run(self, query: str) -> Dict[str, Any]:
        """
        执行完整的AI检索流程
        """
        start_time = time.time()
        
        # 清除旧的向量存储上下文
        self.vector_store.clear()

        # 1. 意图识别
        intent_info = self.intent_recognizer.classify_intent(query)
        logger.info(f"Intent info: {intent_info}")

        # 2. 判断是否需要检索
        if not intent_info.get('needs_search', True):
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

        logger.info("-------------准备质量过滤-------------------")

        

        # 4. 质量过滤 (基于摘要重排序)
        filtered_results = self.quality_filter.filter_and_rank(search_results, query)
        logger.info(f"质量过滤 {len(filtered_results)} 结果")

        logger.info("-------------准备内容抓取-------------------")

        # 5. 内容抓取 (Deep Fetch)
        # 取 Top K 进行抓取
        urls_to_fetch = [r['url'] for r in filtered_results[:self.config.DETAIL_FETCH_TOP_K]]
        crawled_contents = self.crawler.fetch_urls(urls_to_fetch)
        logger.info(f"内容抓取 {len(crawled_contents)} pages")
        
        logger.info("-------------准备向量化存储与检索-------------------")


        # 6. 向量化存储与检索 (RAG)
        context_text = ""
        if crawled_contents:
            # 存入向量数据库
            # 将抓取的内容转为 Document 格式
            documents = []
            for content in crawled_contents:
                documents.append({
                    "content": content["content"],
                    "metadata": {"source": content["url"], "title": content["title"]}
                })
            self.vector_store.add_documents(documents)
            
            # 检索相关片段
            relevant_docs = self.vector_store.similarity_search(query, k=5)
            context_text = "\n\n".join([d.page_content for d in relevant_docs])
        else:
            # 如果抓取失败，回退到使用摘要
            logger.warning("Crawling failed or empty, falling back to snippets")
            context_text = "\n\n".join([f"{r['title']}: {r['content_snippet']}" for r in filtered_results[:5]])

        logger.info("-------------准备LLM生成回答-------------------")


        # 7. 生成回答
        answer = self._generate_rag_answer(query, context_text)

        processing_time = time.time() - start_time

        return {
            'query': query,
            'intent_info': intent_info,
            'search_results': search_results,
            'filtered_results': filtered_results,
            'crawled_contents': crawled_contents, # 可选：返回抓取内容供前端展示
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
