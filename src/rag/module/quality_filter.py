from typing import List, Dict
from src.config import Config
import logging

logger = logging.getLogger(__name__)

class QualityFilter:
    def __init__(self):
        self.config = Config()
        try:
            from sentence_transformers import CrossEncoder
            self.reranker = CrossEncoder(self.config.MINILM_MODEL)
        except Exception as e:
            logger.warning(f"Failed to load CrossEncoder model: {e}. Using simple heuristic.")
            self.reranker = None

    def filter_and_rank(self, results: List[Dict[str, any]], query: str) -> List[Dict[str, any]]:
        """
        对搜索结果进行质量过滤和重排序
        """
        if not results:
            return []

        # 1. 初步去重 (根据URL)
        initial_count = len(results)
        seen_urls = set()
        unique_results = []
        for r in results:
            if r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                unique_results.append(r)
        
        results = unique_results
        logger.debug(f"【QualityFilter】URL去重: {initial_count} -> {len(results)}")

        # 2. 如果有重排序模型，使用模型打分
        if self.reranker:
            try:
                # 准备 (query, title + snippet) 对
                pairs = [[query, f"{r.get('title', '')} {r.get('content_snippet', '')}"] for r in results]
                scores = self.reranker.predict(pairs)
                
                # 更新分数
                for i, score in enumerate(scores):
                    results[i]['score'] = float(score)
                
                # 排序
                results.sort(key=lambda x: x['score'], reverse=True)
            except Exception as e:
                logger.error(f"Reranking failed: {e}")
        
        # 3. 截断 (返回 Top K)
        # 根据 Config 配置，但这里可以硬编码一个合理的默认值或使用 Config
        top_k = getattr(self.config, 'RERANK_TOP_K', 5)
        return results[:top_k]
