import time
import uuid
import html as html_lib
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Iterable

import streamlit as st

from src.frontend.context.entity import MessageType
from src.llm.llm_manager import LlmManager


def build_chat_html(messages: List[Dict[str, Any]]) -> str:
    """
    构建聊天消息的 HTML 字符串，用于在 Streamlit 中以自定义样式渲染消息列表。

    设计目标：
    1. 支持用户与助手消息的左右对齐展示；
    2. 支持助手消息的“流式分段”展示（冻结段 + 活跃段）；
    3. 适配较长对话的滚动区域并隐藏默认滚动条。

    参数说明：
    - messages: 消息列表，每条消息包含 role/content/metadata 等字段。

    返回：
    - 可以直接传入 st.markdown(unsafe_allow_html=True) 的 HTML 字符串。
    """
    def _strip_code_fence(text: str) -> str:
        lines = text.splitlines()
        if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip()
        return text.strip()

    def _try_parse_trip_data(raw: Any) -> Optional[Dict[str, Any]]:
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            return None
        candidate = _strip_code_fence(raw)
        try:
            return json.loads(candidate)
        except Exception:
            return None

    def _build_trip_table(data: Dict[str, Any]) -> Optional[str]:
        if not isinstance(data, dict):
            return None
        destination = data.get("destination")
        days = data.get("days")
        daily_plan = data.get("daily_plan")
        if not isinstance(daily_plan, dict):
            return None

        def _day_sort_key(day_key: Any):
            text = str(day_key)
            try:
                return (0, int(text))
            except Exception:
                return (1, text)

        def _cell(value: Any) -> str:
            return html_lib.escape("" if value is None else str(value))

        rows = []
        for day_key in sorted(daily_plan.keys(), key=_day_sort_key):
            items = daily_plan.get(day_key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    "<tr>"
                    f"<td>{_cell(day_key)}</td>"
                    f"<td>{_cell(item.get('time'))}</td>"
                    f"<td>{_cell(item.get('attraction'))}</td>"
                    f"<td>{_cell(item.get('address'))}</td>"
                    f"<td>{_cell(item.get('transport'))}</td>"
                    f"<td>{_cell(item.get('duration'))}</td>"
                    f"<td>{_cell(item.get('latitude'))}</td>"
                    f"<td>{_cell(item.get('longitude'))}</td>"
                    "</tr>"
                )

        if not rows:
            return None

        caption_text = " · ".join([_cell(destination), f"{_cell(days)}天"] if destination or days else [])
        caption_html = f"<caption>{caption_text}</caption>" if caption_text else ""
        header_html = (
            "<thead><tr>"
            "<th>天数</th>"
            "<th>时间</th>"
            "<th>安排</th>"
            "<th>地址</th>"
            "<th>交通</th>"
            "<th>时长</th>"
            "<th>纬度</th>"
            "<th>经度</th>"
            "</tr></thead>"
        )
        body_html = "<tbody>" + "".join(rows) + "</tbody>"
        return f'<table class="trip-table">{caption_html}{header_html}{body_html}</table>'

    css = """
    <style>
    .chat-outer{
        display:flex;
        flex-direction:column;
        height:calc(100vh - 260px);
    }
    .chat-wrapper{
        display:flex;
        flex-direction:column;
        gap:12px;
        flex:1;
        min-height:0;
        overflow-y:auto;
        padding:8px;
    }
    .msg{display:flex;align-items:flex-start;margin:24px 0px;}
    .msg.assistant{justify-content:flex-start;}
    .msg.user{justify-content:flex-end;}
    .bubble{border-radius:8px;padding:10px 12px;max-width:80%;line-height:1.6;font-size:14px;white-space:pre-wrap;word-break:break-word;}
    .assistant .bubble{background:#fff;border:1px solid #eee;}
    .user .bubble{background:#E6F4FF;border:1px solid #CDE6FF;}
    .avatar{width:28px;height:28px;border-radius:50%;background:#eef;display:flex;align-items:center;justify-content:center;font-size:14px;margin-right: 10px;}
    .user .avatar{display:none;}
    .bubble.loading{display:flex;align-items:center;gap:8px;}
    .loading-dots{display:inline-flex;gap:4px;}
    .loading-dots span{width:6px;height:6px;border-radius:50%;background:#999;animation:ai-loading 1.2s infinite ease-in-out;}
    .loading-dots span:nth-child(2){animation-delay:0.2s;}
    .loading-dots span:nth-child(3){animation-delay:0.4s;}
    @keyframes ai-loading{
        0%,80%,100%{transform:scale(0);}
        40%{transform:scale(1);}
    }
    .stream-segment{display:block;white-space:pre-wrap;}
    .stream-segment.frozen{opacity:0.95;}
    .think-box{margin-top:8px;border:1px dashed #ddd;border-radius:6px;padding:6px 8px;background:#fafafa;}
    .think-box summary{cursor:pointer;color:#666;font-size:12px;}
    .think-box pre{white-space:pre-wrap;margin:6px 0 0 0;font-size:12px;color:#666;}
    .trip-table{width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed;}
    .trip-table caption{text-align:left;font-weight:600;margin-bottom:6px;color:#333;}
    .trip-table th,.trip-table td{border:1px solid #e6e6e6;padding:6px 8px;vertical-align:top;word-break:break-word;}
    .trip-table thead th{background:#f7f7f7;}
    </style>
    """
    body = ['<div class="chat-outer"><div id="chat-wrapper" class="chat-wrapper">']
    last_assistant_index = -1
    for idx, message in enumerate(messages):
        role = message.get("role")
        if role == MessageType.ASSISTANT or role == "assistant":
            last_assistant_index = idx
    for idx, message in enumerate(messages):
        role = message.get("role")
        role_str = "assistant" if role == MessageType.ASSISTANT or role == "assistant" else "user"
        content = message.get("content", "")
        metadata = message.get("metadata", {}) if isinstance(message, dict) else {}
        think_text = ""
        if isinstance(metadata, dict):
            think_value = metadata.get("think")
            if isinstance(think_value, list):
                think_text = "\n\n".join([str(item) for item in think_value if item])
            elif isinstance(think_value, str):
                think_text = think_value

        # 当 metadata 中包含 segments/active 时，说明该消息处于流式渲染状态
        is_streaming_content = False
        if isinstance(metadata, dict) and metadata.get("segments") is not None:
            segments = metadata.get("segments") or []
            active = metadata.get("active") or ""
            combined_parts: List[str] = []
            for segment in segments:
                # print(f"""\n 处理前的片段: {str(segment)}""")
                safe_segment = html_lib.escape(str(segment)).replace("\n", "<br/>") # 不替换的话,\n\n会导致Streamlit把 \n\n 误当成分段导致界面错乱
                # print(f"处理后的片段: {str(safe_segment)} \n")
                combined_parts.append(f'<span class="stream-segment frozen">{safe_segment}</span>')
            safe_active = html_lib.escape(str(active)).replace("\n", "<br/>") # 不替换的话,\n\n会导致Streamlit把 \n\n 误当成分段导致界面错乱
            combined_parts.append(f'<span class="stream-segment">{safe_active}</span>')
            content = "".join(combined_parts)
            is_streaming_content = True

        is_loading = isinstance(metadata, dict) and metadata.get("loading", False)
        body.append(f'<div class="msg {role_str}">')
        if role_str == "assistant":
            body.append('<div class="avatar">AI</div>')
        if is_loading:
            body.append(
                '<div class="bubble loading">AI处理中'
                '<span class="loading-dots"><span></span><span></span><span></span></span>'
                '</div>'
            )
        else:
            safe_content = str(content)
            if not is_streaming_content:
                trip_table = _build_trip_table(_try_parse_trip_data(content))
                if trip_table:
                    safe_content = trip_table
                else:
                    safe_content = html_lib.escape(safe_content).replace("\n", "<br/>") # 不替换的话,\n\n会导致Streamlit把 \n\n 误当成分段导致界面错乱
            if think_text and idx == last_assistant_index:
                print(f"在ui_manager获取think内容，然后隐藏think内容准备重新刷新一次界面UI")
                safe_think = html_lib.escape(str(think_text)).replace("\n", "<br/>") ## 不替换的话,\n\n会导致Streamlit把 \n\n 误当成分段导致界面错乱
                safe_content = f'<details class="think-box"><summary>思考过程</summary><pre>{safe_think}</pre></details>{safe_content}'
            body.append(f'<div class="bubble">{safe_content}</div>')
        body.append('</div>')
    body.append('</div></div>')
    body.append(
        '<script>'
        'var chat=document.getElementById("chat-wrapper");'
        'if(chat){chat.scrollTop=chat.scrollHeight;}'
        '</script>'
    )

    # print(f"body: {body}")
    return css + "".join(body)


class ChatStreamRenderer:
    """
    聊天流式渲染工具类。

    职责说明：
    1. 负责消费 LlmManager 提供的 start/delta/end 事件序列；
    2. 维护“冻结段 + 活跃段”的双缓冲结构，减少 Markdown 重排抖动；
    3. 使用时间窗口进行渲染节流，避免过于频繁的 UI 刷新；
    4. 在流式结束后返回完整文本，供 JSON 解析与历史存储使用。
    """

    def __init__(self, llm_manager: LlmManager) -> None:
        """
        初始化 ChatStreamRenderer。

        参数说明：
        - llm_manager: LlmManager 实例，用于构建统一流式事件序列。
        """
        self.llm_manager = llm_manager

    def render_stream_response(
        self,
        response_text: str,
        base_messages: List[Dict[str, Any]],
        placeholder,
        response_stream: Optional[Iterable[str]] = None,
    ) -> str:
        """
        按统一协议消费 LLM 流式输出，并通过 Streamlit 实时渲染。

        处理流程：
        1. 生成唯一 message_id，构建 start/delta/end 事件序列；
        2. 对 delta 事件进行增量累积，并按空行分段，将完成段落放入冻结区；
        3. 按 120ms 为单位节流刷新 UI，仅在必要时重绘聊天区域；
        4. 收到 end 事件时停止流式更新，并返回完整输出文本。

        参数说明：
        - response_text: 非流式兜底路径下的一次性完整回复文本；
        - base_messages: 当前已有对话历史，用于在其后追加流式消息；
        - placeholder: Streamlit 占位符，用于替换渲染聊天 HTML；
        - response_stream: LLM 的增量文本迭代器；为 None 时退化为本地分片流式。

        返回：
        - full_text: 流式期间累积的完整回复文本。
        """
        message_id = f"assistant_{uuid.uuid4().hex[:12]}"
        if response_stream is not None:
            events = self.llm_manager.build_stream_events_from_stream(
                response_stream,
                message_id,
            )
        else:
            events = self.llm_manager.build_stream_events(
                response_text,
                message_id,
                chunk_size=24,
            )

        frozen_segments: List[str] = []
        active_segment = ""
        full_text = ""
        last_flush = time.perf_counter()
        flush_interval = 0.12

        for event in events:
            if event.get("event") == "delta":
                delta_text = event.get("content_delta", "")
                full_text += delta_text
                active_segment += delta_text
                parts = active_segment.split("\n\n")
                if len(parts) > 1:
                    for piece in parts[:-1]:
                        frozen_segments.append(piece + "\n\n")
                    active_segment = parts[-1]

            now = time.perf_counter()
            should_flush = event.get("event") == "end" or (now - last_flush) >= flush_interval
            if should_flush:
                stream_message = {
                    "role": MessageType.ASSISTANT,
                    "content": "",
                    "timestamp": datetime.now().isoformat(),
                    "metadata": {
                        "segments": frozen_segments,
                        "active": active_segment,
                        "streaming": True,
                    },
                }
                placeholder.markdown(
                    build_chat_html(base_messages + [stream_message]),
                    unsafe_allow_html=True,
                )
                last_flush = now
                if event.get("event") != "end":
                    time.sleep(0.02)

        return full_text
