from typing import List, Dict, Any
from src.config import Config
import logging
import os
import time

import psutil

logger = logging.getLogger(__name__)

class QualityFilter:
    def __init__(self):
        self.config = Config()
        self.reranker = None
        self._reranker_load_attempted = False

    def _build_runtime_snapshot(self) -> Dict[str, Any]:
        process = psutil.Process(os.getpid())
        rss_bytes = 0
        try:
            rss_bytes = int(process.memory_info().rss)
        except Exception:
            rss_bytes = 0
        return {
            "pid": os.getpid(),
            "rss_mb": round(rss_bytes / 1024 / 1024, 2),
            "reranker_enabled": bool(self.config.ENABLE_CROSS_ENCODER_RERANKER),
            "reranker_model": str(self.config.MINILM_MODEL or ""),
        }

    def _ensure_reranker(self) -> None:
        if self._reranker_load_attempted:
            return
        self._reranker_load_attempted = True

        if not self.config.ENABLE_CROSS_ENCODER_RERANKER:
            logger.info("QualityFilter reranker disabled by config: %s", self._build_runtime_snapshot())
            return

        load_started_at = time.perf_counter()
        logger.info("QualityFilter reranker loading started: %s", self._build_runtime_snapshot())
        try:
            from sentence_transformers import CrossEncoder

            self.reranker = CrossEncoder(self.config.MINILM_MODEL)
            logger.info(
                "QualityFilter reranker loading finished: %s",
                {
                    **self._build_runtime_snapshot(),
                    "cost_ms": round((time.perf_counter() - load_started_at) * 1000.0, 2),
                },
            )
        except Exception as e:
            logger.warning(
                "Failed to load CrossEncoder model, fallback to heuristic: error=%s snapshot=%s",
                e,
                self._build_runtime_snapshot(),
            )
            self.reranker = None

    def filter_and_rank(self, results: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
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
        self._ensure_reranker()
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
