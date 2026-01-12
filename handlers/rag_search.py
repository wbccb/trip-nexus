import logging
import sys
import os

# 将项目根目录添加到 python path，以便能找到 src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.rag.rag_main import AIRetrievalPipeline
from src.llm.llm_manager import LlmManager

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def search_handler(query: str):
    """
    处理搜索请求的Handler
    """
    print(f"🔍 正在处理搜索请求: {query}")
    
    # 1. 初始化 LLM
    llm_manager = LlmManager()
    llm = llm_manager.get_llm()
    
    # 2. 初始化 RAG Pipeline
    pipeline = AIRetrievalPipeline(llm)
    
    # 3. 执行搜索
    try:
        result = pipeline.run(query)

        print("\n=====================================================================================")

    
        print("\n✅ 搜索流程执行完毕，下面是总结!")
        print(f"意图: {result['intent_info'].get('primary_intent')}")
        print(f"是否需要搜索: {result['needs_search']}")
        
        if result['needs_search']:
            print(f"\n🔗 搜索结果 ({len(result['search_results'])}):")
            for r in result['search_results'][:3]:
                print(f"  - {r['title']} ({r['url']})")
                
            print(f"\n🕷️ 抓取页面 ({len(result.get('crawled_contents', []))}):")
            for c in result.get('crawled_contents', []):
                print(f"  - {c['title']} (Length: {len(c['content'])})")
                
            print("\n🤖 AI 回答:")
            print(result['answer'])
        else:
            print("\n🤖 AI 直接回答:")
            print(result['answer'])
            
        return result
        
    except Exception as e:
        print(f"❌ 搜索过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    # 测试代码
    test_query = "杭州现在的天气怎么样？适合穿什么衣服？"
    if len(sys.argv) > 1:
        test_query = sys.argv[1]
    
    search_handler(test_query)
