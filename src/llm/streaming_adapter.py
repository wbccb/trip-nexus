from typing import Any, Dict, Iterable, List
import re
import logging
from src.observability import log_event, summarize_value

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
    log_event(logger, logging.INFO, f"流式适配输出: {tag}", {"输出长度": len(cleaned_text), "输出预览": _format_log_text(cleaned_text)})


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
        调用模型的流式接口并输出增量文本。

        处理流程：
        1. 优先检查模型是否具备 stream 接口；
        2. 若支持 stream，则逐 chunk 调用 extract_stream_delta 并 yield；
        3. 若不支持 stream，则改用 invoke 一次性返回完整文本。

        参数说明：
        - llm: 已初始化的模型实例；
        - prompt: 发送给模型的完整提示词。

        返回：
        - 增量文本片段迭代器。
        """
        if hasattr(llm, "stream"):
            in_think = False
            carry = ""
            cleaned_parts: List[str] = []

            def _process_buffer(buffer_text: str) -> tuple[str, str, bool]:
                nonlocal in_think
                output = ""
                while buffer_text:
                    if in_think:
                        end_idx = buffer_text.find("</think>")
                        if end_idx == -1:
                            keep = buffer_text[-8:] if len(buffer_text) > 8 else buffer_text
                            return output, keep, in_think
                        buffer_text = buffer_text[end_idx + 8:]
                        in_think = False
                        continue
                    start_idx = buffer_text.find("<think>")
                    if start_idx == -1:
                        if len(buffer_text) <= 7:
                            return output, buffer_text, in_think
                        output += buffer_text[:-7]
                        return output, buffer_text[-7:], in_think
                    output += buffer_text[:start_idx]
                    buffer_text = buffer_text[start_idx + 7:]
                    in_think = True
                return output, "", in_think

            for chunk in llm.stream(prompt):
                delta = self.extract_stream_delta(chunk)
                if not delta:
                    continue
                buffer_text = carry + delta
                output_text, carry, in_think = _process_buffer(buffer_text)
                if output_text:
                    cleaned_parts.append(output_text)
                    yield output_text

            if not in_think and carry:
                cleaned_parts.append(carry)
                yield carry
            cleaned_text = "".join(cleaned_parts)
            _log_llm_output("stream_response", cleaned_text)
            return

        raw_response = llm.invoke(prompt)
        if hasattr(raw_response, "content"):
            response_text = raw_response.content
        else:
            response_text = raw_response
        cleaned_text = _strip_think_content(response_text)
        _log_llm_output("invoke_response", cleaned_text)
        if cleaned_text:
            yield str(cleaned_text)
