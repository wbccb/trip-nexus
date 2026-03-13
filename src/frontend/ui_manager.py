import logging
import json
import base64
import hashlib
import re
from datetime import datetime
import time as time_module
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
from src.frontend.agent_ui import AgentUI
from src.observability import ErrorCodes, normalize_exception, get_global_recorder

class UIManager:
    def __init__(self, llm_manager: LlmManager, config: Config, map_renderer: TripMap | None = None):
        """
        UI 管理器（Streamlit 页面入口）。

        v0.0.3 新增能力：
        - 侧边栏提供 Planner + Executor 的新 Agent Loop 调试面板；
        - 通过 EventBus/SnapshotStore 展示计划执行的事件与快照时间线。
        """

        try:
            st.set_page_config(page_title="TripNexus", layout="wide")
        except Exception:
            pass
        self.config = config
        self.llm_manager = llm_manager
        self._init_session_state()
        self.conversation_storage = get_conversation_storage(config)
        self.conversation_manager = ConversationManager(conversation_storage=self.conversation_storage, llm_manager=llm_manager)
        self.map_renderer = map_renderer or TripMap()
        # 使用独立的流式渲染工具类，降低 UIManager 体积并复用逻辑
        self.chat_stream_renderer = ChatStreamRenderer(llm_manager)
        # 初始化全局指标记录器，用于 UI 链路的观测打点
        self._metrics = get_global_recorder()
        self.agent_ui = AgentUI(
            llm_manager=self.llm_manager,
            metrics=self._metrics,
            render_rag_evidence_panel=self.render_rag_evidence_panel,
        )

    def _split_think_content(self, content: str) -> tuple[str, str]:
        if not content:
            return "", ""
        think_blocks = re.findall(r"<think>(.*?)</think>", content, flags=re.DOTALL)
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        cleaned = re.sub(r"</?think>", "", cleaned)
        cleaned = cleaned.strip()
        think_text = "\n\n".join([block.strip() for block in think_blocks if block and block.strip()])
        return cleaned, think_text

    def _log_llm_output(self, stage: str, content: str) -> None:
        safe_content = content or ""
        head_size = 180
        tail_size = 180
        if len(safe_content) <= head_size + tail_size + 5:
            preview = safe_content
        else:
            preview = f"{safe_content[:head_size]}....{safe_content[-tail_size:]}"
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager] llm_output stage={stage} len={len(safe_content)} preview={preview}")

    def _init_session_state(self) -> None:
        """
        初始化会话状态。

        关键点：
        - Streamlit 的 st.session_state 用于跨 rerun 保持交互状态；
        - Agent 调试能力需要保存 thread_id、上次 state、输入文本与配置项，
          以便用户多次点击“运行/继续/清空”时保持一致行为。
        """
        required_keys = {
            "trip_data",
            "map_obj",
            "edit_cmd",
            "current_conversation_id",
            "map_visible",
            "agent_thread_id",
            "agent_last_state",
            "agent_user_input",
            "agent_config",
            "rag_evidence_ui",
        }
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
            cfg = self.config
            st.session_state.llm_config = {
                "provider": cfg.GENERATION_PROVIDER,
                "base_url": cfg.GENERATION_BASE_URL,
                "model_name": cfg.GENERATION_MODEL_NAME,
                "api_key": cfg.GENERATION_API_KEY,
                "temperature": cfg.GENERATION_TEMPERATURE,
                "analysis_provider": cfg.ANALYSIS_PROVIDER,
                "analysis_base_url": cfg.ANALYSIS_BASE_URL,
                "analysis_model_name": cfg.ANALYSIS_MODEL_NAME,
                "analysis_api_key": cfg.ANALYSIS_API_KEY,
                "analysis_temperature": cfg.ANALYSIS_TEMPERATURE,
                "generation_provider": cfg.GENERATION_PROVIDER,
                "generation_base_url": cfg.GENERATION_BASE_URL,
                "generation_model_name": cfg.GENERATION_MODEL_NAME,
                "generation_api_key": cfg.GENERATION_API_KEY,
                "generation_temperature": cfg.GENERATION_TEMPERATURE,
            }
        if st.session_state.get("agent_thread_id") is None:
            st.session_state.agent_thread_id = ""
        if st.session_state.get("agent_last_state") is None:
            st.session_state.agent_last_state = {}
        if st.session_state.get("agent_user_input") is None:
            st.session_state.agent_user_input = ""
        if st.session_state.get("agent_config") is None:
            # Agent 配置是“控制平面”：决定编排链路是否启用某些节点，以及工具偏好参数等。
            st.session_state.agent_config = {
                "enable_checker": True,
                "enable_optimizer": True,
                "enable_rag": True,
                "budget_cap": None,
                "trip_density": "medium",
                "prefer_indoor": False,
                "poi_top_k": 5,
                "poi_query": "热门景点",
                "rag_top_k": 3,
                "weather_days": 3,
            }
        if st.session_state.get("agent_plan_preview") is None:
            # 计划预览缓存
            st.session_state.agent_plan_preview = None
        if st.session_state.get("agent_plan_intent") is None:
            # 计划对应的意图
            st.session_state.agent_plan_intent = ""
        if st.session_state.get("agent_plan_confirmed") is None:
            # 是否确认计划
            st.session_state.agent_plan_confirmed = False

        if st.session_state.get("rag_evidence_ui") is None:
            st.session_state.rag_evidence_ui = {}

    def _build_evidence_item_id(self, section: str, item: Dict[str, Any]) -> str:
        """
        为单条 Evidence 构建稳定 ID，便于在 Streamlit session_state 中保存交互状态。

        说明：
        - Evidence 可能来自联网搜索摘要或正文分块，字段并不完全一致；
        - 为了让“勾选/置顶/编辑”在 rerun 后仍能对齐同一条证据，这里将若干关键字段做哈希。
        """
        source = str(item.get("source") or "")
        title = str(item.get("title") or "")
        text = str(item.get("text") or "")
        raw = f"{section}||{source}||{title}||{text}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _get_rag_evidence_entries(self, evidence: Dict[str, Any], section: str) -> List[Dict[str, Any]]:
        """
        将 RAG evidence 的某个分区（summary/body）归一化为可渲染列表。

        设计目标：
        - 优先展示 candidates（可编辑、可筛选），以满足“手动编辑候选摘要”的需求；
        - 若 candidates 为空，则回退到 items（管线已选入上下文的证据）。
        """
        section_payload = evidence.get(section) or {}
        candidates = section_payload.get("candidates") or []
        items = section_payload.get("items") or []
        base_list = candidates if candidates else items
        if not isinstance(base_list, list):
            return []

        normalized: List[Dict[str, Any]] = []
        for it in base_list:
            if not isinstance(it, dict):
                continue
            normalized.append(
                {
                    "id": self._build_evidence_item_id(section, it),
                    "section": section,
                    "type": it.get("type") or section,
                    "source": it.get("source"),
                    "engine": it.get("engine"),
                    "title": it.get("title"),
                    "text": it.get("text") or "",
                    "confidence": it.get("confidence", it.get("score")),
                    "timestamp": it.get("timestamp"),
                    "_is_candidate": bool(candidates),
                }
            )
        return normalized

    def render_rag_evidence_panel(self, evidence: Dict[str, Any], panel_key: str, show_answer_block: bool = False) -> None:
        """
        渲染可交互的 RAG 证据面板（Summary/Body 分区 + 筛选/编辑/预算预警）。

        功能覆盖：
        - 证据结构：Summary/Body 分区展示，显示来源、置信度、时间戳与摘要文本；
        - 交互能力：勾选保留、排序置顶、单条折叠/展开、手动编辑候选摘要；
        - 预算提示：展示当前 Evidence Budget 使用量，并在超限时预警。
        """
        if not isinstance(evidence, dict) or not evidence:
            print("【RAG】界面无证据可展示")
            st.info("暂无 RAG 证据可展示。")
            return

        ui_state: Dict[str, Any] = st.session_state.rag_evidence_ui.setdefault(panel_key, {})
        defaults_applied_key = f"{panel_key}__defaults_applied"

        summary_entries = self._get_rag_evidence_entries(evidence, "summary")
        body_entries = self._get_rag_evidence_entries(evidence, "body")
        print(f"【RAG】界面准备展示证据，摘要/正文条目数：{len(summary_entries)}/{len(body_entries)}")

        summary_budget = int((evidence.get("summary") or {}).get("budget_chars") or 0)
        body_budget = int((evidence.get("body") or {}).get("budget_chars") or 0)

        all_entries = summary_entries + body_entries
        if all_entries and not ui_state.get(defaults_applied_key):
            selected_summary = (evidence.get("summary") or {}).get("items") or []
            selected_body = (evidence.get("body") or {}).get("items") or []
            selected_texts = set()
            for it in selected_summary + selected_body:
                if isinstance(it, dict) and it.get("text"):
                    selected_texts.add(str(it.get("text")))
            for entry in all_entries:
                item_state = ui_state.setdefault(entry["id"], {})
                if "keep" not in item_state:
                    item_state["keep"] = entry.get("text") in selected_texts
                if "pin" not in item_state:
                    item_state["pin"] = False
                if "edited_text" not in item_state:
                    item_state["edited_text"] = entry.get("text") or ""
            ui_state[defaults_applied_key] = True

        with st.expander("RAG 证据面板（可筛选/编辑/预算预警）", expanded=False):
            col_a, col_b, col_c = st.columns([1.1, 1, 1])
            with col_a:
                show_kept_only = st.checkbox("仅看已勾选", value=bool(ui_state.get("show_kept_only", False)), key=f"{panel_key}__kept_only")
                ui_state["show_kept_only"] = show_kept_only
            with col_b:
                keyword = st.text_input("搜索（标题/文本/来源）", value=str(ui_state.get("keyword", "")), key=f"{panel_key}__keyword")
                ui_state["keyword"] = keyword
            with col_c:
                min_conf = st.number_input(
                    "最小置信度",
                    min_value=0.0,
                    max_value=1.0,
                    value=float(ui_state.get("min_confidence", 0.0) or 0.0),
                    step=0.05,
                    key=f"{panel_key}__min_conf",
                )
                ui_state["min_confidence"] = float(min_conf)

            def _passes_filters(entry: Dict[str, Any]) -> bool:
                item_state = ui_state.get(entry["id"], {})
                if ui_state.get("show_kept_only") and not item_state.get("keep", False):
                    return False
                conf = entry.get("confidence")
                if conf is not None:
                    try:
                        if float(conf) < float(ui_state.get("min_confidence") or 0.0):
                            return False
                    except Exception:
                        pass
                kw = (ui_state.get("keyword") or "").strip().lower()
                if kw:
                    hay = " ".join(
                        [
                            str(entry.get("title") or ""),
                            str(entry.get("text") or ""),
                            str(entry.get("source") or ""),
                            str(entry.get("engine") or ""),
                        ]
                    ).lower()
                    if kw not in hay:
                        return False
                return True

            def _sort_key(entry: Dict[str, Any]):
                item_state = ui_state.get(entry["id"], {})
                pin = 1 if item_state.get("pin") else 0
                conf = entry.get("confidence")
                try:
                    conf_val = float(conf) if conf is not None else -1.0
                except Exception:
                    conf_val = -1.0
                ts = str(entry.get("timestamp") or "")
                return (-pin, -conf_val, ts)

            def _render_entries(entries: List[Dict[str, Any]], budget_chars: int) -> None:
                visible_entries = [e for e in entries if _passes_filters(e)]
                visible_entries.sort(key=_sort_key)

                used_chars = 0
                for e in entries:
                    item_state = ui_state.get(e["id"], {})
                    if not item_state.get("keep"):
                        continue
                    used_chars += len(str(item_state.get("edited_text") or ""))

                if budget_chars > 0:
                    st.markdown(f"**Evidence Budget**：{used_chars}/{budget_chars} chars")
                    progress = min(max(used_chars / budget_chars, 0.0), 1.0) if budget_chars else 0.0
                    st.progress(progress)
                    if used_chars > budget_chars:
                        st.warning("Evidence Budget 已超限：建议取消勾选或缩短编辑内容。")
                else:
                    st.markdown(f"**Evidence Budget**：{used_chars} chars")

                st.markdown(f"**候选条目**：{len(visible_entries)}/{len(entries)}")

                for idx, entry in enumerate(visible_entries):
                    item_state = ui_state.setdefault(entry["id"], {})
                    title = str(entry.get("title") or "").strip()
                    source = str(entry.get("source") or "").strip()
                    engine = str(entry.get("engine") or "").strip()
                    confidence = entry.get("confidence")
                    timestamp = str(entry.get("timestamp") or "").strip()

                    header_bits = []
                    if title:
                        header_bits.append(title[:60])
                    if confidence is not None:
                        header_bits.append(f"conf={confidence}")
                    if timestamp:
                        header_bits.append(f"ts={timestamp}")
                    header = " | ".join(header_bits) if header_bits else f"Evidence {idx + 1}"

                    with st.expander(header, expanded=False):
                        col1, col2, col3 = st.columns([0.7, 0.6, 1.7])
                        with col1:
                            item_state["keep"] = st.checkbox(
                                "勾选保留",
                                value=bool(item_state.get("keep", False)),
                                key=f"{panel_key}__keep__{entry['id']}",
                            )
                            item_state["pin"] = st.checkbox(
                                "置顶",
                                value=bool(item_state.get("pin", False)),
                                key=f"{panel_key}__pin__{entry['id']}",
                            )
                        with col2:
                            st.markdown(f"**分区**：{entry.get('section')}")
                            if confidence is not None:
                                st.markdown(f"**置信度**：{confidence}")
                            if timestamp:
                                st.markdown(f"**时间戳**：{timestamp}")
                        with col3:
                            if source:
                                st.markdown(f"**来源**：[{source}]({source})")
                            if engine:
                                st.markdown(f"**搜索源**：{engine}")

                        item_state["edited_text"] = st.text_area(
                            "摘要文本（可编辑）",
                            value=str(item_state.get("edited_text") or entry.get("text") or ""),
                            height=120 if entry.get("section") == "summary" else 180,
                            key=f"{panel_key}__edit__{entry['id']}",
                        )

            tab_summary, tab_body = st.tabs(["Summary", "Body"])
            with tab_summary:
                _render_entries(summary_entries, summary_budget)
            with tab_body:
                _render_entries(body_entries, body_budget)

            if show_answer_block:
                st.divider()
                st.markdown("#### 基于用户选择生成回答")
                default_question = str(evidence.get("_query") or ui_state.get("question") or "").strip()
                question = st.text_area(
                    "问题（将使用你勾选/编辑后的证据作为参考信息）",
                    value=default_question,
                    height=80,
                    key=f"{panel_key}__user_question",
                )
                ui_state["question"] = question

                def _build_selected_context_text() -> str:
                    selected_summary: List[str] = []
                    for entry in summary_entries:
                        item_state = ui_state.get(entry["id"], {})
                        if not item_state.get("keep"):
                            continue
                        text = str(item_state.get("edited_text") or "").strip()
                        if text:
                            selected_summary.append(text)

                    selected_body: List[str] = []
                    for entry in body_entries:
                        item_state = ui_state.get(entry["id"], {})
                        if not item_state.get("keep"):
                            continue
                        text = str(item_state.get("edited_text") or "").strip()
                        if text:
                            selected_body.append(text)

                    summary_text = "\n".join([f"- {t}" for t in selected_summary]) if selected_summary else "无"
                    body_text = "\n\n".join(selected_body) if selected_body else "无"
                    return f"【摘要证据】\n{summary_text}\n\n【正文证据】\n{body_text}"

                col_x, col_y = st.columns([1, 2])
                with col_x:
                    regenerate = st.button("用已选证据生成回答", use_container_width=True, key=f"{panel_key}__regen_answer")
                with col_y:
                    st.caption("说明：勾选/编辑会影响这里的回答；不会回写到检索/抓取阶段。")

                if regenerate:
                    context_text = _build_selected_context_text()
                    q = (question or "").strip() or "请基于参考信息给出结论。"
                    prompt_text = (
                        "基于以下参考信息回答用户的问题。如果参考信息不足以回答问题，请说明。\n\n"
                        f"参考信息：\n{context_text}\n\n"
                        f"用户问题：\n{q}\n\n"
                        "回答："
                    )
                    llm = self.llm_manager.get_llm()
                    response = llm.invoke(prompt_text)
                    answer_text = response.content if hasattr(response, "content") else response
                    cleaned_text, _ = self._split_think_content(str(answer_text))
                    self._log_llm_output("evidence_answer", cleaned_text)
                    ui_state["user_selected_answer"] = str(cleaned_text).strip()

                if ui_state.get("user_selected_answer"):
                    st.markdown("**回答（基于用户选择证据生成）**")
                    st.markdown(str(ui_state.get("user_selected_answer")))

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
        """
        渲染 LLM 设置面板。

        说明：
        - 该面板用于配置“两次调用”模型：第 1 次做分析（抽取实体/意图），第 2 次做生成（行程生成/修改）；
        - Agent Loop 复用同一个 LlmManager，因此此处配置更新后，Agent 调试面板也会同步生效。
        """

        config = st.session_state.get("llm_config", {})
        provider_options = ["ollama", "openai_compatible"]

        analysis_provider_current = config.get("analysis_provider", self.config.ANALYSIS_PROVIDER or "ollama")
        analysis_provider_index = provider_options.index(analysis_provider_current) if analysis_provider_current in provider_options else 0
        analysis_provider = st.selectbox("第 1 次调用-模型提供方（实体抽取+意图识别）", provider_options, index=analysis_provider_index)
        analysis_base_url_default = config.get("analysis_base_url") or (
            self.config.ANALYSIS_BASE_URL if analysis_provider == "ollama" else ""
        )
        analysis_model_default = config.get("analysis_model_name") or (
            self.config.ANALYSIS_MODEL_NAME if analysis_provider == "ollama" else ""
        )
        if analysis_provider == "ollama":
            # 使用分析阶段配置中的 Base URL 列表渲染下拉选择
            analysis_base_urls = getattr(self.config, "ANALYSIS_BASE_URL_OPTIONS", []) or [
                analysis_base_url_default or self.config.ANALYSIS_BASE_URL
            ]
            if analysis_base_url_default in analysis_base_urls:
                analysis_base_url_index = analysis_base_urls.index(analysis_base_url_default)
            else:
                analysis_base_url_index = 0
            analysis_base_url = st.selectbox(
                "第 1 次调用-Base URL（Ollama）",
                analysis_base_urls,
                index=analysis_base_url_index,
            )
        else:
            # 非 Ollama 场景仍然保留自由输入能力
            analysis_base_url = st.text_input("第 1 次调用-Base URL", analysis_base_url_default)
        if analysis_provider == "ollama":
            # 使用分析阶段配置中的模型名称列表渲染下拉选择
            analysis_models = getattr(self.config, "ANALYSIS_MODEL_NAME_OPTIONS", []) or [analysis_model_default]
            if analysis_model_default in analysis_models:
                analysis_model_index = analysis_models.index(analysis_model_default)
            else:
                analysis_model_index = 0
            analysis_model_name = st.selectbox(
                "第 1 次调用-模型名称（Ollama）",
                analysis_models,
                index=analysis_model_index,
            )
        else:
            analysis_model_name = st.text_input("第 1 次调用-模型名称", analysis_model_default)
        analysis_api_key_value = config.get("analysis_api_key") or ""
        if analysis_provider == "openai_compatible":
            analysis_api_key_value = st.text_input("第 1 次调用-API Key", analysis_api_key_value, type="password")
        analysis_temperature_value = float(config.get("analysis_temperature", self.config.ANALYSIS_TEMPERATURE or 0.7))
        analysis_temperature = st.slider("第 1 次调用-温度", 0.0, 1.0, analysis_temperature_value, 0.05)

        generation_provider_current = config.get("generation_provider", self.config.GENERATION_PROVIDER or "ollama")
        generation_provider_index = provider_options.index(generation_provider_current) if generation_provider_current in provider_options else 0
        generation_provider = st.selectbox("第 2 次调用-模型提供方（行程生成/修改）", provider_options, index=generation_provider_index)
        generation_base_url_default = config.get("generation_base_url") or (
            self.config.GENERATION_BASE_URL if generation_provider == "ollama" else ""
        )
        generation_model_default = config.get("generation_model_name") or (
            self.config.GENERATION_MODEL_NAME if generation_provider == "ollama" else ""
        )
        if generation_provider == "ollama":
            # 使用生成阶段配置中的 Base URL 列表渲染下拉选择
            generation_base_urls = getattr(self.config, "GENERATION_BASE_URL_OPTIONS", []) or [
                generation_base_url_default or self.config.GENERATION_BASE_URL
            ]
            if generation_base_url_default in generation_base_urls:
                generation_base_url_index = generation_base_urls.index(generation_base_url_default)
            else:
                generation_base_url_index = 0
            generation_base_url = st.selectbox(
                "第 2 次调用-Base URL（Ollama）",
                generation_base_urls,
                index=generation_base_url_index,
            )
        else:
            # 非 Ollama 场景仍然保留自由输入能力
            generation_base_url = st.text_input("第 2 次调用-Base URL", generation_base_url_default)
        if generation_provider == "ollama":
            # 使用生成阶段配置中的模型名称列表渲染下拉选择
            generation_models = getattr(self.config, "GENERATION_MODEL_NAME_OPTIONS", []) or [generation_model_default]
            if generation_model_default in generation_models:
                generation_model_index = generation_models.index(generation_model_default)
            else:
                generation_model_index = 0
            generation_model_name = st.selectbox(
                "第 2 次调用-模型名称（Ollama）",
                generation_models,
                index=generation_model_index,
            )
        else:
            generation_model_name = st.text_input("第 2 次调用-模型名称", generation_model_default)
        generation_api_key_value = config.get("generation_api_key") or ""
        if generation_provider == "openai_compatible":
            generation_api_key_value = st.text_input("第 2 次调用-API Key", generation_api_key_value, type="password")
        generation_temperature_value = float(config.get("generation_temperature", self.config.GENERATION_TEMPERATURE or 0.7))
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

    def render_agent_debug_panel(self) -> None:
        self.agent_ui.render_debug_panel()

    def _handle_chat_error(self, error: Exception, chat_placeholder) -> None:
        """
        统一处理聊天链路异常，输出用户可见提示与观测指标。
        """
        # 生成统一错误 payload，便于 UI 展示与后续排查
        error_payload = normalize_exception(
            error,
            code=ErrorCodes.UNEXPECTED_ERROR,
            source="ui_chat",
        )
        # 记录 UI 链路异常指标
        self._metrics.record("ui_chat_error", {"error": error_payload})
        # 构造用户可见的错误提示文本
        error_message = f"系统处理失败（{error_payload.get('code')}）：{error_payload.get('message')}"
        # 通过 Streamlit 提示用户
        st.error(error_message)
        # 追加一条助手错误消息到聊天历史，保证 UI 可见反馈
        assistant_msg = {
            "role": MessageType.ASSISTANT,
            "content": error_message,
            "timestamp": datetime.now().isoformat(),
            "metadata": {"error": True, "error_payload": error_payload},
        }
        # 写入会话消息列表，维持对话一致性
        st.session_state.chat_history.append(assistant_msg)
        # 标记 AI 处理结束，避免输入被锁死
        st.session_state.ai_processing = False
        # 刷新聊天区域显示当前历史记录
        chat_placeholder.markdown(build_chat_html(st.session_state.chat_history), unsafe_allow_html=True)

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
            try:
                # 记录聊天提交开始时间与日志
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                start_ts = datetime.now()
                print(f"[{ts}][UIManager] chat_submit start, session_id={session_id}, prompt_len={len(prompt)}")
                # 记录 UI 链路的开始指标
                self._metrics.record("ui_chat_start", {"session_id": session_id, "prompt_len": len(prompt)})
                # 组织用户消息结构
                user_msg = {"role": MessageType.USER, "content": prompt, "timestamp": datetime.now().isoformat(), "metadata": {}}
                # 写入聊天历史
                st.session_state.chat_history.append(user_msg)
                # 转换为消息对象用于后续存储
                user_message_obj = Message.model_validate(user_msg)
                # 构造加载中占位消息
                temp_loading = {
                    "role": MessageType.ASSISTANT,
                    "content": "",
                    "timestamp": datetime.now().isoformat(),
                    "metadata": {"loading": True},
                }
                # 追加加载中消息用于即时渲染
                loading_messages = st.session_state.chat_history + [temp_loading]
                # 设置处理中状态，避免重复提交
                st.session_state.ai_processing = True
                # 先渲染加载中视图
                chat_placeholder.markdown(build_chat_html(loading_messages), unsafe_allow_html=True)
                # 调用 LLM 进行意图识别与参数抽取
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager] 实体抽取+意图识别 start")
                intent_data = self.llm_manager.analyze_user_message(
                    query=prompt,
                    context=st.session_state.chat_history,
                    current_trip=st.session_state.trip_data,
                )
                # 打印意图识别结果
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager] 实体抽取+意图识别 end, intent={intent_data.get('intent')}")
                # 写入用户消息与意图数据到会话管理
                self.conversation_manager.process_new_message(
                    user_id,
                    device_id,
                    user_message_obj,
                    session_id,
                    intent_data=intent_data,
                )

                print(f"处理用户消息结束,当前用户的意图是{intent_data.get('intent')}, 准备开始进行LLM的生成:")

                # 获取意图类型，默认走普通对话
                intent_type = intent_data.get("intent", "general_conversation")
                # 初始化流式输出变量
                response_stream = None
                # 初始化行程流式请求缓存
                trip_streaming_request = None
                # 根据意图分支处理逻辑
                if intent_type == "generate_trip":
                    # 准备行程生成所需参数
                    prepared_request = self.llm_manager.prepare_trip_request_from_intent(
                        intent_data,
                        st.session_state.chat_history,
                    )
                    # 若缺少必要信息，提示用户补充
                    if prepared_request.get("needs_more_info"):
                        print("信息缺失，无法生成行程，暂时中断..............")
                        missing_info = prepared_request.get("missing_info", [])
                        response_data = {
                            "response": f"我需要更多信息才能为您生成行程。请提供以下信息：{', '.join(missing_info)}",
                            "trip_data": None,
                        }
                    else:
                        # 启动行程生成流式调用
                        trip_streaming_request = prepared_request
                        stream_start_ts = datetime.now()
                        stream_request_id = f"trip-{stream_start_ts.strftime('%H%M%S%f')}"
                        stream_stage = "trip_generation_stream"
                        print(
                            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager] generate_trip使用大模型开始生成行程【流式输出】"
                            f"stage={stream_stage} request_id={stream_request_id} prompt_len={len(prompt)}"
                        )
                        response_stream = self.llm_manager.stream_trip_generation(
                            prepared_request.get("user_input") or {},
                            prepared_request.get("context_texts") or [],
                        )
                        response_data = {
                            "response": "",
                            "trip_data": None,
                        }
                elif intent_type in ["modify_trip", "add_attraction", "delete_attraction", "reorder_trip"]:
                    # 仅在已有行程时支持修改类意图
                    if st.session_state.trip_data:
                        print(
                            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager] 目前是：{intent_type}，准备触发大模型"
                        )
                        prepared_request = self.llm_manager.prepare_trip_request_from_modification(
                            intent_data,
                            st.session_state.trip_data,
                            st.session_state.chat_history,
                        )
                        trip_streaming_request = prepared_request
                        stream_start_ts = datetime.now()
                        stream_request_id = f"trip-{stream_start_ts.strftime('%H%M%S%f')}"
                        stream_stage = "trip_generation_stream"
                        response_stream = self.llm_manager.stream_trip_generation(
                            prepared_request.get("user_input") or {},
                            prepared_request.get("context_texts") or [],
                            prepared_request.get("edit_cmd"),
                        )
                        response_data = {
                            "response": "",
                            "trip_data": None,
                        }
                    else:
                        response_data = {
                            "response": "我需要先为您生成一个基础行程，然后才能进行调整。请先提供目的地、天数和预算信息。",
                            "trip_data": None,
                        }
                else:
                    print("当前不是【生成行程】，也不是【修改行程】，只是普通对话模式")
                    tool_call = self.llm_manager.call_tool_by_llm(prompt, st.session_state.chat_history)
                    if tool_call.get("needs_tool") and tool_call.get("result"):
                        result_payload = tool_call.get("result")
                        if isinstance(result_payload, dict) and result_payload.get("success"):
                            response_data = {
                                "response": f"工具结果：{json.dumps(result_payload.get('data'), ensure_ascii=False)}",
                                "trip_data": None,
                            }
                            print("当前不是【生成行程】，也不是【修改行程】，也不是普通对话模式，直接返回工具结果")
                        else:
                            tool_call = {"needs_tool": False}
                    if not tool_call.get("needs_tool"):
                        decision_payload = tool_call.get("decision") or {}
                        print(
                            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager] 工具路由未命中，准备走对话模型 "
                            f"needs_tool={decision_payload.get('needs_tool')} tool_name={decision_payload.get('tool_name')} params={decision_payload.get('params')}"
                        )
                        stream_start_ts = datetime.now()
                        stream_request_id = f"chat-{stream_start_ts.strftime('%H%M%S%f')}"
                        stream_stage = "chat_response_stream"
                        print(
                            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager] 无法获取类型，直接走LLM调用，准备触发大模型 "
                            f"stage={stream_stage} request_id={stream_request_id} prompt_len={len(prompt)}"
                        )
                        response_stream = self.llm_manager.stream_chat_response(
                            prompt,
                            st.session_state.chat_history,
                            st.session_state.trip_data,
                        )
                        response_data = {
                            "response": "",
                            "trip_data": None,
                        }
                # 统一提取回复文本
                if isinstance(response_data, dict) and "response" in response_data:
                    chat_response = response_data["response"]
                else:
                    chat_response = str(response_data)
                # 初始化行程结果
                trip_data = None
                # 处理流式输出
                if response_stream is not None:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager]流式数据渲染-----------------------------开始")
                    chat_response = self.chat_stream_renderer.render_stream_response(
                        chat_response,
                        st.session_state.chat_history,
                        chat_placeholder,
                        response_stream=response_stream,
                    )
                    stream_elapsed_ms = int((datetime.now() - stream_start_ts).total_seconds() * 1000)
                    print(
                        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager] 流式输出-------结束"
                        f"stage={stream_stage} request_id={stream_request_id} elapsed_ms={stream_elapsed_ms} response_len={len(chat_response)}"
                    )
                    self._log_llm_output(stream_stage, chat_response)
                    chat_response, think_text = self._split_think_content(chat_response)
                    # 若是行程流式生成，则解析行程数据
                    if trip_streaming_request is not None:
                        trip_data = self.llm_manager.parse_trip_from_response_text(chat_response)
                        if trip_data:
                            st.session_state.trip_data = trip_data
                            st.session_state.map_obj = None
                            self.conversation_manager.conversationStorage.store_trip_data(session_id, trip_data)
                else:
                    self._log_llm_output("non_stream_response", chat_response)
                    chat_response, think_text = self._split_think_content(chat_response)
                    # 非流式输出直接渲染
                    print("非流式输出直接渲染-----------------------------开始")
                    self.chat_stream_renderer.render_stream_response(
                        chat_response,
                        st.session_state.chat_history,
                        chat_placeholder,
                        response_stream=None,
                    )
                    print("非流式输出直接渲染-----------------------------结束")
                    # 若返回包含行程数据，则写入状态
                    if isinstance(response_data, dict) and "trip_data" in response_data:
                        trip_data = response_data["trip_data"]
                        if trip_data:
                            st.session_state.trip_data = trip_data
                            st.session_state.map_obj = None
                            self.conversation_manager.conversationStorage.store_trip_data(session_id, trip_data)
                # 组织助手回复消息
                metadata_payload = {
                    "context_type": "trip_modification",
                    "conversation_id": st.session_state.current_conversation_id,
                    "has_trip_data": bool(trip_data),
                    "trip_data": trip_data
                }
                if think_text:
                    metadata_payload["think"] = think_text
                    # print(f"目前抽离出来的<think>内容是: \n + {think_text} \n\n")
                    # print(f"目前抽离出来的回答文本是: \n + {chat_response}")
                assistant_msg = {
                    "role": MessageType.ASSISTANT,
                    "content": chat_response,
                    "timestamp": datetime.now().isoformat(),
                    "metadata": metadata_payload,
                }
                # 追加助手消息到历史记录
                st.session_state.chat_history.append(assistant_msg)
                # 转换为消息对象用于持久化
                assistant_message_obj = Message.model_validate(assistant_msg)
                print("追加AI返回信息到历史记录 + 转换为消息对象用于持久化")

                # AI消息进行处理：主要是压缩多轮对话消息 + 存储会话信息到数据库中
                self.conversation_manager.process_new_message(user_id, device_id, assistant_message_obj, session_id)
                # 计算总耗时并记录日志
                total_cost = (datetime.now() - start_ts).total_seconds()
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][UIManager] AI消息处理结束！！, total_cost={total_cost:.2f}s")
                print("\n")
                # 记录 UI 链路成功指标
                self._metrics.record("ui_chat_success", {"session_id": session_id, "elapsed_ms": int(total_cost * 1000)})
                # 显示AI消息到界面中
                chat_placeholder.markdown(build_chat_html(st.session_state.chat_history), unsafe_allow_html=True)
                # 解除处理中状态
                st.session_state.ai_processing = False
                # 触发刷新
                st.rerun()
                # 若生成行程则展示提示
                if trip_data:
                    st.divider()
                    st.markdown("### 🎯 为您生成的行程方案")
                    self._display_trip_in_chat(trip_data)
                    st.success("✨ 行程已生成！右侧地图和详细安排已更新。")
            except Exception as exc:
                # 捕获异常并统一处理 UI 错误提示与指标
                self._handle_chat_error(exc, chat_placeholder)

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
                    top: 10px;
                    left: 50px;
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
                                time_module.sleep(0.25)
                        if last_event and last_event.get("map_obj"):
                            st.session_state.map_obj = last_event.get("map_obj")
                            print("[DEBUG] Map object generated successfully")
                        elif not st.session_state.get("map_obj"):
                            st.session_state.map_obj = self.map_renderer.render_map(trip_data)
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

        agent_last_state = st.session_state.get("agent_last_state")
        if isinstance(agent_last_state, dict):
            agent_status = agent_last_state.get("status")
        else:
            agent_status = getattr(agent_last_state, "status", None)
        autorefresh = getattr(st, "autorefresh", None)
        if callable(autorefresh) and agent_status in ("running", "paused"):
            autorefresh(interval=1000, key="agent_panels_autorefresh")

        panels_container = st.container()
        with panels_container:
            live_placeholder = st.empty()
            status_placeholder = st.empty()
            with live_placeholder.container():
                self.agent_ui.render_live_panel(floating=False)
            with status_placeholder.container():
                self.agent_ui.render_status_panel(floating=False)

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
