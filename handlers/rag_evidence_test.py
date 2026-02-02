import logging
import sys
import os
from typing import Dict, Any

# 将项目根目录添加到 python path，以便能找到 src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.rag.rag_main import AIRetrievalPipeline
from src.llm.llm_manager import LlmManager

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def _print_evidence_section(section_name: str, section: Dict[str, Any]) -> None:
    """
    打印证据区的预算、条目与候选信息，用于验证预算裁剪与去重效果。
    """
    items = section.get("items", [])
    candidates = section.get("candidates", [])
    used_chars = section.get("used_chars", 0)
    budget_chars = section.get("budget_chars", 0)

    print(f"\n📦 {section_name} 预算: {used_chars}/{budget_chars} chars")
    print(f"✅ {section_name} 已选条目数: {len(items)}")
    for idx, item in enumerate(items, 1):
        text = item.get("text", "")
        source = item.get("source", "")
        title = item.get("title", "")
        print(f"  {idx}. {title} | {source} | Length={len(text)}")

    print(f"🧾 {section_name} 候选条目数: {len(candidates)}")

def _run_rag_evidence_test(query: str) -> Dict[str, Any]:
    """
    执行 RAG 检索并输出证据预算与去重结果，验证新证据构建逻辑。
    """
    print(f"🔍 RAG 证据预算测试开始: {query}")

    # 1. 初始化 LLM
    llm_manager = LlmManager()
    llm = llm_manager.get_llm()

    # 2. 初始化 RAG Pipeline
    pipeline = AIRetrievalPipeline(llm)

    # 3. 执行检索
    result = pipeline.run(query)

    print("\n=====================================================================================")
    print("✅ RAG 证据预算测试完成")
    print(f"意图: {result['intent_info'].get('primary_intent')}")
    print(f"是否需要搜索: {result['needs_search']}")

    evidence = result.get("evidence", {})
    summary_section = evidence.get("summary", {})
    body_section = evidence.get("body", {})
    budget = evidence.get("budget", {})

    print("\n🎯 证据预算配置")
    print(f"  - summary_max_chars: {budget.get('summary_max_chars')}")
    print(f"  - body_max_chars: {budget.get('body_max_chars')}")

    _print_evidence_section("摘要区", summary_section)
    _print_evidence_section("正文区", body_section)

    print("\n🤖 AI 回答:")
    print(result.get("answer"))

    return result

if __name__ == "__main__":
    # 测试代码
    test_query = "东京三日游攻略有哪些值得参考的行程安排？"
    if len(sys.argv) > 1:
        test_query = sys.argv[1]
    _run_rag_evidence_test(test_query)
