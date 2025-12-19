import streamlit as st
from streamlit_folium import st_folium
from typing import Optional, Dict, List, Any
from datetime import datetime

from src.frontend.context.conversation_manager import ConversationManager
from src.frontend.context.storage import get_conversation_storage
from src.frontend.context.entity import Message
from src.llm.llm_manager import LlmManager
from src.config import Config

class UIManager:
    def __init__(self, llm_manager: LlmManager, config: Config):
        st.set_page_config(page_title="TripNexus", layout="wide")
        self._init_session_state()
        self.llm_manager = llm_manager
        self.conversation_storage = get_conversation_storage(config)
        self.conversation_manager = ConversationManager(conversation_storage=self.conversation_storage)

    def _init_session_state(self) -> None:
        """初始化会话状态"""
        required_keys = {"trip_data", "map_obj", "edit_cmd", "current_conversation_id"}
        for key in required_keys:
            if key not in st.session_state:
                st.session_state[key] = None

        required_array_keys = {"chat_history"}
        for key in required_array_keys:
            if key not in st.session_state:
                st.session_state[key] = []

    def render_input_form(self) -> Optional[Dict[str, Any]]:
        """渲染输入表单，返回结构化参数"""
        with st.form("trip_form", clear_on_submit=False):
            col1, col2 = st.columns(2)
            with col1:
                destination: str = st.text_input("目的地", "成都")
                days: int = st.slider("旅行天数", 1, 10, 3)
            with col2:
                budget: int = st.slider("预算（元/人）", 1000, 20000, 5000)
                preference: List[str] = st.multiselect(
                    "偏好", ["美食", "历史", "自然", "购物", "亲子"]
                )
            guide_links: str = st.text_area(
                "攻略链接（每行一个）",
                "https://www.mafengwo.cn/i/23884996.html"
            )
            submit: bool = st.form_submit_button("生成行程")

        if submit:
            return {
                "destination": destination,
                "days": days,
                "budget": budget,
                "preference": preference,
                "guide_links": [link.strip() for link in guide_links.split("\n") if link.strip()]
            }
        return None

    def render_trip_result(self, trip_data: Dict[str, Any]) -> None:
        """
        展示行程结果和地图，适配Streamlit。
        优化点：1. 适应LLM输出的错误结构；2. 增加数据有效性检查；3. 改进布局。
        """
        st.subheader("📅 AI生成行程", divider="blue")

        # ------------------ 1. 数据结构适配与检查 ------------------
        raw_daily_plan = trip_data.get("daily_plan")

        if not raw_daily_plan:
            st.error("行程数据缺失或为空，请尝试重新生成。")
            return

        daily_plans_grouped: Dict[str, List[Dict[str, str]]]

        # 适配结构：如果 LLM 输出的是平铺列表，将其包装为 Day 1
        if isinstance(raw_daily_plan, list):
            st.warning("模型输出结构异常（缺少日期分组），已自动将所有行程归为第 1 天。")
            daily_plans_grouped = {"1": raw_daily_plan}
        elif isinstance(raw_daily_plan, dict):
            daily_plans_grouped = raw_daily_plan
        else:
            st.error(f"无法解析行程结构，预期为字典或列表，实际为 {type(raw_daily_plan)}。")
            return

        # ------------------ 2. 行程展示逻辑 ------------------

        # 使用 sorted(daily_plans_grouped.items()) 确保日期按数字顺序显示，
        # 即使键是字符串 '1', '10' 等也能正确排序（需确保键可转为整数）。
        try:
            sorted_plans = sorted(daily_plans_grouped.items(), key=lambda x: int(x[0]))
        except ValueError:
            # 如果日期键无法转为整数，则按字符串排序
            sorted_plans = sorted(daily_plans_grouped.items())

        for day_str, items in sorted_plans:
            # 使用 Markdown 标题提升日期视觉层级
            st.markdown(f"#### 第 {day_str} 天")

            # 使用 st.container() 代替 st.expander() 避免点击展开/收起造成不必要的交互
            with st.container(border=True):
                for idx, item in enumerate(items):
                    # 确保关键字段存在，避免 KeyError
                    time = item.get('time', '未知时间')
                    attraction = item.get('attraction', '未知景点')
                    transport = item.get('transport', '未知交通')
                    address = item.get('address', '地址缺失')
                    duration = item.get('duration', '时长缺失')

                    # 使用 st.columns 提升布局
                    cols = st.columns([1, 4, 1.5])  # 调整比例，给景点更多空间

                    # 时间
                    cols[0].markdown(f"**⏰ {time}**", help=f"停留：{duration}")

                    # 景点和详情
                    cols[1].markdown(f"**📍 {attraction}**")

                    # 交通
                    cols[2].markdown(f"🚗 {transport}")

                    # 使用 st.caption 展示地址，避免使用嵌套的 expander
                    cols[1].caption(f"地址：{address}")

                    st.divider()  # 行程项之间增加分割线

        # ------------------ 3. 地图展示逻辑 ------------------
        st.subheader("🗺️ 行程地图", divider="blue")

        # 检查地图对象是否存在，并在不存在时尝试生成
        if not st.session_state.get('map_obj'):
            st.session_state.map_obj = self.render_map(trip_data)

        if st.session_state.map_obj:
            st_folium(
                st.session_state.map_obj,
                width=1000,
                height=600,
                # 优化：返回对象可以帮助调试，通常设置为 'all'，这里保持 []
                returned_objects=[],
                key="trip_map"  # 明确设置key
            )
        else:
            st.error("无法生成地图，请检查地址解析服务是否正常。")

    def render_edit_controls(self) -> Optional[Dict[str, Any]]:
        """行程修改控件"""
        if not st.session_state.trip_data:
            return None

        with st.sidebar:
            st.subheader("✏️ 修改行程")
            edit_type: str = st.selectbox("操作类型", ["无", "添加景点", "删除景点", "调整顺序"])

            match edit_type:
                case "添加景点":
                    attraction: str = st.text_input("景点名称")
                    day: int = st.number_input(
                        "添加到第几天",
                        min_value=1,
                        max_value=len(st.session_state.trip_data["daily_plan"]),
                        value=1
                    )
                    if st.button("确认添加"):
                        return {"type": "add", "attraction": attraction, "day": day}
                case "删除景点":
                    day: int = st.number_input("删除第几天的景点", 1, len(st.session_state.trip_data["daily_plan"]), 1)
                    attractions = [item["attraction"] for item in st.session_state.trip_data["daily_plan"][str(day)]]
                    selected = st.selectbox("选择景点", attractions)
                    if st.button("确认删除"):
                        return {"type": "delete", "attraction": selected, "day": day}
                case "调整顺序":
                    return {"type": "reorder", "msg": "调整顺序需重新生成行程"}
                case _:
                    return None

    def render_chat_interface(self, user_id: str, device_id: str) -> None:
        """渲染聊天界面，支持多轮对话"""
        st.sidebar.subheader("💬 行程对话助手")

        # 使用st.chat_message构建聊天界面
        chat_container = st.container()

        with chat_container:
            # 显示对话历史
            for message in st.session_state.chat_history:
                with st.chat_message(message["role"]):
                    # 显示消息内容
                    st.markdown(message["content"])
                    # 显示元数据（如果有）
                    if "metadata" in message:
                        st.caption(f"上下文: {message['metadata'].get('context_type', '无')}")
                    # 显示错误标记（如果有）
                    if message.get("error", False):
                        st.caption("⚠️ 消息处理出错")

        # 用户输入
        if prompt := st.chat_input("告诉我您想如何调整行程？"):
            # 3.1 添加用户消息到历史
            user_msg = {
                "role": "user",
                "content": prompt,
                "timestamp": datetime.now().isoformat()
            }
            st.session_state.chat_history.append(user_msg)

            # 3.2 即时显示用户消息（也可以依赖历史渲染，但这里即时显示更流畅）
            with chat_container:  # 注意：这里用chat_container包裹，确保消息在同一个容器中
                with st.chat_message(user_msg["role"]):
                    st.markdown(user_msg["content"])

            # 3.3 获取AI响应并显示（核心：追加到历史，而非覆盖）
            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                full_response = ""

                # try:
                # TODO 调用LLM获取响应（这里需要实现后端逻辑）
                full_response = self.llm_manager.change_trip(prompt)
                message_placeholder.markdown(full_response)

                # 3.4 添加AI响应到历史（实现递增的核心：历史列表追加）
                assistant_msg = {
                    "role": "assistant",
                    "content": full_response,
                    "timestamp": datetime.now().isoformat(),
                    "metadata": {
                        "context_type": "trip_modification",
                        "conversation_id": st.session_state.current_conversation_id
                    }
                }
                st.session_state.chat_history.append(assistant_msg)

                # 3.5 更新上下文
                message_obj = Message.model_validate(assistant_msg)
                self.conversation_manager.process_new_message(user_id, device_id, message_obj)

                # except Exception as e:
                #     error_msg = f"对话处理出错: {str(e)}"
                #     message_placeholder.error(error_msg)
                #     st.session_state.chat_history.append({
                #         "role": "assistant",
                #         "content": error_msg,
                #         "error": True
                #     })

    def _reset_conversation(self, user_id: str, device_id: str) -> None:
        """清空对话框的内容"""
        # 重置聊天历史为空列表
        st.session_state.clear()
        self._init_session_state()
        # 可选：重置会话ID（如果需要全新会话上下文）
        st.session_state.current_conversation_id = self.conversation_manager.generate_session_id(user_id, device_id)
        # 可选：清空后给出提示
        st.sidebar.success("对话已清空！")

    def render_main_interface(self, user_id: str, device_id: str) -> None:
        """渲染主界面，集成聊天和行程展示"""
        # 侧边栏：聊天控制
        with st.sidebar:
            st.header("🎯 行程助手")

            # 会话管理
            if st.button("🆕 新对话"):
                self._reset_conversation(user_id, device_id)

            # 显示最近会话
            # if hasattr(self, 'conversation_manager'):
            #     user_id = st.session_state.get('user_id', 'anonymous')
            #     messages: List[Message] = self.conversation_manager.get_user_conversations(user_id)
            #     if messages:
            #         st.subheader("📋 历史会话")
            #         for message in messages[:5]:  # 显示最近5个
            #             st.chat_message(message)

            st.divider()
            self.render_edit_controls()  # 保留原有的编辑控件

        # 主区域：聊天 + 行程
        # if st.session_state.trip_data:
        #     tab1, tab2 = st.tabs(["🗺️ 行程详情", "💬 对话调整"])
        #
        #     with tab1:
        #         self.render_trip_result(st.session_state.trip_data)

            # with tab2:
            #     self.render_chat_interface()
        # else:
        #     st.info("请先生成行程，然后可以使用对话功能进行调整")
        #     self.render_input_form()
        self.render_chat_interface(user_id, device_id)
