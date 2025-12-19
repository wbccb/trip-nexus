import streamlit as st
from src.frontend.main_ui import TripUI
from src.rag.rag_main import AIRetrievalPipeline
from src.llm.llm_manager import LlmManager
from src.map.map_renderer import TripMap
from __init__ import __version__, __description__
from src.config import Config

def main() -> None:
    config = Config()
    map_renderer = TripMap()
    llm_manager = LlmManager()
    # rag = AIRetrievalPipeline(llm)
    ui = TripUI(llm_manager, config)

    # 版本信息
    st.sidebar.markdown(f"### 📱 版本: v{__version__}")
    st.sidebar.markdown(f"ℹ️ {__description__}")
    st.sidebar.markdown("---")

    user_id = "temp"
    device_id = "11"
    ui.render_main_interface(user_id, device_id);
    #
    # # 获取用户输入
    # user_input: Optional[Dict[str, Any]] = ui.render_input_form()
    # if not user_input:
    #     st.info("请填写旅行信息并点击生成按钮")
    #     return
    #
    # # 1. 加载攻略到RAG
    # with st.spinner("📥 正在解析攻略信息..."):
    #     rag.load_and_store_guides(user_input["guide_links"])
    #
    # # 2. 检索相关攻略
    # query: str = (
    #     f"{user_input['destination']}{user_input['days']}天旅游，"
    #     f"预算{user_input['budget']}元，偏好{user_input['preference']}"
    # )
    # context: List[str] = rag.retrieve_relevant_info(query)
    # for contextItem in context:
    #     print(f"当前检索出来的文本: {contextItem}")
    #
    # # 3. 生成/修改行程
    # edit_cmd = ui.render_edit_controls()
    # if edit_cmd and edit_cmd["type"] != "无":
    #     with st.spinner("🔄 正在更新行程..."):
    #         trip_data = llm_manager.generate_trip(user_input, context, edit_cmd)
    # else:
    #     with st.spinner("🧠 AI正在规划行程..."):
    #         trip_data = llm_manager.generate_trip(user_input, context)
    #
    # if not trip_data:
    #     st.error("❌ 行程生成失败，请检查输入或更换攻略链接")
    #     return
    # st.session_state.trip_data = trip_data
    # print(f"================================================================================================")
    # print(f"行程生成: {trip_data}")
    # print(f"================================================================================================")
    #
    # # 4. 生成地图
    # with st.spinner("🗺️ 正在绘制行程地图..."):
    #     map_obj = map_renderer.render_map(trip_data)
    #     st.session_state.map_obj = map_obj
    #
    # # 5. 展示结果
    # ui.render_trip_result(trip_data)

if __name__ == "__main__":
    main()