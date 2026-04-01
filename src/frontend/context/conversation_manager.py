from src.frontend.context.storage import get_conversation_storage, BaseConversationStorage
from src.frontend.context.entity import *
import json
from datetime import datetime
from typing import Optional
from pydantic import ValidationError
import re
from src.llm.llm_manager import LlmManager
from datetime import datetime as _dt
import logging
from src.observability import log_event, log_llm_end, log_llm_start, summarize_value

logger = logging.getLogger(__name__)


def _strip_think_content(text: Any) -> str:
    if text is None:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", str(text), flags=re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned)
    return cleaned.strip()


def _format_log_text(text: str, head: int = 180, tail: int = 180) -> str:
    return summarize_value(text, head=head, tail=tail)


def _log_llm_output(tag: str, cleaned_text: str) -> None:
    log_event(logger, logging.INFO, f"上下文 LLM 输出: {tag}\n----------------------", {"输出长度": len(cleaned_text), "输出预览": _format_log_text(cleaned_text)})

"""
会话管理：
1. 提取实体
2. 生成对话摘要
3. 压缩早期对话内容
4. 动态优化上下文，构建LLM输入:检查会话token，自动调用上面的步骤来构建出会话的prompt
"""
class ConversationManager:
    def __init__(self, conversation_storage: BaseConversationStorage, llm_manager: LlmManager):
        self.conversationStorage = conversation_storage
        self.llm_manager = llm_manager  # 假设传入一个LLM客户端

    def is_redundant_message(self, message: str) -> bool:
        """判断是否为冗余信息（寒暄等）"""
        redundant_phrases = [
            "你好", "谢谢", "再见", "拜拜", "好的", "明白了", "收到",
            "hello", "hi", "thank you", "bye", "ok", "got it"
        ]
        return any(phrase.lower() in message.lower() for phrase in redundant_phrases)

    def merge_core_entities_from_intent_data(self, intent_data: Dict[str, Any], existing_entities: CoreEntity) -> CoreEntity:
        params = intent_data.get("parameters") or {}
        updated_entities = existing_entities.model_copy(deep=True)

        destination = params.get("destination")
        if isinstance(destination, list) and destination:
            destination = destination[0]
        if isinstance(destination, str) and destination.strip():
            updated_entities.destination = destination.strip()

        budget = params.get("budget")
        if budget not in [None, "", [], {}]:
            try:
                updated_entities.budget = float(budget)
            except (TypeError, ValueError):
                pass

        preference = params.get("preference")
        if preference not in [None, "", [], {}]:
            updated_entities.preferences["preference"] = preference

        updated_entities.last_updated = datetime.now()
        return updated_entities

    def extract_core_entities(self, new_message: str, existing_entities: CoreEntity) -> CoreEntity:
        """
        使用LLM增量提取核心实体，并安全地合并到现有状态中。
        """
        if not self.llm_manager:
            return existing_entities

        # 1. 序列化现有数据（处理 None 情况）
        # 使用 model_dump_json (Pydantic v2) 或 json() (Pydantic v1)
        existing_json = "{}"
        if existing_entities:
            existing_json = existing_entities.model_dump_json(exclude_none=True, indent=2)

        # 2. 优化 Prompt：增加约束，防止 LLM 幻觉或格式错误
        prompt = f"""
        你是一个专业的数据处理助手。你的任务是从用户的新消息中提取旅游意向实体，并进行增量更新。

        【已知实体信息】:
        {existing_json}

        【用户新消息】:
        "{new_message}"

        【输出要求】:
        1. 请分析新消息是否补充或修正了已知信息。
        2. 严格按以下 JSON 结构输出（仅输出 JSON，不要 Markdown 标签）：
           {{
               "destination": "地点(str)",
               "budget": 数字(float/int),
               "travel_dates": ["YYYY-MM-DD", "YYYY-MM-DD"],
               "preferences": {{ "标签": "具体描述" }}
           }}
        3. 如果某项信息在“新消息”中未提及，请在该字段填 null。对于数组类型（如travel_dates），如果为空请填 [] 或 null，严禁在数组中包含 null 元素。
        4. 只有当新消息明确提到新偏好时，才更新 preferences。
        """

        start_ts = log_llm_start(
            logger,
            stage="核心实体抽取",
            model=str(getattr(self.llm_manager.get_analysis_llm(), "model", getattr(self.llm_manager.get_analysis_llm(), "model_name", "未知模型"))),
            prompt=prompt,
            extra={"消息长度": len(new_message), "已有实体": bool(existing_entities)},
        )
        raw_response = self.llm_manager.get_analysis_llm().invoke(prompt)
        if hasattr(raw_response, "content"):
            response_text = raw_response.content
        else:
            response_text = raw_response
        cleaned_response_text = _strip_think_content(response_text)
        _log_llm_output("extract_entities_response", cleaned_response_text)
        log_llm_end(logger, stage="核心实体抽取", started_at=start_ts, output=cleaned_response_text, extra={"原始响应长度": len(str(response_text))})

        clean_response = self.llm_manager.extract_json_from_string(cleaned_response_text)
        if not clean_response.startswith("{"):
            json_match = re.search(r'\{.*\}', clean_response, re.DOTALL)
            if json_match:
                clean_response = json_match.group(0)
            else:
                log_event(logger, logging.WARNING, "核心实体抽取结果无法解析 JSON，跳过更新")
                return existing_entities

        extracted_data = json.loads(clean_response)

        log_event(logger, logging.INFO, "核心实体抽取解析完成", {"字段": list(extracted_data.keys())})

        updated_entities = existing_entities.model_copy(deep=True)

        if extracted_data.get("destination"):
            updated_entities.destination = extracted_data["destination"]

        if extracted_data.get("budget") is not None:
            updated_entities.budget = extracted_data["budget"]

        if extracted_data.get("travel_dates"):
            valid_dates_str = [d for d in extracted_data["travel_dates"] if d]
            valid_dates_obj = []
            for d_str in valid_dates_str:
                try:
                    if isinstance(d_str, str):
                        dt = datetime.strptime(d_str, "%Y-%m-%d")
                        valid_dates_obj.append(dt)
                    elif isinstance(d_str, datetime):
                        valid_dates_obj.append(d_str)
                except ValueError:
                    try:
                        dt = datetime.fromisoformat(d_str)
                        valid_dates_obj.append(dt)
                    except ValueError:
                        log_event(logger, logging.WARNING, "日期格式无法解析", {"原始值": d_str})
            
            if valid_dates_obj:
                updated_entities.travel_dates = valid_dates_obj

        if extracted_data.get("preferences"):
            if updated_entities.preferences is None:
                updated_entities.preferences = {}
            updated_entities.preferences.update(extracted_data["preferences"])

        updated_entities.last_updated = datetime.now()
        return updated_entities

    def generate_summary(self, messages: List[Message], existing_summary: str = "") -> str:
        """生成对话摘要"""
        if not self.llm_manager:
            return existing_summary

        # 构建摘要Prompt
        conversation_text = "\n".join([f"{msg.role}: {msg.content}" for msg in messages])

        prompt = f"""
        你是一个对话摘要助手。请基于以下对话内容生成一个简洁的摘要，保留关键信息。

        当前摘要（如有）：
        {existing_summary}

        新的对话内容：
        {conversation_text}

        请生成一个包含所有关键信息的摘要，格式要求：
        1. 用自然语言描述
        2. 重点包含：用户需求、关键决策、重要信息
        3. 长度控制在200字以内
        4. 用中文输出
        """

        start_ts = log_llm_start(
            logger,
            stage="对话摘要生成",
            model=str(getattr(self.llm_manager.get_analysis_llm(), "model", getattr(self.llm_manager.get_analysis_llm(), "model_name", "未知模型"))),
            prompt=prompt,
            extra={"消息数": len(messages)},
        )
        raw_response = self.llm_manager.get_analysis_llm().invoke(prompt)
        if hasattr(raw_response, "content"):
            response = raw_response.content
        else:
            response = raw_response
        cleaned_response = _strip_think_content(response)
        _log_llm_output("summary_response", cleaned_response)
        log_llm_end(logger, stage="对话摘要生成", started_at=start_ts, output=cleaned_response, extra={"原始响应长度": len(str(response))})
        
        return cleaned_response

    def compress_early_messages(self, context: SessionContext) -> str:
        """压缩早期对话内容"""
        if len(context.short_term_messages) <= 10:
            return context.long_term_summary

        # 获取需要压缩的早期消息（最早的对话）
        early_messages = context.short_term_messages[:-10]  # 保留最近10轮
        if not early_messages:
            return context.long_term_summary

        # 生成新摘要
        new_summary = self.generate_summary(early_messages, context.long_term_summary)

        log_event(logger, logging.INFO, "早期消息压缩完成", {"新摘要长度": len(new_summary)})

        # 更新长期摘要
        self.conversationStorage.store_long_term_summary(context.session_id, new_summary)

        # 保留最近10轮对话
        context.short_term_messages = context.short_term_messages[-10:]

        return new_summary

    def count_tokens(self, text: str) -> int:
        """计算token数量"""
        return len(ENCODER.encode(text))

    def optimize_context_for_llm(self, context: SessionContext, max_tokens: int = 4096) -> Dict[str, Any]:
        """动态优化上下文，构建LLM输入"""
        # 1. 摘要融合
        core_entities_text = f"核心实体: {context.core_entities.json(indent=2)}"
        summary_text = f"对话摘要: {context.long_term_summary}"

        # 2. 构建基础上下文
        base_context = {
            "system_message": "你是一个智能助手，需要基于完整的上下文信息回答用户问题。",
            "core_entities": core_entities_text,
            "long_term_summary": summary_text,
            "recent_messages": []
        }

        # 3. 按优先级添加对话内容
        # 优先级：核心实体 > 最近3轮 > 长期摘要 > 早期对话

        current_tokens = self.count_tokens(json.dumps(base_context))

        # 添加最近3轮对话（最高优先级）
        recent_messages = context.short_term_messages[-3:] if context.short_term_messages else []
        for msg in recent_messages:
            msg_tokens = self.count_tokens(msg.content) + 10  # 估算role等开销
            if current_tokens + msg_tokens <= max_tokens:
                base_context["recent_messages"].append(msg.dict())
                current_tokens += msg_tokens

        # 4. 冗余过滤：跳过寒暄信息

        # 5. 动态截断检查
        if current_tokens > max_tokens:
            # 按优先级截断
            base_context["recent_messages"] = base_context["recent_messages"][-1:]  # 只保留最新1轮

        return base_context

    def process_new_message(
        self,
        user_id: str,
        device_id: str,
        message: Message,
        session_id: str,
        intent_data: Optional[Dict[str, Any]] = None,
    ) -> SessionContext:
        """处理新消息，更新上下文"""

        if message.role == "user":
            log_event(logger, logging.INFO, "准备写入用户消息到会话历史", {"message": message})
        else:
            log_event(logger, logging.INFO, "准备写入 AI 消息到会话历史")

        # 1. 获取或创建会话上下文
        # session_id = self.conversationStorage.generate_session_id(user_id, device_id)
        short_term_data = self.conversationStorage.get_short_term_context(session_id)

        if short_term_data:
            # 1.1 命中短期缓存（Redis），直接恢复会话上下文
            log_event(logger, logging.INFO, "命中短期缓存，直接恢复会话上下文")
            context = SessionContext(
                session_id=session_id,
                user_id=user_id,
                device_id=device_id,
                short_term_messages=[Message(**msg) for msg in short_term_data.get("messages", [])],
                message_count=short_term_data.get("message_count", 0),
                last_active=datetime.fromisoformat(short_term_data.get("last_active", datetime.now().isoformat()))
            )
        else:
            # 1.2 短期缓存未命中（Redis Miss），尝试从持久化存储（DB）恢复
            # 这通常发生在会话超过2小时不活跃，Redis key过期的情况
            history_messages_json = self.conversationStorage.get_session_chat_list(session_id)
            
            if history_messages_json:
                log_event(logger, logging.INFO, "从数据库恢复会话历史", {"消息数": len(history_messages_json)})
                # 反序列化历史消息
                all_messages = []
                for msg_json in history_messages_json:
                    try:
                        all_messages.append(Message.model_validate_json(msg_json))
                    except Exception as e:
                        log_event(logger, logging.WARNING, "解析历史消息失败", {"原因": str(e)})
                
                # 重建上下文：仅加载最近10条到短期窗口
                context = SessionContext(
                    session_id=session_id,
                    user_id=user_id,
                    device_id=device_id,
                    short_term_messages=all_messages[-10:], # 滑动窗口
                    message_count=len(all_messages),
                    last_active=datetime.now() # 恢复活跃状态
                )
                
                # 尝试同步恢复长期摘要（如果有）
                long_term_summary = self.conversationStorage.get_long_term_summary(session_id)
                if long_term_summary:
                    context.long_term_summary = long_term_summary
                
                # 尝试同步恢复行程数据（如果有）
                trip_data = self.conversationStorage.get_trip_data(session_id)
                if trip_data:
                    context.trip_data = trip_data

            else:
                # 1.3 数据库也无记录，创建全新会话
                context = SessionContext(
                    session_id=session_id,
                    user_id=user_id,
                    device_id=device_id
                )

        # 从用户消息中提取核心实体
        if message.role == MessageType.USER:
            # 2. 检查是否为冗余信息
            message.is_redundant = self.is_redundant_message(message.content)
            # 3. 获取现有核心实体
            existing_entities = self.conversationStorage.get_core_entities(session_id) or CoreEntity()
            # 4. 增量提取核心实体（如果不是冗余信息）
            if not message.is_redundant:
                start_ts = _dt.now()
                log_event(logger, logging.INFO, "开始增量覆盖核心实体", {"原始实体": existing_entities})
                if intent_data:
                    context.core_entities = self.merge_core_entities_from_intent_data(intent_data, existing_entities)
                else:
                    log_event(logger, logging.INFO, "未提供 intent_data，跳过核心实体抽取")
                cost = (_dt.now() - start_ts).total_seconds()
                # 更新存储
                log_event(logger, logging.INFO, "核心实体抽取与存储完成", {"耗时秒": cost})
                self.conversationStorage.store_core_entities(session_id, context.core_entities)
        else:
            message.is_redundant = False

        # 5. 添加新消息到短期窗口
        context.short_term_messages.append(message)
        context.message_count += 1
        context.last_active = datetime.now()

        log_event(logger, logging.INFO, "短期消息已更新", {"消息总数": context.message_count})

        # 6. 检查是否需要压缩早期对话
        if context.message_count > 10:
            log_event(logger, logging.INFO, "短期消息超过阈值，开始压缩早期消息", {"消息总数": context.message_count})
            context.long_term_summary = self.compress_early_messages(context)

        # 7. 更新短期存储
        self.conversationStorage.store_short_term_context(session_id, context)

        # 8. 存储数据到数据库中
        self.conversationStorage.store_session_chat(session_id, message.model_dump_json())

        if message.role == "user":
            log_event(logger, logging.INFO, "用户消息上下文更新完成", {"角色": message.role, "消息总数": context.message_count})
        else:
            log_event(logger, logging.INFO, "AI 消息上下文更新完成", {"角色": message.role, "消息总数": context.message_count})

        return context

    def get_optimized_context(self, session_id: str, max_tokens: int = 4096) -> Dict[str, Any]:
        """获取优化后的上下文用于LLM输入"""
        # 从存储恢复完整上下文
        short_term_data = self.conversationStorage.get_short_term_context(session_id)
        if not short_term_data:
            return {}

        messages = [Message(**msg) for msg in short_term_data.get("messages", [])]

        context = SessionContext(
            session_id=session_id,
            user_id="temp",  # 临时值，实际应从存储获取
            device_id="temp",
            short_term_messages=messages,
            core_entities=self.conversationStorage.get_core_entities(session_id) or CoreEntity(),
            long_term_summary=self.conversationStorage.get_long_term_summary(session_id),
            message_count=short_term_data.get("message_count", len(messages)),
            last_active=datetime.fromisoformat(short_term_data.get("last_active", datetime.now().isoformat()))
        )

        # 优化上下文
        return self.optimize_context_for_llm(context, max_tokens)

    def get_user_conversations(self, user_id: str) -> Optional[List[Message]]:
        """从redis获取短期的会话管理类中的短期会话10条"""
        return self.conversationStorage.get_short_term_context(user_id)

    def generate_session_id(self, user_id: str, device_id: str) -> str:
        return self.conversationStorage.generate_session_id(user_id, device_id)
