import logging
import json
import base64
from datetime import datetime, time
from typing import Optional, Dict, List, Any

import streamlit as st
from streamlit.components.v1 import html

from src.config import Config
from src.frontend.chat_stream_renderer import ChatStreamRenderer, build_chat_html
from src.frontend.context.conversation_manager import ConversationManager
from src.frontend.context.entity import Message, MessageType
from src.frontend.context.storage import get_conversation_storage
from src.llm.llm_manager import LlmManager
from src.map.map_renderer import TripMap
from src.utils.console import console_log

class UIManager:
    def __init__(self, llm_manager: LlmManager, config: Config, map_renderer: TripMap | None = None):
        st.set_page_config(page_title="TripNexus", layout="wide")
        self._init_session_state()
        self.llm_manager = llm_manager
        self.conversation_storage = get_conversation_storage(config)
        self.conversation_manager = ConversationManager(conversation_storage=self.conversation_storage, llm_manager=llm_manager)
        self.map_renderer = map_renderer or TripMap()
        # 使用独立的流式渲染工具类，降低 UIManager 体积并复用逻辑
        self.chat_stream_renderer = ChatStreamRenderer(llm_manager)

    def _init_session_state(self) -> None:
        """初始化会话状态"""
        required_keys = {"trip_data", "map_obj", "edit_cmd", "current_conversation_id", "map_visible"}
        for key in required_keys:
            if key not in st.session_state:
                st.session_state[key] = None

        required_array_keys = {"chat_history"}
        for key in required_array_keys:
            if key not in st.session_state:
                st.session_state[key] = []

        if "map_visible" not in st.session_state or st.session_state["map_visible"] is None:
            st.session_state["map_visible"] = True

        if "llm_config" not in st.session_state:
            st.session_state.llm_config = {
                "provider": "ollama",
                "base_url": "http://localhost:11434",
                "model_name": "deepseek-r1:7b",
                "api_key": "",
                "temperature": 0.7,
                "analysis_provider": "ollama",
                "analysis_base_url": "http://localhost:11434",
                "analysis_model_name": "deepseek-r1:7b",
                "analysis_api_key": "",
                "analysis_temperature": 0.7,
                "generation_provider": "ollama",
                "generation_base_url": "http://localhost:11434",
                "generation_model_name": "deepseek-r1:7b",
                "generation_api_key": "",
                "generation_temperature": 0.7,
            }

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
                    # 数据有效性检查：如果 item 为 None，跳过
                    if not item:
                        continue
                        
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

        if not st.session_state.get("map_obj") and self.map_renderer:
            with st.spinner("地图生成中，请稍候..."):
                st.session_state.map_obj = self.map_renderer.render_map(trip_data)

        if st.session_state.map_obj:
            html(
                st.session_state.map_obj._repr_html_(),
                height=600,
                width=1000,
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

    def render_llm_settings(self) -> None:
        config = st.session_state.get("llm_config", {})
        provider_options = ["ollama", "openai_compatible"]

        analysis_provider_current = config.get("analysis_provider", "ollama")
        analysis_provider_index = provider_options.index(analysis_provider_current) if analysis_provider_current in provider_options else 0
        analysis_provider = st.selectbox("第 1 次调用-模型提供方（实体抽取+意图识别）", provider_options, index=analysis_provider_index)
        analysis_base_url_default = config.get("analysis_base_url") or ("http://localhost:11434" if analysis_provider == "ollama" else "")
        analysis_model_default = config.get("analysis_model_name") or ("deepseek-r1:7b" if analysis_provider == "ollama" else "")
        analysis_base_url = st.text_input("第 1 次调用-Base URL", analysis_base_url_default)
        analysis_model_name = st.text_input("第 1 次调用-模型名称", analysis_model_default)
        analysis_api_key_value = config.get("analysis_api_key") or ""
        if analysis_provider == "openai_compatible":
            analysis_api_key_value = st.text_input("第 1 次调用-API Key", analysis_api_key_value, type="password")
        analysis_temperature_value = float(config.get("analysis_temperature", 0.7))
        analysis_temperature = st.slider("第 1 次调用-温度", 0.0, 1.0, analysis_temperature_value, 0.05)

        generation_provider_current = config.get("generation_provider", "ollama")
        generation_provider_index = provider_options.index(generation_provider_current) if generation_provider_current in provider_options else 0
        generation_provider = st.selectbox("第 2 次调用-模型提供方（行程生成/修改）", provider_options, index=generation_provider_index)
        generation_base_url_default = config.get("generation_base_url") or ("http://localhost:11434" if generation_provider == "ollama" else "")
        generation_model_default = config.get("generation_model_name") or ("deepseek-r1:7b" if generation_provider == "ollama" else "")
        generation_base_url = st.text_input("第 2 次调用-Base URL", generation_base_url_default)
        generation_model_name = st.text_input("第 2 次调用-模型名称", generation_model_default)
        generation_api_key_value = config.get("generation_api_key") or ""
        if generation_provider == "openai_compatible":
            generation_api_key_value = st.text_input("第 2 次调用-API Key", generation_api_key_value, type="password")
        generation_temperature_value = float(config.get("generation_temperature", 0.7))
        generation_temperature = st.slider("第 2 次调用-温度", 0.0, 1.0, generation_temperature_value, 0.05)

        if st.button("应用 LLM 配置", use_container_width=True):
            st.session_state.llm_config = {
                "provider": generation_provider,
                "base_url": generation_base_url,
                "model_name": generation_model_name,
                "api_key": generation_api_key_value if generation_provider == "openai_compatible" else "",
                "temperature": generation_temperature,
                "analysis_provider": analysis_provider,
                "analysis_base_url": analysis_base_url,
                "analysis_model_name": analysis_model_name,
                "analysis_api_key": analysis_api_key_value if analysis_provider == "openai_compatible" else "",
                "analysis_temperature": analysis_temperature,
                "generation_provider": generation_provider,
                "generation_base_url": generation_base_url,
                "generation_model_name": generation_model_name,
                "generation_api_key": generation_api_key_value if generation_provider == "openai_compatible" else "",
                "generation_temperature": generation_temperature,
            }
            self.llm_manager.update_llm_config(st.session_state.llm_config)

    def render_chat_interface(self, user_id: str, device_id: str, session_id: str) -> None:
        """渲染聊天界面，支持多轮对话"""
        if not st.session_state.trip_data:
            trip_data = self.conversation_manager.conversationStorage.get_trip_data(session_id)
            if trip_data:
                st.session_state.trip_data = trip_data
        if "ai_processing" not in st.session_state:
            st.session_state.ai_processing = False
        chat_container = st.container()
        with chat_container:
            chat_placeholder = st.empty()

        with chat_container:
            chat_placeholder.markdown(
                build_chat_html(st.session_state.chat_history),
                unsafe_allow_html=True,
            )
            
        # 使用原生 chat_input 固定在底部
        if st.session_state.ai_processing:
            prompt = st.chat_input("AI处理中，暂时无法输入...", disabled=True)
        else:
            prompt = st.chat_input("告诉我您想如何调整行程？")

        if prompt:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            start_ts = datetime.now()
            print(f"[{ts}][UIManager] chat_submit start, session_id={session_id}, prompt_len={len(prompt)}")
            user_msg = {"role": MessageType.USER, "content": prompt, "timestamp": datetime.now().isoformat(), "metadata": {}}
            st.session_state.chat_history.append(user_msg)
            user_message_obj = Message.model_validate(user_msg)
            temp_loading = {
                "role": MessageType.ASSISTANT,
                "content": "",
                "timestamp": datetime.now().isoformat(),
                "metadata": {"loading": True},
            }
            loading_messages = st.session_state.chat_history + [temp_loading]
            st.session_state.ai_processing = True
            chat_placeholder.markdown(build_chat_html(loading_messages), unsafe_allow_html=True)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager] analyze_user_message start")
            intent_data = self.llm_manager.analyze_user_message(
                query=prompt,
                context=st.session_state.chat_history,
                current_trip=st.session_state.trip_data,
            )
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager] analyze_user_message end, intent={intent_data.get('intent')}")
            self.conversation_manager.process_new_message(
                user_id,
                device_id,
                user_message_obj,
                session_id,
                intent_data=intent_data,
            )
            intent_type = intent_data.get("intent", "general_conversation")
            response_stream = None
            trip_streaming_request = None
            if intent_type == "generate_trip":
                prepared_request = self.llm_manager.prepare_trip_request_from_intent(
                    intent_data,
                    st.session_state.chat_history,
                )
                if prepared_request.get("needs_more_info"):
                    missing_info = prepared_request.get("missing_info", [])
                    response_data = {
                        "response": f"我需要更多信息才能为您生成行程。请提供以下信息：{', '.join(missing_info)}。例如：'我想去成都玩3天，预算5000元'",
                        "trip_data": None,
                    }
                else:
                    trip_streaming_request = prepared_request
                    response_stream = self.llm_manager.stream_trip_generation(
                        prepared_request.get("user_input") or {},
                        prepared_request.get("context_texts") or [],
                    )
                    response_data = {
                        "response": "",
                        "trip_data": None,
                    }
            elif intent_type in ["modify_trip", "add_attraction", "delete_attraction", "reorder_trip"]:
                if st.session_state.trip_data:
                    response_data = self.llm_manager._handle_trip_modification(intent_data, st.session_state.trip_data, st.session_state.chat_history)
                else:
                    response_data = {
                        "response": "我需要先为您生成一个基础行程，然后才能进行调整。请先提供目的地、天数和预算信息。",
                        "trip_data": None,
                    }
            else:
                response_stream = self.llm_manager.stream_chat_response(
                    prompt,
                    st.session_state.chat_history,
                    st.session_state.trip_data,
                )
                response_data = {
                    "response": "",
                    "trip_data": None,
                }
            if isinstance(response_data, dict) and "response" in response_data:
                chat_response = response_data["response"]
            else:
                chat_response = str(response_data)
            trip_data = None
            if response_stream is not None:
                chat_response = self.chat_stream_renderer.render_stream_response(
                    chat_response,
                    st.session_state.chat_history,
                    chat_placeholder,
                    response_stream=response_stream,
                )
                if trip_streaming_request is not None:
                    trip_data = self.llm_manager.parse_trip_from_response_text(chat_response)
                    if trip_data:
                        st.session_state.trip_data = trip_data
                        st.session_state.map_obj = None
                        self.conversation_manager.conversationStorage.store_trip_data(session_id, trip_data)
            else:
                self.chat_stream_renderer.render_stream_response(
                    chat_response,
                    st.session_state.chat_history,
                    chat_placeholder,
                    response_stream=None,
                )
                if isinstance(response_data, dict) and "trip_data" in response_data:
                    trip_data = response_data["trip_data"]
                    if trip_data:
                        st.session_state.trip_data = trip_data
                        st.session_state.map_obj = None
                        self.conversation_manager.conversationStorage.store_trip_data(session_id, trip_data)
            assistant_msg = {
                "role": MessageType.ASSISTANT,
                "content": chat_response,
                "timestamp": datetime.now().isoformat(),
                "metadata": {
                    "context_type": "trip_modification",
                    "conversation_id": st.session_state.current_conversation_id,
                    "has_trip_data": bool(trip_data),
                    "trip_data": trip_data
                }
            }
            st.session_state.chat_history.append(assistant_msg)
            assistant_message_obj = Message.model_validate(assistant_msg)
            # AI消息进行处理：主要是压缩多轮对话消息 + 存储会话信息到数据库中
            self.conversation_manager.process_new_message(user_id, device_id, assistant_message_obj, session_id)
            total_cost = (datetime.now() - start_ts).total_seconds()
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager] chat_submit end, total_cost={total_cost:.2f}s")
            # 显示AI消息到界面中
            chat_placeholder.markdown(build_chat_html(st.session_state.chat_history), unsafe_allow_html=True)
            st.session_state.ai_processing = False
            st.rerun()
            if trip_data:
                st.divider()
                st.markdown("### 🎯 为您生成的行程方案")
                self._display_trip_in_chat(trip_data)
                st.success("✨ 行程已生成！右侧地图和详细安排已更新。")

    def render_map_panel(self) -> None:
        st.subheader("🗺️ 行程地图", divider="blue")

        if st.session_state.get("map_visible") is None:
            st.session_state.map_visible = True

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}][UIManager] render_map_panel called. visible={st.session_state.map_visible}, has_trip={bool(st.session_state.get('trip_data'))}, has_map_obj={bool(st.session_state.get('map_obj'))}")

        # 3. 处理隐藏逻辑
        if not st.session_state.map_visible:
            if st.button("显示地图", key="show_map_button"):
                st.session_state.map_visible = True
                st.rerun()  # 立即刷新
            return
        
        # 4. 处理显示逻辑
        if st.button("隐藏地图", key="hide_map_button"):
            st.session_state.map_visible = False
            st.rerun()  # 立即刷新
            return

        trip_data = st.session_state.get("trip_data")
        if not trip_data:
            st.info("暂无行程数据，生成行程后将显示地图。")
            return
        
        # 5. 渲染地图对象（如果不存在则生成）
        if not st.session_state.get("map_obj") and self.map_renderer:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}][UIManager] map_obj missing, generating new map...")
            try:
                with st.spinner("地图生成中，请稍候..."):
                    st.session_state.map_obj = self.map_renderer.render_map(trip_data)
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{ts}][UIManager] map generated successfully")
            except Exception as e:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{ts}][UIManager] map generation failed: {e}")
                st.error(f"地图生成失败: {str(e)}")
                return

        # 6. 显示地图组件
        if st.session_state.get("map_obj"):
            print("[MapDebug] rendering map via html component")
            html(
                st.session_state.map_obj._repr_html_(),
                height=600,
                width=1000,
            )

    def _build_map_sidebar_html(self, map_html_content: str) -> str:
        b64_html = base64.b64encode(map_html_content.encode("utf-8")).decode("utf-8")
        sidebar_html = f"""
            <div style="
                position: fixed;
                top: 60px;
                right: 0;
                width: 40%;
                height: calc(100vh - 60px);
                background-color: white;
                box-shadow: -4px 0 10px rgba(0,0,0,0.1);
                z-index: 999999;
                border-left: 1px solid #e0e0e0;
                display: flex;
                flex-direction: column;
            ">
                <div style="
                    padding: 12px 16px;
                    background: #f8f9fa;
                    border-bottom: 1px solid #eee;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    flex-shrink: 0;
                    height: 50px;
                ">
                    <span style="font-weight: 600; font-size: 16px; color: #333;">🗺️ 行程地图</span>
                    <span style="width: 40px;"></span>
                </div>
                <div style="flex: 1; width: 100%; position: relative;">
                    <iframe src="data:text/html;charset=utf-8;base64,{b64_html}" 
                            style="width: 100%; height: 100%; border: none;">
                    </iframe>
                </div>
            </div>
        """
        return sidebar_html

    def _display_trip_in_chat(self, trip_data: Dict[str, Any]):
        """在聊天界面中显示格式化的行程"""
        if not trip_data:
            return

        # 生成Markdown格式
        trip_markdown = self._format_trip_as_markdown(trip_data)

        # 使用expander折叠详细行程，避免聊天界面过长
        with st.expander("🗺️ 查看详细行程安排", expanded=False):
            st.markdown(trip_markdown)

        # 添加快速操作按钮
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("🔄 重新生成", key=f"regen_{datetime.now().timestamp()}"):
                # TODO 待实现
                st.rerun()
        with col2:
            if st.button("✏️ 修改行程", key=f"edit_{datetime.now().timestamp()}"):
                # TODO 待实现
                st.rerun()

    def _format_trip_as_markdown(self, trip_data: Dict[str, Any]) -> str:
        """将行程数据转换为美观的Markdown格式"""
        if not trip_data:
            return "❌ 无行程数据可显示"

        try:
            destination = trip_data.get("destination", "未知目的地")
            days = trip_data.get("days", 0)
            daily_plan = trip_data.get("daily_plan", {})

            # 开始构建Markdown
            markdown_content = []

            # 1. 行程概览卡片
            markdown_content.append("### 🌟 行程概览")
            markdown_content.append(f"**目的地**: {destination}")
            markdown_content.append(f"**行程天数**: {days}天")
            markdown_content.append(f"**总景点数**: {len(daily_plan) if isinstance(daily_plan, dict) else 0}个")
            markdown_content.append("")

            # 2. 按天数分组显示行程
            markdown_content.append("### 📅 详细行程安排")

            # 按天数排序（如果键是数字字符串）
            if isinstance(daily_plan, list):
                # 如果是列表，将其转换为字典格式处理，或者直接循环列表索引
                daily_plan_dict = {str(i + 1): item for i, item in enumerate(daily_plan)}
                sorted_days = sorted(daily_plan_dict.keys(), key=int)
                plan_to_process = daily_plan_dict
            else:
                # 如果已经是字典
                sorted_days = sorted(daily_plan.keys(), key=lambda x: int(x) if x.isdigit() else 999)
                plan_to_process = daily_plan

            for day_key in sorted_days:
                day_plan = plan_to_process[day_key]

                # 处理不同的数据结构（可能是单个字典或列表）
                items = []
                if isinstance(day_plan, list):
                    items = day_plan
                elif isinstance(day_plan, dict):
                    # 检查是否是单个行程项
                    if "attraction" in day_plan:
                        items = [day_plan]
                    else:
                        # 可能是按时间分组的字典
                        items = list(day_plan.values())

                if not items:
                    continue

                # 按时间排序
                items.sort(key=lambda x: x.get("time", "99:99"))

                markdown_content.append(f"#### 🗓️ 第 {day_key} 天")
                markdown_content.append("")

                # 创建每日行程表格
                table_rows = []
                table_rows.append("| 时间 | 景点 | 交通 | 持续时间 |")
                table_rows.append("|------|------|------|----------|")

                for item in items:
                    time = item.get("time", "⏰ 未知时间")
                    attraction = item.get("attraction", "📍 未知景点")
                    address = item.get("address", "🏠 地址未提供")
                    transport = item.get("transport", "🚗 交通未提供")
                    duration = item.get("duration", "⏱️ 时长未提供")

                    # 创建带工具提示的表格行
                    row = f"| **{time}** | **{attraction}**<br><small>{address}</small> | {transport} | {duration} |"
                    table_rows.append(row)

                markdown_content.extend(table_rows)
                markdown_content.append("")

            # 3. 添加总结和提示
            markdown_content.append("### 💡 旅行小贴士")
            markdown_content.append("- 🗺️ **地图导航**: 右侧地图已标记所有景点位置，点击可查看详情")
            markdown_content.append("- ⏰ **时间建议**: 请根据实际交通情况预留缓冲时间")
            markdown_content.append("- 💰 **预算分配**: 建议每日餐饮预算 200-300 元，交通 50-100 元")
            markdown_content.append("- 📱 **实用工具**: 可使用高德/百度地图导航，大众点评查找美食")

            return "\n".join(markdown_content)

        except Exception as e:
            print(f"❌ 行程格式化失败: {str(e)}")
            return f"❌ 行程数据格式错误，无法显示。错误: {str(e)}"

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
        st.markdown(
            """
            <style>
            [data-testid="stChatMessage"] { margin-bottom: 12px; }
            </style>
            """,
            unsafe_allow_html=True,
        )
        with st.sidebar:
            current_session_id = self.render_session_list(user_id, device_id)
            if current_session_id is None:
                sessions = self.conversation_storage.get_session_list(user_id)
                if sessions:
                    first_session_id = sessions[0]["session_id"]
                    st.session_state.current_conversation_id = first_session_id
                    short_term_list = self.conversation_storage.get_short_term_context(first_session_id)
                    if not short_term_list:
                        session_chat_list = self.conversation_storage.get_session_chat_list(first_session_id)
                        short_term_list = [json.loads(msg_str) for msg_str in session_chat_list]
                    st.session_state.chat_history = short_term_list
                    st.rerun()
                else:
                    new_session_id = self.conversation_storage.generate_session_id(user_id, device_id)
                    self.conversation_storage.store_session(user_id, session_id=new_session_id)
                    st.session_state.current_conversation_id = new_session_id
                    st.session_state.chat_history = []
                    st.rerun()

            st.markdown("---")
            st.subheader("LLM 设置")
            self.render_llm_settings()

        # 预加载 trip_data，确保界面渲染前数据已就绪
        if st.session_state.current_conversation_id and not st.session_state.get("trip_data"):
            trip_data = self.conversation_storage.get_trip_data(st.session_state.current_conversation_id)
            if trip_data:
                st.session_state.trip_data = trip_data

        if st.session_state.get("trip_data"):
            st.markdown(
                """
                <style>
                div.element-container:has(div#map-toggle-marker) + div.element-container {
                    position: absolute;
                    top: 8px;
                    left: 20px;
                    z-index: 1000001;
                    width: auto !important;
                }
                </style>
                <div id="map-toggle-marker"></div>
                """,
                unsafe_allow_html=True,
            )
            is_map_visible = st.session_state.get("map_visible", False)
            if st.button(
                label="隐藏地图" if is_map_visible else "显示地图",
                key="toggle_map_toolbar_btn",
                help="切换地图显示/隐藏",
                type="secondary"
            ):
                st.session_state.map_visible = not is_map_visible
                st.rerun()

        # 始终渲染聊天界面（占满全宽，不被挤压）
        self.render_chat_interface(user_id, device_id, st.session_state.current_conversation_id)

        # 浮动显示右侧地图（悬浮层）
        map_visible = st.session_state.get("map_visible", False)
        has_trip_data = bool(st.session_state.get("trip_data"))
        print(f"[DEBUG] Map render check: visible={map_visible}, has_data={has_trip_data}")
        
        if map_visible and has_trip_data:
            # 渲染地图内容
            trip_data = st.session_state.get("trip_data")
            map_placeholder = st.empty()
            map_rendered_inline = False
            st.markdown("""
                <style>
                div.element-container:has(div#close-map-marker) + div.element-container {
                    position: fixed !important;
                    top: 64px;
                    right: 20px;
                    z-index: 1000001;
                    width: auto !important;
                }
                div.element-container:has(div#close-map-marker) + div.element-container button {
                    background-color: transparent;
                    border: none;
                    color: #666;
                    font-size: 16px;
                    padding: 4px 12px;
                    line-height: 1;
                }
                div.element-container:has(div#close-map-marker) + div.element-container button:hover {
                    color: #333;
                    border: none;
                    background-color: rgba(0,0,0,0.05);
                }
                div.element-container:has(div#close-map-marker) + div.element-container button:focus {
                    border: none;
                    outline: none;
                    box-shadow: none;
                }
                </style>
                <div id="close-map-marker"></div>
            """, unsafe_allow_html=True)
            
            if st.button("✕", key="close_map_overlay_btn", help="关闭地图"):
                st.session_state.map_visible = False
                st.rerun()
            if not st.session_state.get("map_obj") and self.map_renderer:
                 print("[DEBUG] Generating map object...")
                 with st.spinner("地图生成中..."):
                    try:
                        last_event = None
                        for event in self.map_renderer.render_map_batches(trip_data, batch_size=4):
                            map_html_content = event.get("html") or ""
                            if map_html_content:
                                sidebar_html = self._build_map_sidebar_html(map_html_content)
                                map_placeholder.markdown(sidebar_html, unsafe_allow_html=True)
                                map_rendered_inline = True
                            last_event = event
                            if not event.get("is_final"):
                                time.sleep(0.25)
                        if last_event and last_event.get("map_obj"):
                            st.session_state.map_obj = last_event.get("map_obj")
                            print("[DEBUG] Map object generated successfully")
                        elif not st.session_state.get("map_obj"):
                            st.session_state.map_obj = self.map_renderer.render_map(trip_data)
                            print("[DEBUG] Map object generated successfully")
                    except Exception as e:
                        print(f"[DEBUG] Map generation failed: {e}")
                        st.error(f"地图生成失败: {str(e)}")
            
            if st.session_state.get("map_obj") and not map_rendered_inline:
                try:
                    # 2. 获取地图的完整 HTML 字符串
                    map_html_content = st.session_state.map_obj.get_root().render()
                    print(f"[DEBUG] Map HTML generated, length: {len(map_html_content)}")
                    
                    sidebar_html = self._build_map_sidebar_html(map_html_content)
                    print("[DEBUG] Injecting map sidebar HTML")
                    map_placeholder.markdown(sidebar_html, unsafe_allow_html=True)
                except Exception as e:
                    print(f"[DEBUG] Error rendering map sidebar: {e}")
                    st.error(f"地图渲染错误: {e}")
            else:
                print("[DEBUG] No map object available to render")

    def render_session_list(self, user_id: str, device_id: str) -> None:
        """绘制左侧的会话列表（侧边栏内）"""
        if st.button("新建会话", use_container_width=True):
            console_log("新建会话:"+user_id , device_id)
            new_session_id = self.conversation_storage.generate_session_id(user_id, device_id)
            console_log("新建会话 new_session_id", new_session_id)
            self.conversation_storage.store_session(user_id, session_id=new_session_id)
            st.session_state.current_conversation_id = new_session_id
            st.session_state.chat_history = []
            st.rerun()
        sessions = self.conversation_storage.get_session_list(user_id)
        if not sessions:
            st.info("暂无会话记录")
            return None
        for idx, session in enumerate(sessions):
            session_id = session["session_id"]
            name = session["name"]
            col1, col2 = st.columns([8, 2])
            with col1:
                if st.button(
                        f"{name}",
                        key=f"session_{idx}_{session_id}",
                        use_container_width=True,
                        type="primary" if session_id == st.session_state.current_conversation_id else "secondary"
                ):
                    st.session_state.current_conversation_id = session_id
                    short_term_list = self.conversation_storage.get_short_term_context(session_id)
                    if not short_term_list:
                        session_chat_list = self.conversation_storage.get_session_chat_list(session_id)
                        short_term_list = [json.loads(msg_str) for msg_str in session_chat_list]
                    st.session_state.chat_history = short_term_list
                    st.rerun()
            with col2:
                if st.button(
                        "🗑️",
                        key=f"delete_{idx}_{session_id}",
                        use_container_width=True,
                        help="删除该会话"
                ):
                    self.conversation_storage.delete_session(session_id=session_id)
                    if session_id == st.session_state.current_conversation_id:
                        st.session_state.current_conversation_id = None
                        st.session_state.chat_history = []
                    st.rerun()
        return st.session_state.current_conversation_id
