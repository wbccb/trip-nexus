from src.frontend.context.storage import get_conversation_storage, BaseConversationStorage
from src.frontend.context.entity import *
import json
"""
会话管理：
1. 提取实体
2. 生成对话摘要
3. 压缩早期对话内容
4. 动态优化上下文，构建LLM输入:检查会话token，自动调用上面的步骤来构建出会话的prompt
"""
class ConversationManager:
    def __init__(self, conversation_storage: BaseConversationStorage, llm_client=None):
        self.conversationStorage = conversation_storage
        self.llm_client = llm_client  # 假设传入一个LLM客户端

    def is_redundant_message(self, message: str) -> bool:
        """判断是否为冗余信息（寒暄等）"""
        redundant_phrases = [
            "你好", "谢谢", "再见", "拜拜", "好的", "明白了", "收到",
            "hello", "hi", "thank you", "bye", "ok", "got it"
        ]
        return any(phrase.lower() in message.lower() for phrase in redundant_phrases)

    def extract_core_entities(self, new_message: str, existing_entities: CoreEntity) -> CoreEntity:
        """使用LLM增量提取核心实体"""
        if not self.llm_client:
            return existing_entities

        # 构建Prompt引导LLM提取增量信息
        prompt = f"""
        你是一个上下文管理助手，负责从对话中提取关键信息。请分析以下新消息，并与现有信息对比：

        现有信息：
        {existing_entities.json(indent=2)}

        新消息：
        {new_message}

        请只提取新出现的或需要更新的关键信息，格式为JSON：
        {{
            "destination": "string or null",
            "budget": "number or null",
            "travel_dates": ["date1", "date2"] or null,
            "preferences": {{
                "key1": "value1",
                "key2": "value2"
            }}
        }}

        注意：
        1. 如果某字段没有新信息，返回null
        2. 只提取明确提到的信息
        3. 保持数据类型正确
        """

        try:
            response = self.llm_client.generate(
                prompt=prompt,
                max_tokens=500,
                temperature=0.1,
                response_format={"type": "json_object"}
            )

            new_entities = CoreEntity.parse_raw(response)

            # 合并新旧实体，新实体优先
            updated_entities = existing_entities.copy()

            if new_entities.destination:
                updated_entities.destination = new_entities.destination
            if new_entities.budget is not None:
                updated_entities.budget = new_entities.budget
            if new_entities.travel_dates:
                updated_entities.travel_dates = new_entities.travel_dates
            if new_entities.preferences:
                updated_entities.preferences.update(new_entities.preferences)

            updated_entities.last_updated = datetime.now()
            return updated_entities

        except Exception as e:
            print(f"实体提取失败: {e}")
            return existing_entities

    def generate_summary(self, messages: List[Message], existing_summary: str = "") -> str:
        """生成对话摘要"""
        if not self.llm_client:
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

        try:
            response = self.llm_client.generate(
                prompt=prompt,
                max_tokens=300,
                temperature=0.3
            )
            return response.strip()
        except Exception as e:
            print(f"摘要生成失败: {e}")
            return existing_summary

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

    def process_new_message(self, user_id: str, device_id: str, message: Message) -> SessionContext:
        """处理新消息，更新上下文"""

        # 1. 获取或创建会话
        session_id = self.conversationStorage.generate_session_id(user_id, device_id)
        short_term_data = self.conversationStorage.get_short_term_context(session_id)

        if short_term_data:
            # 恢复现有会话
            context = SessionContext(
                session_id=session_id,
                user_id=user_id,
                device_id=device_id,
                short_term_messages=[Message(**msg) for msg in short_term_data.get("messages", [])],
                message_count=short_term_data.get("message_count", 0),
                last_active=datetime.fromisoformat(short_term_data.get("last_active", datetime.now().isoformat()))
            )
        else:
            # 创建新会话
            context = SessionContext(
                session_id=session_id,
                user_id=user_id,
                device_id=device_id
            )

        # 2. 检查是否为冗余信息
        message.is_redundant = self.is_redundant_message(message.content)

        # 3. 获取现有核心实体
        existing_entities = self.conversationStorage.get_core_entities(session_id) or CoreEntity()

        # 4. 增量提取核心实体（如果不是冗余信息）
        if not message.is_redundant:
            context.core_entities = self.extract_core_entities(message.content, existing_entities)
            # 更新存储
            self.conversationStorage.store_core_entities(session_id, context.core_entities)

        # 5. 添加新消息到短期窗口
        context.short_term_messages.append(message)
        context.message_count += 1
        context.last_active = datetime.now()

        # 6. 检查是否需要压缩早期对话
        if context.message_count > 10:
            context.long_term_summary = self.compress_early_messages(context)

        # 7. 更新短期存储
        self.conversationStorage.store_short_term_context(session_id, context)

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