from src.rag.module.intent_recognition import IntentRecognizer
from src.rag.network.multi_source_search import MultiSourceSearcher
from src.rag.module.quality_filter import QualityFilter
from typing import Dict, Any
import time
from src.config import Config

class AIRetrievalPipeline:
    def __init__(self, llm):
        self.config = Config()
        self.intent_recognizer = IntentRecognizer(llm)
        self.searcher = MultiSourceSearcher(llm)
        self.quality_filter = QualityFilter()

    def run(self, query: str) -> Dict[str, Any]:
        """
        执行完整的AI检索流程
        """
        start_time = time.time()

        # 1. 意图识别
        intent_info = self.intent_recognizer.classify_intent(query)

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

        # 3. 多源搜索
        search_results = self.searcher.search(query, intent_info)

        # 4. 质量过滤
        filtered_results = self.quality_filter.filter_and_rank(search_results)


        processing_time = time.time() - start_time

        return {
            'query': query,
            'intent_info': intent_info,
            'search_results': search_results,
            'filtered_results': filtered_results,
            'processing_time': processing_time,
            'needs_search': True
        }