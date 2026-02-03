from datetime import datetime
from typing import Any, Dict, Iterable, List


def _ts() -> str:
    """
    返回当前时间的字符串表示，用于日志前缀。

    返回：
    - 形如 "YYYY-MM-DD HH:MM:SS" 的时间字符串。
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class LlmStreamingAdapter:
    """
    LLM 流式输出适配工具类。

    职责说明：
    1. 将完整文本拆分为统一的 start/delta/end 事件序列；
    2. 将模型原生 stream() 输出适配为统一事件协议；
    3. 从不同形态的 chunk 中抽取增量文本；
    4. 在需要时负责从单次 invoke 中退化为“伪流式”输出。
    """

    def build_stream_events(
        self,
        content: str,
        message_id: str,
        chunk_size: int = 32,
    ) -> List[Dict[str, Any]]:
        """
        将完整文本拆分为前端可消费的流式事件序列。

        处理流程：
        1. 追加 start 事件，建立消息骨架；
        2. 按 chunk_size 切分内容，每段生成一个 delta 事件；
        3. 最后追加 end 事件，标记流式结束。

        参数说明：
        - content: 完整输出文本；
        - message_id: 本次消息唯一标识；
        - chunk_size: 单个增量片段最大长度。

        返回：
        - 按 sequence 递增排列的事件列表。
        """
        safe_text = content or ""
        events: List[Dict[str, Any]] = []
        sequence = 0

        events.append(
            {
                "event": "start",
                "sequence": sequence,
                "message_id": message_id,
                "content_delta": "",
                "is_final": False,
            }
        )
        sequence += 1

        if safe_text:
            for i in range(0, len(safe_text), chunk_size):
                delta = safe_text[i : i + chunk_size]
                events.append(
                    {
                        "event": "delta",
                        "sequence": sequence,
                        "message_id": message_id,
                        "content_delta": delta,
                        "is_final": False,
                    }
                )
                sequence += 1

        events.append(
            {
                "event": "end",
                "sequence": sequence,
                "message_id": message_id,
                "content_delta": "",
                "is_final": True,
            }
        )
        return events

    def build_stream_events_from_stream(
        self,
        stream: Iterable[str],
        message_id: str,
    ) -> Iterable[Dict[str, Any]]:
        """
        将模型原生 stream() 输出适配为统一的 start/delta/end 协议。

        参数说明：
        - stream: 模型返回的增量文本迭代器；
        - message_id: 当前消息的唯一标识。

        返回：
        - 按顺序 yield 的事件字典。
        """
        sequence = 0
        yield {
            "event": "start",
            "sequence": sequence,
            "message_id": message_id,
            "content_delta": "",
            "is_final": False,
        }
        for delta in stream:
            if not delta:
                continue
            sequence += 1
            yield {
                "event": "delta",
                "sequence": sequence,
                "message_id": message_id,
                "content_delta": delta,
                "is_final": False,
            }
        sequence += 1
        yield {
            "event": "end",
            "sequence": sequence,
            "message_id": message_id,
            "content_delta": "",
            "is_final": True,
        }

    def extract_stream_delta(self, chunk: Any) -> str:
        """
        从不同模型返回的 chunk 结构中提取增量文本。

        支持结构：
        - 纯字符串；
        - 带 content 属性的对象；
        - 带 content 字段的字典。

        参数说明：
        - chunk: 模型 stream() 返回的单个增量块。

        返回：
        - 可直接拼接的文本片段，无法提取时返回空字符串。
        """
        if chunk is None:
            return ""
        if isinstance(chunk, str):
            return chunk
        if hasattr(chunk, "content"):
            return getattr(chunk, "content") or ""
        if isinstance(chunk, dict):
            content = chunk.get("content")
            return content if isinstance(content, str) else ""
        return str(chunk)

    def stream_llm_text(self, llm: Any, prompt: str) -> Iterable[str]:
        """
        调用模型的流式接口并输出增量文本，失败时自动降级为单次调用。

        处理流程：
        1. 优先检查模型是否具备 stream 接口；
        2. 若支持 stream，则逐 chunk 调用 extract_stream_delta 并 yield；
        3. 发生异常时打印日志并降级为 invoke，一次性返回完整文本。

        参数说明：
        - llm: 已初始化的模型实例；
        - prompt: 发送给模型的完整提示词。

        返回：
        - 增量文本片段迭代器。
        """
        try:
            if hasattr(llm, "stream"):
                for chunk in llm.stream(prompt):
                    delta = self.extract_stream_delta(chunk)
                    if delta:
                        yield delta
                return
        except Exception as exc:
            print(f"[{_ts()}][LlmStreamingAdapter] stream_llm_text 流式调用失败，降级为 invoke: {exc}")

        raw_response = llm.invoke(prompt)
        if hasattr(raw_response, "content"):
            response_text = raw_response.content
        else:
            response_text = raw_response
        if response_text:
            yield str(response_text)

