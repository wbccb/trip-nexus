import streamlit as st
import folium
from streamlit_folium import st_folium
from typing import Optional, Dict, List, Any


class TripUI:
    def __init__(self):
        if not st._is_running_with_streamlit:
            raise RuntimeError("必须通过 'streamlit run' 启动应用")
        st.set_page_config(page_title="TripNexus", layout="wide")
        self._init_session_state()

    def _init_session_state(self) -> None:
        """初始化会话状态，兼容Streamlit 1.50.0"""
        required_keys = {"trip_data", "map_obj", "edit_cmd"}
        for key in required_keys:
            if key not in st.session_state:
                st.session_state[key] = None

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
        """展示行程结果和地图，适配Streamlit 1.50.0"""
        st.subheader("📅 AI生成行程", divider="blue")
        daily_plan: Dict[str, List[Dict[str, str]]] = trip_data["daily_plan"]

        for day, items in daily_plan.items():
            with st.expander(f"第{day}天", expanded=True):
                for idx, item in enumerate(items):
                    cols = st.columns([1, 3, 2])
                    cols[0].write(f"⏰ {item['time']}")
                    cols[1].write(f"📍 **{item['attraction']}**")
                    cols[2].write(f"🚗 {item['transport']}")
                    with cols[1].expander("详情"):
                        st.write(f"地址：{item['address']}")
                        st.write(f"停留：{item['duration']}")
                st.divider()

        st.subheader("🗺️ 行程地图", divider="blue")
        if st.session_state.map_obj:
            st_folium(
                st.session_state.map_obj,
                width=1000,
                height=600,
                returned_objects=[]
            )

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