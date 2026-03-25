from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any, Iterable

from langchain_ollama import OllamaLLM
import copy
import json
import re
from string import Formatter
from datetime import datetime
from geopy.geocoders import Nominatim
from src.rag.network.multi_source_search import MultiSourceSearcher
from src.rag.rag_main import AIRetrievalPipeline
from src.llm.tool_protocol import ToolSchema, ToolCallResult, ToolRegistry
from src.llm.tools.weather_tool import get_daily_weather
from src.llm.tools.geo_tool import geocode_address
from src.llm.tools.poi_tool import search_poi
from src.llm.streaming_adapter import LlmStreamingAdapter
from src.models.conflicts import AlternativePlan, ConflictItem, ConflictReport, ConflictType
from src.observability import (
    ErrorCodes,
    normalize_exception,
    get_global_recorder,
)


class DailyPlanItem(BaseModel):
    time: str = Field(description="格式如'09:00-09:30'，必须为半小时粒度时间段")
    attraction: str = Field(description="景点名称，必须准确")
    address: str = Field(description="详细地址，包含街道门牌号")
    city: str = Field(default="", description="城市名称，例如'成都市'")
    street: str = Field(default="", description="街道信息，例如'青羊区青华路'")
    latitude: float = Field(default=0.0, description="景点纬度，精确到小数点后至少4位")
    longitude: float = Field(default=0.0, description="景点经度，精确到小数点后至少4位")
    transport: str = Field(description="具体交通方式，如'地铁2号线→步行5分钟'")
    duration: str = Field(description="停留时间，如'2小时'")
    rating: Optional[float] = Field(default=None, description="评分，0-5 之间的小数")
    tags: List[str] = Field(default_factory=list, description="类型标签列表")
    recommend_reason: Optional[str] = Field(default="", description="推荐理由，简洁描述")
    sources: List[Any] = Field(default_factory=list, description="来源链接列表，元素可为 {title,url} 或 url 字符串")


class TripPlan(BaseModel):
    destination: str = Field(description="旅行目的地城市")
    days: int = Field(description="旅行总天数")
    daily_plan: Dict[str, List[DailyPlanItem]] = Field(
        description="键为'1','2'等字符串，值为当天行程列表"
    )


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _strip_think_content(text: Any) -> str:
    if text is None:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", str(text), flags=re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned)
    return cleaned.strip()


def _format_log_text(text: str, head: int = 180, tail: int = 180) -> str:
    if text is None:
        return ""
    text_value = str(text)
    if len(text_value) <= head + tail + 5:
        return text_value
    return f"{text_value[:head]}....{text_value[-tail:]}"


def _log_llm_output(tag: str, cleaned_text: str) -> None:
    preview = _format_log_text(cleaned_text)
    print(f"[{_ts()}][LlmManager] {tag} cleaned_len={len(cleaned_text)} cleaned_preview={preview}")


def _collect_template_fields(template: str) -> List[str]:
    fields: List[str] = []
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name:
            fields.append(field_name)
    return fields


def _validate_template_fields(template: str, allowed_fields: List[str], template_name: str) -> None:
    allowed = set(allowed_fields)
    actual = set(_collect_template_fields(template))
    unexpected = sorted([field for field in actual if field not in allowed])
    missing = sorted([field for field in allowed if field not in actual])
    if unexpected:
        raise ValueError(f"{template_name} 存在未转义或未声明的占位符: {unexpected}")
    if missing:
        print(f"[{_ts()}][LlmManager] 模板校验提示 {template_name} 缺少占位符: {missing}")


BUDGET_LEVEL_MAP = {
    "economy": "经济模式（优先公共交通与平价餐饮，控制整体花费，避免不必要的高消费安排）",
    "balanced": "均衡模式（交通、餐饮与景点体验保持平衡，可在体验与成本之间灵活取舍）",
    "comfortable": "舒适模式（可接受更舒适的交通与更有特色的餐饮，不必过度压缩预算）",
}

INTENSITY_MAP = {
    "leisure": "休闲模式（单日不超过 3 个主要景点，控制步行强度，预留更多休息时间）",
    "standard": "标准模式（单日 4-5 个景点，节奏适中，兼顾体验丰富度与体力负担）",
    "extreme": "特种兵模式（允许高强度、早出晚归与高覆盖率，优先提升打卡数量）",
}

PACE_MAP = {
    "cultural": "文化探索（优先博物馆、历史街区、文化体验类内容，并为重点景点预留更多停留时间）",
    "efficient": "打卡效率（热门景点尽量高效串联，以更紧凑的路线提升覆盖数量）",
    "family_friendly": "亲子友好（避免过早出发，优先儿童友好景点，安排更平缓的节奏与午间休息）",
}

OPENAI_CHAT_TEMPLATE_KWARGS = {
    "chat_template_kwargs": {
        "enable_thinking": False,
    }
}


class LlmManager:
    # 默认使用本地安装的 Ollama deepseek-r1:7b
    def __init__(
        self,
        model_name: str = "deepseek-r1:7b",
        ollama_base_url: str = "http://localhost:11434",
        provider: str = "ollama",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.7,
    ):
        if base_url is None:
            base_url = ollama_base_url

        base_config: Dict[str, Any] = {
            "provider": provider,
            "base_url": base_url,
            "model_name": model_name,
            "api_key": api_key,
            "temperature": temperature,
        }

        self._analysis_config: Dict[str, Any] = dict(base_config)
        self._generation_config: Dict[str, Any] = dict(base_config)

        self.analysis_llm = self._create_llm(self._analysis_config)
        self.generation_llm = self._create_llm(self._generation_config)
        self.parser = JsonOutputParser(pydantic_object=TripPlan)
        self.tool_registry = ToolRegistry()
        self._geolocator = Nominatim(
            user_agent="trip_nexus_tools_v1",
            timeout=15,
            domain="nominatim.openstreetmap.org",
        )
        # self._poi_searcher = MultiSourceSearcher(self.analysis_llm)
        # 升级为 RAG Pipeline，支持 Evidence Budget 和日志
        self._poi_searcher = AIRetrievalPipeline(self.analysis_llm)
        self._register_default_tools()
        # 初始化全局指标记录器，统一采集 LLM 相关的链路指标
        self._metrics = get_global_recorder()
        # 初始化流式适配器，用于统一 stream/invoke 行为
        self._streaming_adapter = LlmStreamingAdapter()

    def _create_llm(self, cfg: Dict[str, Any]):
        provider = cfg.get("provider") or "ollama"  # 读取提供方配置，缺省为 ollama
        model_name = cfg.get("model_name") or "deepseek-r1:7b"  # 读取模型名称，缺省为 deepseek-r1:7b
        base_url = cfg.get("base_url") or "http://localhost:11434"  # 读取 Base URL，缺省为本地 Ollama
        temperature = cfg.get("temperature") or 0.7  # 读取温度参数，缺省为 0.7

        if provider == "openai_compatible":  # 仅当 provider 为 openai_compatible 时进入该分支
            try:
                from langchain_openai import ChatOpenAI  # 延迟导入，避免不兼容版本在启动时崩溃
            except ImportError as e:
                raise RuntimeError(
                    "当前环境的 langchain_openai 与 langchain_core 版本不兼容，暂不支持 OpenAI 兼容模型。"
                    "请升级相关依赖或切换 provider=ollama。"
                ) from e

            api_key = cfg.get("api_key") or ""  # 读取 API Key，缺省为空字符串
            print(f"[{_ts()}][LlmManager] 初始化 OpenAI 兼容模型: {model_name} @ {base_url}")  # 记录初始化日志
            return ChatOpenAI(  # 创建 OpenAI 兼容模型实例
                model=model_name,  # 模型名称
                api_key=api_key,  # API Key
                base_url=base_url,  # 远端 Base URL（/v1）
                temperature=temperature,  # 温度参数
                extra_body=OPENAI_CHAT_TEMPLATE_KWARGS,  # 统一关闭深度思考，优先响应速度
            )

        print(f"[{_ts()}][LlmManager] 初始化 Ollama 模型: {model_name} @ {base_url}")  # 记录 Ollama 初始化日志
        return OllamaLLM(  # 创建 Ollama 模型实例
            base_url=base_url,  # Ollama Base URL
            model=model_name,  # 模型名称
            temperature=temperature,  # 温度参数
            num_ctx=4096,  # 上下文长度
            timeout=300,  # 请求超时
            reasoning=False,  # 等价关闭 thinking/reasoning 模式，避免输出思考内容
        )

    def update_llm_config(self, config: Dict[str, Any]) -> None:
        print(f"[{_ts()}][LlmManager] update_llm_config called with keys: {list(config.keys())}")
        analysis_cfg = {
            "provider": config.get("analysis_provider") or config.get("provider") or "ollama",
            "base_url": config.get("analysis_base_url") or config.get("base_url") or "http://localhost:11434",
            "model_name": config.get("analysis_model_name") or config.get("model_name") or "deepseek-r1:7b",
            "api_key": config.get("analysis_api_key") or config.get("api_key") or "",
            "temperature": float(config.get("analysis_temperature", config.get("temperature", 0.7))),
        }
        generation_cfg = {
            "provider": config.get("generation_provider") or config.get("provider") or "ollama",
            "base_url": config.get("generation_base_url") or config.get("base_url") or "http://localhost:11434",
            "model_name": config.get("generation_model_name") or config.get("model_name") or "deepseek-r1:7b",
            "api_key": config.get("generation_api_key") or config.get("api_key") or "",
            "temperature": float(config.get("generation_temperature", config.get("temperature", 0.7))),
        }
        print(f"[{_ts()}][LlmManager] analysis_cfg: {analysis_cfg}")
        print(f"[{_ts()}][LlmManager] generation_cfg: {generation_cfg}")
        self._analysis_config = analysis_cfg
        self._generation_config = generation_cfg
        self.analysis_llm = self._create_llm(self._analysis_config)
        self.generation_llm = self._create_llm(self._generation_config)

    def get_llm(self):
        return self.generation_llm

    def get_analysis_llm(self):
        return self.analysis_llm

    def get_generation_llm(self):
        return self.generation_llm

    def list_tools(self) -> List[Dict[str, Any]]:
        return self.tool_registry.list_schemas()

    def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """统一调用工具注册表，返回标准化的结果结构。"""
        result = self.tool_registry.call(tool_name, params)
        if hasattr(result, "model_dump") and callable(getattr(result, "model_dump")):
            return result.model_dump()
        return result

    def _is_cloud_model(self, llm_role: str) -> bool:
        """判断当前模型是否为云端模型，基于 provider 配置做轻量识别。"""
        if llm_role == "analysis":
            cfg = self._analysis_config
        else:
            cfg = self._generation_config
        provider = (cfg.get("provider") or "").lower()
        return provider == "openai_compatible"

    def _supports_function_call(self, llm: Any) -> bool:
        """检查模型实例是否支持 FunctionCall（是否具备 bind_tools 接口）。"""
        return hasattr(llm, "bind_tools")

    def _build_function_call_tools(self) -> List[Any]:
        """基于当前内置工具构建 LangChain Tool，用于云端 FunctionCall 调用。"""
        try:
            from langchain_core.tools import tool as lc_tool
        except Exception:
            return []

        @lc_tool("weather.get_daily")
        def weather_get_daily(city: str, date: Optional[str] = None, days: int = 7) -> Dict[str, Any]:
            """FunctionCall 天气工具：输入城市与日期，返回每日天气结构化结果。"""
            return get_daily_weather(city, date, days)

        @lc_tool("geo.geocode")
        def geo_geocode(address: str, city: Optional[str] = None, attraction: Optional[str] = None) -> Dict[str, Any]:
            """FunctionCall 地理编码工具：输入地址与城市，返回经纬度与标准化地址。"""
            return geocode_address(
                address,
                self._geolocator,
                city=city,
                attraction=attraction,
            )

        @lc_tool("poi.search")
        def poi_search(query: str, city: Optional[str] = None, top_k: int = 5, defer_answer: bool = False) -> Dict[str, Any]:
            """FunctionCall POI 工具：输入关键词与城市，返回搜索结果列表。"""
            return search_poi(
                query,
                self._poi_searcher,
                city=city,
                top_k=top_k,
                defer_answer=defer_answer,
            )

        return [weather_get_daily, geo_geocode, poi_search]

    def _call_tool_by_function_call(self, query: str, context: Optional[List[Any]] = None) -> Dict[str, Any]:
        """使用云端模型的 FunctionCall 能力完成工具选择与执行。"""
        tools = self._build_function_call_tools()
        # 如果工具封装失败，回退到路由方案以保证链路不中断
        if not tools:
            return {
                "needs_tool": False,
                "decision": {"needs_tool": False, "tool_name": "", "params": {}, "source": "function_call"},
                "result": None,
                "fallback": True,
            }

        # 将上下文整理成可读文本，便于模型理解当前对话状态
        normalized_context = self._normalize_tool_context(context)
        context_text = ""
        if normalized_context:
            context_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in normalized_context])

        prompt = f"""
对话上下文：
{context_text or "无"}

用户输入：
{query}
"""
        # 将工具绑定到云端模型，让模型自行产出 tool_calls
        bound_llm = self.analysis_llm.bind_tools(tools)
        print("准备触发大模型调用: functionCall获取当前需要使用的工具名")
        response = bound_llm.invoke(prompt)
        response_text = response.content if hasattr(response, "content") else response
        cleaned_response_text = _strip_think_content(response_text)
        _log_llm_output("function_call_response", cleaned_response_text)
        print(f"[LLMManager] functionCall获取当前需要使用的工具名，!!!LLM触发 => 得到大模型原始响应: {response}")

        # 兼容不同模型返回结构，优先读取 tool_calls
        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls is None and hasattr(response, "additional_kwargs"):
            tool_calls = response.additional_kwargs.get("tool_calls")

        # 模型未触发工具时直接返回，不进行额外兜底路由
        if not tool_calls:
            print(f"[LLMManager] 从response拿不到tool_calls")
            return {
                "needs_tool": False,
                "decision": {"needs_tool": False, "tool_name": "", "params": {}, "source": "function_call"},
                "result": None,
                "fallback": False,
            }

        # 当前先取第一个工具调用，避免多工具时的执行顺序问题
        first_call = tool_calls[0]
        tool_name = first_call.get("name") if isinstance(first_call, dict) else ""
        params = first_call.get("args") if isinstance(first_call, dict) else {}
        if not isinstance(params, dict):
            params = {}

        

        # 工具名为空时直接返回，避免误调用
        if not tool_name:
            print(f"[LLMManager] 从tool_calls拿不到工具名: {json.dumps(first_call, ensure_ascii=False, default=str)}")
            return {
                "needs_tool": False,
                "decision": {"needs_tool": False, "tool_name": "", "params": {}, "source": "function_call"},
                "result": None,
                "fallback": False,
            }

        print(f"[LLMManager] 使用云端模型的 FunctionCall 能力完成工具选择与执行，!!!LLM触发 => 得到工具名:{tool_name}, 参数:{params}")


        # 执行工具并返回标准化结果
        result = self.call_tool(tool_name, params)
        res =  {
            "needs_tool": True,
            "decision": {"needs_tool": True, "tool_name": tool_name, "params": params, "source": "function_call"},
            "result": result,
            "fallback": False,
        }
        result_data = result.get("data") if isinstance(result, dict) else None
        result_items = None
        if isinstance(result_data, dict):
            result_items = result_data.get("results")
        result_size = len(result_items) if isinstance(result_items, list) else None
        success_flag = result.get("success") if isinstance(result, dict) else None
        error_message = result.get("error") if isinstance(result, dict) else None
        print(
            "[LLMManager] 工具执行结果 "
            f"tool={tool_name} success={success_flag} "
            f"size={result_size} error={error_message}"
        )
        return res

    def _normalize_tool_context(self, context: Optional[List[Any]] = None) -> List[Dict[str, str]]:
        """统一上下文结构，确保工具路由和 FunctionCall 输入一致。"""
        # 将多种来源的上下文统一成 role/content 结构，避免工具路由依赖特定上游格式
        if not context:
            return []
        normalized: List[Dict[str, str]] = []
        for msg in context[-6:]:
            if isinstance(msg, dict):
                # 标准对话消息：保留 role 与 content
                role = msg.get("role") or "user"
                content = msg.get("content") or ""
                normalized.append({"role": str(role), "content": str(content)})
            else:
                # 纯文本上下文：默认视为 user 消息
                normalized.append({"role": "user", "content": str(msg)})
        return normalized

    def build_stream_events(
        self,
        content: str,
        message_id: str,
        chunk_size: int = 32,
    ) -> List[Dict[str, Any]]:
        """
        构建前端可消费的流式事件序列，按统一协议拆分完整文本。

        参数说明：
        - content: 完整输出文本；
        - message_id: 本次消息唯一标识；
        - chunk_size: 单个增量片段最大长度。

        返回：
        - 由 LlmStreamingAdapter 生成的事件列表。
        """
        return self._streaming_adapter.build_stream_events(content, message_id, chunk_size)

    def build_stream_events_from_stream(
        self,
        stream: Iterable[str],
        message_id: str,
    ) -> Iterable[Dict[str, Any]]:
        """
        将模型原生流式输出适配为统一的 start/delta/end 事件序列。

        参数说明：
        - stream: 模型返回的增量文本迭代器；
        - message_id: 当前消息唯一标识。

        返回：
        - 由 LlmStreamingAdapter 生成的事件迭代器。
        """
        return self._streaming_adapter.build_stream_events_from_stream(stream, message_id)

    def stream_llm_text(self, prompt: str, llm_role: str = "generation", stage: str = "stream") -> Iterable[str]:
        """
        使用大模型原生 stream 输出文本增量，失败时由适配器降级为一次性 invoke。

        Args:
            prompt: 发送给模型的完整提示词。
            llm_role: 使用的模型角色，默认 generation。

        返回：
            增量文本片段迭代器，由 LlmStreamingAdapter 统一处理流式与降级逻辑。
        """
        # 根据角色选择对应的模型实例
        llm = self.generation_llm if llm_role == "generation" else self.analysis_llm
        # 记录流式调用开始时间，便于统计耗时
        start_ts = datetime.now()
        # 记录流式输出累计字符数
        output_chars = 0
        # 记录流式调用开始指标
        self._metrics.record(
            "llm_stream_start",
            {"role": llm_role, "prompt_len": len(prompt)},
        )
        cfg = self._generation_config if llm_role == "generation" else self._analysis_config
        request_id = f"{llm_role}-{start_ts.strftime('%H%M%S%f')}"
        print(
            f"[{_ts()}][LlmManager] llm_http_request_start stage={stage} role={llm_role} "
            f"provider={cfg.get('provider')} base_url={cfg.get('base_url')} model={cfg.get('model_name')} "
            f"request_id={request_id}"
        )

        def _stream_wrapper() -> Iterable[str]:
            nonlocal output_chars
            for delta in self._streaming_adapter.stream_llm_text(llm, prompt):
                output_chars += len(str(delta))
                yield delta
            elapsed_ms = int((datetime.now() - start_ts).total_seconds() * 1000)
            self._metrics.record(
                "llm_stream_end",
                {"role": llm_role, "elapsed_ms": elapsed_ms, "output_len": output_chars},
            )
            print(
                f"[{_ts()}][LlmManager] llm_http_request_end stage={stage} role={llm_role} "
                f"elapsed_ms={elapsed_ms} output_len={output_chars} request_id={request_id}"
            )

        return _stream_wrapper()

    def build_trip_prompt(
        self,
        user_input: Dict[str, Any],
        context: List[str],
        edit_cmd: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        构建行程生成的最终提示词，包含约束信息与工具预调用结果。

        Args:
            user_input: 已整理的行程生成参数。
            context: 最近上下文文本列表。
            edit_cmd: 行程修改指令（如添加/删除景点）。

        Returns:
            适用于 LLM 生成行程 JSON 的提示词。
        """
        constraints_text = self._build_constraints_context(user_input)
        merged_context: List[str] = []
        if context:
            merged_context.extend(context)
        if constraints_text:
            merged_context.append(constraints_text)
        # 行程生成前先做工具预调用，补充天气/POI/地理编码等外部信息
        tool_query = self._build_tool_query(user_input=user_input, edit_cmd=edit_cmd)
        if tool_query:
            tool_call = self.call_tool_by_llm(tool_query, self._normalize_tool_context(context))
            if tool_call.get("needs_tool") and tool_call.get("result"):
                result_payload = tool_call.get("result")
                if isinstance(result_payload, dict) and result_payload.get("success"):
                    # 将工具结果注入上下文，参与后续 prompt 构建
                    merged_context.append(f"工具结果：{json.dumps(result_payload, ensure_ascii=False)}")
        return self.build_prompt(user_input, merged_context, edit_cmd)

    def parse_trip_from_response_text(self, response_text: str) -> Optional[Dict[str, Any]]:
        """
        将流式返回的完整文本解析为行程 JSON 数据。

        Args:
            response_text: LLM 输出的完整文本内容。

        Returns:
            成功时返回行程数据字典，失败时返回 None。
        """
        try:
            clean_response = self.extract_json_from_string(response_text)
            trip_data = self.parser.parse(clean_response)
            if hasattr(trip_data, "model_dump") and callable(getattr(trip_data, "model_dump")):
                return trip_data.model_dump()
            if isinstance(trip_data, dict):
                return trip_data
            raise TypeError(f"解析器返回了意外类型: {type(trip_data)}")
        except Exception as e:
            print(f"[{_ts()}][LlmManager] 行程 JSON 解析失败: {e}")
            return None

    def stream_trip_generation(
        self,
        user_input: Dict[str, Any],
        context: List[str],
        edit_cmd: Optional[Dict[str, Any]] = None,
    ) -> Iterable[str]:
        """
        使用大模型真实流式输出行程 JSON 文本。

        Args:
            user_input: 行程生成参数。
            context: 最近上下文文本列表。
            edit_cmd: 行程修改指令（如添加/删除景点）。

        Returns:
            可迭代的文本增量流。
        """
        prompt = self.build_trip_prompt(user_input, context, edit_cmd)
        return self.stream_llm_text(prompt, llm_role="generation", stage="trip_generation_stream")

    def prepare_trip_request_from_intent(
        self,
        intent_data: Dict[str, Any],
        context: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """
        从意图识别结果中整理行程生成所需参数与上下文。

        Args:
            intent_data: analyze_user_message 返回的意图与参数数据。
            context: 最近对话上下文。

        Returns:
            包含是否缺参、缺参列表、user_input 与 context_texts 的字典。
        """
        params = intent_data.get("parameters", {})
        needs_more_info = intent_data.get("needs_more_info", True)
        missing_info = intent_data.get("missing_info", [])

        # 缺参检测，确保行程生成参数完整
        if needs_more_info:
            if not missing_info:
                if not params.get("destination") or params["destination"] in [None, [], ""]:
                    missing_info.append("目的地")
                if not params.get("days") or params["days"] is None:
                    missing_info.append("旅行天数")
                if not params.get("budget") or params["budget"] is None:
                    missing_info.append("预算")
            if missing_info:
                return {
                    "needs_more_info": True,
                    "missing_info": missing_info,
                    "user_input": None,
                    "context_texts": [],
                }

        # 处理 destination - 可能是列表或字符串
        destination_raw = params.get("destination", "成都")
        if isinstance(destination_raw, list) and destination_raw:
            destination = destination_raw[0]
        elif isinstance(destination_raw, str) and destination_raw.strip():
            destination = destination_raw.strip()
        else:
            destination = "成都"

        # 处理 days - 安全转换
        days_raw = params.get("days")
        try:
            days = int(days_raw) if days_raw not in [None, ""] else 3
            days = max(1, min(days, 15))
        except (TypeError, ValueError):
            days = 3

        # 处理 budget - 安全转换
        budget_raw = params.get("budget")
        try:
            budget = int(budget_raw) if budget_raw not in [None, ""] else 5000
            budget = max(1000, min(budget, 50000))
        except (TypeError, ValueError):
            budget = 5000

        # 处理 preference - 处理各种可能的格式
        preference_raw = params.get("preference")
        if isinstance(preference_raw, list) and preference_raw:
            preference = [str(p) for p in preference_raw if p]
        elif isinstance(preference_raw, str) and preference_raw.strip():
            preference = [pref.strip() for pref in preference_raw.split(",") if pref.strip()]
        else:
            preference = ["美食", "历史"]

        # 从上下文中补充缺失的信息
        if context:
            for msg in reversed(context[-5:]):
                content = msg["content"].lower()
                if days == 3:
                    day_match = re.search(r'(\d+)[\s]*天', content)
                    if day_match:
                        try:
                            days = max(1, min(int(day_match.group(1)), 15))
                        except ValueError:
                            pass
                if budget == 5000:
                    budget_match = re.search(r'(\d+)[\s]*(?:元|块|rmb)', content)
                    if budget_match:
                        try:
                            budget = max(1000, min(int(budget_match.group(1)), 50000))
                        except ValueError:
                            pass

        user_input = {
            "destination": destination,
            "days": days,
            "budget": budget,
            "preference": preference if isinstance(preference, list) else [preference],
            "guide_links": [],
        }
        context_texts = [msg["content"] for msg in context[-3:]] if context else []
        return {
            "needs_more_info": False,
            "missing_info": [],
            "user_input": user_input,
            "context_texts": context_texts,
        }

    def _build_chat_prompt(
        self,
        query: str,
        context: Optional[List[Dict[str, str]]] = None,
        current_trip: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        构建通用对话提示词，将最近上下文与行程摘要合并到对话中。

        Args:
            query: 用户本次输入文本。
            context: 最近对话上下文列表。
            current_trip: 当前行程数据，用于补充背景信息。

        Returns:
            用于生成自然语言回复的提示词。
        """
        normalized_context = self._normalize_tool_context(context)
        context_text = ""
        if normalized_context:
            context_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in normalized_context])
        trip_text = ""
        if isinstance(current_trip, dict) and current_trip:
            destination = current_trip.get("destination")
            days = current_trip.get("days")
            if destination or days:
                trip_text = f"当前行程：目的地={destination or '未知'}，天数={days or '未知'}。"
        return (
            "你是旅行规划助手，请根据上下文清晰回答用户问题。\n"
            f"{trip_text}\n"
            f"对话上下文：\n{context_text or '无'}\n\n"
            f"用户输入：{query}\n"
            "助手："
        )

    def stream_chat_response(
        self,
        query: str,
        context: Optional[List[Dict[str, str]]] = None,
        current_trip: Optional[Dict[str, Any]] = None,
    ) -> Iterable[str]:
        """
        生成对话回复的真实流式输出，返回增量文本迭代器。

        Args:
            query: 用户输入文本。
            context: 最近对话上下文。
            current_trip: 当前行程数据。

        Returns:
            大模型流式输出的增量文本迭代器。
        """
        prompt = self._build_chat_prompt(query, context, current_trip)
        return self.stream_llm_text(prompt, llm_role="generation", stage="chat_response_stream")

    def decide_tool_call(self, query: str, context: Optional[List[Any]] = None) -> Dict[str, Any]:
        """使用分析模型进行工具路由决策，返回 needs_tool/tool_name/params。"""
        tools = self.list_tools()
        # 工具路由只依赖统一后的上下文，避免 UI/对话链路差异导致判断失真
        normalized_context = self._normalize_tool_context(context)
        context_text = ""
        if normalized_context:
            context_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in normalized_context])
        # 将工具 Schema + 对话上下文 + 用户输入交给分析模型做路由决策
        prompt = f"""
你是工具路由器，需要在给定工具列表中选择是否调用工具。

可用工具列表（JSON）：
{json.dumps(tools, ensure_ascii=False)}

对话上下文：
{context_text or "无"}

用户输入：
{query}

请严格返回以下 JSON 结构：
{{
  "needs_tool": true/false,
  "tool_name": "工具名称或空字符串",
  "params": {{}}
}}
"""
        raw_response = self.analysis_llm.invoke(prompt)
        response_text = raw_response.content if hasattr(raw_response, "content") else raw_response
        cleaned_response_text = _strip_think_content(response_text)
        _log_llm_output("tool_decision_response", cleaned_response_text)
        # 解析失败时降级为“不调用工具”，确保流程可继续
        clean_response = self.extract_json_from_string(cleaned_response_text)
        try:
            data = json.loads(clean_response)
        except Exception:
            data = {"needs_tool": False, "tool_name": "", "params": {}}
        if not isinstance(data, dict):
            return {"needs_tool": False, "tool_name": "", "params": {}}
        if not isinstance(data.get("params"), dict):
            data["params"] = {}
        if "needs_tool" not in data:
            data["needs_tool"] = False
        if "tool_name" not in data:
            data["tool_name"] = ""
        return data

    def call_tool_by_llm(self, query: str, context: Optional[List[Any]] = None) -> Dict[str, Any]:
        """根据模型能力选择 FunctionCall 或工具路由，并执行工具调用。"""
        if self._is_cloud_model("analysis") and self._supports_function_call(self.analysis_llm):
            # 云端模型优先走 FunctionCall，只有在工具封装失败时回退
            function_call_result = self._call_tool_by_function_call(query, context)
            if not function_call_result.get("fallback"):
                return function_call_result
        # 第一步：让分析模型决定是否需要工具与工具参数
        decision = self.decide_tool_call(query, context)
        if not decision.get("needs_tool"):
            return {"needs_tool": False, "decision": decision, "result": None}
        tool_name = decision.get("tool_name") or ""
        params = decision.get("params") or {}
        # 第二步：执行工具并返回统一结果结构
        result = self.call_tool(tool_name, params)
        res = {"needs_tool": True, "decision": decision, "result": result}
        print(f"[LLMManager] 使用云端模型的 FunctionCall 能力完成工具选择与执行=> 工具名:{tool_name}, 参数:{params}, 结果:{res}")
        return res

    def _build_tool_query(self, user_input: Optional[Dict[str, Any]] = None, edit_cmd: Optional[Dict[str, Any]] = None, query: Optional[str] = None) -> str:
        # 将结构化输入与编辑指令拼成自然语言查询，便于路由模型理解
        parts: List[str] = []
        if query:
            parts.append(str(query))
        if user_input:
            destination = user_input.get("destination")
            if destination:
                parts.append(str(destination))
            preference = user_input.get("preference")
            if isinstance(preference, list):
                parts.extend([str(p) for p in preference if p])
            elif preference:
                parts.append(str(preference))
        if edit_cmd:
            if edit_cmd.get("attraction"):
                parts.append(str(edit_cmd.get("attraction")))
            if edit_cmd.get("msg"):
                parts.append(str(edit_cmd.get("msg")))
        return " ".join([p for p in parts if p]).strip()

    def _register_default_tools(self) -> None:
        weather_schema = ToolSchema(
            name="weather.get_daily",
            description="按城市获取每日天气预报",
            params={
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD", "optional": True},
                    "days": {"type": "integer", "description": "预测天数，默认7天", "optional": True, "default": 7},
                },
                "required": ["city"],
            },
            returns={
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "daily": {"type": "array"},
                    "source": {"type": "string"},
                },
            },
            errors={
                "TOOL_NOT_FOUND": "工具未注册",
                "TOOL_EXECUTION_ERROR": "工具执行失败",
            },
        )
        geocode_schema = ToolSchema(
            name="geo.geocode",
            description="地址地理编码获取经纬度",
            params={
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "city": {"type": "string", "optional": True},
                    "attraction": {"type": "string", "optional": True},
                },
                "required": ["address"],
            },
            returns={
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "display_name": {"type": "string"},
                    "query": {"type": "string"},
                },
            },
            errors={
                "TOOL_NOT_FOUND": "工具未注册",
                "TOOL_EXECUTION_ERROR": "工具执行失败",
            },
        )
        poi_schema = ToolSchema(
            name="poi.search",
            description="按城市和关键词搜索 POI",
            params={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "city": {"type": "string", "optional": True},
                    "top_k": {"type": "integer", "optional": True},
                    "defer_answer": {"type": "boolean", "optional": True},
                },
                "required": ["query"],
            },
            returns={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "results": {"type": "array"},
                },
            },
            errors={
                "TOOL_NOT_FOUND": "工具未注册",
                "TOOL_EXECUTION_ERROR": "工具执行失败",
            },
        )
        self.tool_registry.register(weather_schema, get_daily_weather, kind="local")
        self.tool_registry.register(
            geocode_schema,
            lambda address, city=None, attraction=None: geocode_address(
                address,
                self._geolocator,
                city=city,
                attraction=attraction,
            ),
            kind="local",
        )
        self.tool_registry.register(
            poi_schema,
            lambda query, city=None, top_k=5, defer_answer=False: search_poi(
                query,
                self._poi_searcher,
                city=city,
                top_k=top_k,
                defer_answer=defer_answer,
            ),
            kind="local",
        )

    def build_prompt(self, user_input: Dict[str, Any], context: List[str],
                     edit_cmd: Optional[Dict[str, Any]] = None) -> str:
        """构建提示词（支持修改指令）"""
        edit_note = ""
        if edit_cmd:
            match edit_cmd["type"]:
                case "add":
                    edit_note = f"需在第{edit_cmd['day']}天添加景点{edit_cmd['attraction']}，并调整当天行程逻辑"
                case "delete":
                    edit_note = f"需删除第{edit_cmd['day']}天的{edit_cmd['attraction']}，并重新规划当天后续行程"
                case "reorder":
                    edit_note = "需调整行程顺序，确保路线更合理"
                case "modify":
                    edit_note = f"需重新调整行程：{edit_cmd.get('msg', '根据新要求调整')}"

        # 优化提示词模板，更适合指令遵循型模型
        template = """
        你是专业旅游规划师，严格遵循以下所有要求生成行程，并仅返回JSON格式数据。

        【严重警告：JSON格式必须严格合法】
        1. 严禁输出 Markdown 代码块（如 ```json ... ```），只输出纯文本 JSON。
        2. **数组元素之间必须用逗号分隔**。例如：{json_example_correct} 是正确的，{json_example_wrong} 是错误的。
        3. 键值对之间必须用逗号分隔。
        4. 所有的键和字符串值必须使用双引号。
        5. 不要包含任何注释或思考过程（<think>...</think>）。

        【数据结构要求】
        请严格参考以下 JSON 结构示例（注意 daily_plan 是一个字典，key 是第几天，value 是当天的行程列表）：
        {{
            "destination": "城市名",
            "days": 3,
            "daily_plan": {{
                "1": [
                    {{
                        "time": "09:00-09:30",
                        "attraction": "景点A",
                        "address": "地址A，包含城市名和街道门牌号",
                        "city": "城市名A",
                        "street": "街道信息A",
                        "latitude": 30.6570,
                        "longitude": 104.0650,
                        "transport": "交通A",
                        "duration": "30分钟",
                        "rating": 4.5,
                        "tags": ["博物馆", "人文"],
                        "recommend_reason": "馆藏丰富，适合深入了解城市历史。",
                        "sources": [
                            {{"title": "景点评价来源", "url": "https://example.com/poi-a"}}
                        ]
                    }},
                    {{
                        "time": "09:30-10:00",
                        "attraction": "午餐",
                        "address": "地址B，包含城市名和街道门牌号",
                        "city": "城市名A",
                        "street": "街道信息B",
                        "latitude": 30.6580,
                        "longitude": 104.0660,
                        "transport": "步行",
                        "duration": "30分钟",
                        "rating": 4.2,
                        "tags": ["美食", "本地餐厅"],
                        "recommend_reason": "距离近，适合补充体力。",
                        "sources": [
                            {{"title": "餐厅信息", "url": "https://example.com/poi-b"}}
                        ]
                    }}
                ]
            }}
        }}

        【行程约束】
        - 目的地：{destination}
        - 总天数：{days}天
        - 预算：{budget}元/人（请合理分配交通、餐饮开支）
        - 偏好：{preference}
        {constraints_section}
        - 额外要求：{edit_note}

        【参考攻略（优先采纳）】
        {context}

        【细节规范】
        - 每天行程安排在8:00-18:00，时间必须连续且无冲突。
        - 每个时间段必须为半小时粒度，例如"08:00-08:30"、"08:30-09:00"、"09:00-09:30"。
        - 地址必须精确到街道和门牌号（如"成都市青羊区青华路9号"），并同时提供匹配的 city、street 字段。
        - 每个行程项必须包含精确的 latitude 和 longitude 坐标，且与地址一致。
        - 交通方式具体（如"地铁2号线人民公园站B口出"）。
        - rating 为 0-5 的小数，tags 为 1-3 个类型标签，recommend_reason 简洁说明推荐依据。
        - sources 提供 1-2 个可点击来源链接，优先引用攻略或权威页面。

        【Schema 定义】
        {format_instructions}
        """
        allowed_template_fields = [
            "json_example_correct",
            "json_example_wrong",
            "destination",
            "days",
            "budget",
            "preference",
            "constraints_section",
            "edit_note",
            "context",
            "format_instructions",
        ]
        _validate_template_fields(template, allowed_template_fields, "build_prompt")
        prompt = PromptTemplate(
            template=template.strip(),
            input_variables=["destination", "days", "budget", "preference", "context", "edit_note", "constraints_section"],
            partial_variables={
                "format_instructions": self.parser.get_format_instructions(),
                "json_example_correct": '[{"a":1}, {"b":2}]',
                "json_example_wrong": '[{"a":1} {"b":2}]'
            }
        )

        # 打印提示词构建的关键输入，便于定位 user_input/context/edit_note 引起的格式化异常
        print(f"[{_ts()}][LlmManager] 构建行程生成提示词，用户输入={user_input}，上下文条数={len(context)}, 编辑指令={edit_note}")
        # 打印 user_input 的所有字段，确保每个属性都可见
        print(f"[{_ts()}][LlmManager] user_input.destination={user_input.get('destination')}")
        print(f"[{_ts()}][LlmManager] user_input.days={user_input.get('days')}")
        print(f"[{_ts()}][LlmManager] user_input.budget={user_input.get('budget')}")
        print(f"[{_ts()}][LlmManager] user_input.preference(raw)={user_input.get('preference')}")
        print(f"[{_ts()}][LlmManager] user_input.keys={list(user_input.keys())}")
        print("\n")
        # 打印上下文细节，逐条展示，便于确认是否存在空值或异常格式
        for idx, ctx in enumerate(context or []):
            print(f"[{_ts()}][LlmManager] context[{idx}]={ctx}")
            print("\n")
        # 安全处理 preference，保证后续 join 不报错
        preference = user_input.get("preference", [])
        # 如果 preference 是单字符串，转换为列表，保证统一处理逻辑
        if isinstance(preference, str):
            preference = [preference]
        # 如果 preference 既不是字符串也不是列表，则转成字符串列表兜底
        elif not isinstance(preference, list):
            preference = [str(preference)] if preference is not None else []
        # 拼接 preference 文本，供提示词 format 使用
        preference_str = ", ".join([str(p) for p in preference if p])
        # 打印归一化后的 preference 结果，确认拼接是否正确
        # print(f"[{_ts()}][LlmManager] preference(normalized)={preference}")
        # print(f"[{_ts()}][LlmManager] preference_str={preference_str}")
        # 预先组装上下文文本，避免 format 时隐藏错误
        context_text = "\n".join(context) if context else "无参考攻略"
        # 打印最终 format 输入的每一个属性，确保全部字段齐全
        # print(f"[{_ts()}][LlmManager] format.destination={user_input.get('destination')}")
        # print(f"[{_ts()}][LlmManager] format.days={user_input.get('days')}")
        # print(f"[{_ts()}][LlmManager] format.budget={user_input.get('budget')}")
        # print(f"[{_ts()}][LlmManager] format.preference={preference_str}")
        # print(f"[{_ts()}][LlmManager] format.context={context_text}")
        # print(f"[{_ts()}][LlmManager] format.edit_note={edit_note}")
        constraints_section = self._build_constraint_prompt_section(user_input)
        # 执行提示词格式化，若字段缺失会在此处抛出异常，前面的日志可辅助定位
        res = prompt.format(
            destination=user_input["destination"],
            days=user_input["days"],
            budget=user_input["budget"],
            preference=preference_str,
            context=context_text,
            edit_note=edit_note,
            constraints_section=constraints_section,
        )
        # print(f"[{_ts()}][LlmManager] 返回构建完成的提示词文本={res}")
        # 返回构建完成的提示词文本
        return res

    def _build_constraint_prompt_section(self, user_input: Dict[str, Any]) -> str:
        budget_level = str(user_input.get("budget_level") or "balanced").strip().lower()
        if budget_level not in BUDGET_LEVEL_MAP:
            budget_level = "balanced"
        intensity = str(user_input.get("intensity") or "standard").strip().lower()
        if intensity not in INTENSITY_MAP:
            intensity = "standard"
        pace = str(user_input.get("pace") or "cultural").strip().lower()
        if pace not in PACE_MAP:
            pace = "cultural"
        special = user_input.get("special_constraints") or {}
        if not isinstance(special, dict):
            special = {}
        special_parts: List[str] = []
        walking_limit_km = special.get("walking_limit_km")
        if walking_limit_km not in [None, ""]:
            special_parts.append(f"步行上限 {walking_limit_km}km/天")
        if bool(special.get("need_nap")):
            special_parts.append("需要午休，请在 12:00-14:00 预留休息或用餐缓冲")
        if bool(special.get("accessibility")):
            special_parts.append("优先考虑无障碍通道与无障碍友好的场所")
        special_text = "、".join(special_parts) if special_parts else "无特殊约束"
        return (
            f"- 预算档位：{budget_level}，{BUDGET_LEVEL_MAP[budget_level]}\n"
            f"- 体能强度：{intensity}，{INTENSITY_MAP[intensity]}\n"
            f"- 节奏偏好：{pace}，{PACE_MAP[pace]}\n"
            f"- 特殊约束：{special_text}"
        )

    def _normalize_conflict_constraints(self, constraints: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raw = constraints or {}
        if not isinstance(raw, dict):
            raw = {}
        special = raw.get("special_constraints") or {}
        if not isinstance(special, dict):
            special = {}
        return {
            "budget_level": str(raw.get("budget_level") or "balanced").strip().lower(),
            "intensity": str(raw.get("intensity") or "standard").strip().lower(),
            "pace": str(raw.get("pace") or "cultural").strip().lower(),
            "special_constraints": {
                "walking_limit_km": special.get("walking_limit_km"),
                "need_nap": bool(special.get("need_nap")),
                "accessibility": bool(special.get("accessibility")),
            },
        }

    def _normalize_daily_plan_items(self, trip_data: Optional[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        if not isinstance(trip_data, dict):
            return {}
        daily_plan = trip_data.get("daily_plan")
        if isinstance(daily_plan, dict):
            return {str(day): [item for item in items if isinstance(item, dict)] for day, items in daily_plan.items() if isinstance(items, list)}
        if isinstance(daily_plan, list):
            return {"1": [item for item in daily_plan if isinstance(item, dict)]}
        return {}

    def _parse_time_range_minutes(self, time_text: Any) -> Optional[tuple[int, int]]:
        match = re.match(r"^\s*(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\s*$", str(time_text or ""))
        if not match:
            return None
        start_minutes = int(match.group(1)) * 60 + int(match.group(2))
        end_minutes = int(match.group(3)) * 60 + int(match.group(4))
        return (start_minutes, end_minutes) if end_minutes > start_minutes else None

    def _extract_city_token(self, item: Dict[str, Any], fallback_city: str) -> str:
        city = str(item.get("city") or "").strip()
        if city:
            return city
        address = str(item.get("address") or "").strip()
        city_match = re.search(r"([^，,\s]+(?:市|州|地区|盟))", address)
        if city_match:
            return city_match.group(1)
        return fallback_city

    def _parse_transport_minutes(self, text: Any) -> int:
        value = str(text or "")
        total_minutes = 0
        hour_match = re.search(r"(\d+(?:\.\d+)?)\s*小时", value)
        if hour_match:
            total_minutes += int(float(hour_match.group(1)) * 60)
        minute_match = re.search(r"(\d+)\s*分钟", value)
        if minute_match:
            total_minutes += int(minute_match.group(1))
        if total_minutes == 0:
            compact_match = re.search(r"(\d+(?:\.\d+)?)h", value, flags=re.IGNORECASE)
            if compact_match:
                total_minutes += int(float(compact_match.group(1)) * 60)
        return total_minutes

    def _is_major_poi(self, item: Dict[str, Any]) -> bool:
        attraction = str(item.get("attraction") or "")
        exclude_keywords = ["午餐", "晚餐", "早餐", "午休", "休息", "返程", "入住", "酒店", "出发"]
        return attraction.strip() != "" and not any(keyword in attraction for keyword in exclude_keywords)

    def _is_rest_related_item(self, item: Dict[str, Any]) -> bool:
        merged_text = " ".join([
            str(item.get("attraction") or ""),
            str(item.get("transport") or ""),
            str(item.get("duration") or ""),
            str(item.get("recommend_reason") or ""),
            str(item.get("note") or ""),
        ])
        return any(keyword in merged_text for keyword in ["午休", "休息", "午睡", "酒店", "回酒店", "用餐", "午餐"])

    def _copy_trip_data(self, trip_data: Dict[str, Any]) -> Dict[str, Any]:
        return copy.deepcopy(trip_data if isinstance(trip_data, dict) else {})

    def _detect_conflicts(self, trip_data: Dict[str, Any], constraints: Optional[Dict[str, Any]] = None) -> ConflictReport:
        try:
            normalized_constraints = self._normalize_conflict_constraints(constraints)
            daily_plan = self._normalize_daily_plan_items(trip_data)
            if not daily_plan:
                return ConflictReport(has_conflicts=False)

            conflicts: List[ConflictItem] = []
            fallback_city = str((trip_data or {}).get("destination") or "").strip()
            intensity_limit_map = {"leisure": 3, "standard": 5}
            intensity = normalized_constraints.get("intensity", "standard")
            special = normalized_constraints.get("special_constraints") or {}

            for day_key, items in daily_plan.items():
                day = int(day_key) if str(day_key).isdigit() else 0
                if day <= 0:
                    continue

                # 核心逻辑：用城市标签 + 交通时长做轻量规则检测，优先拦截“同日跨城/超长交通”这类明显不可执行的方案。
                city_tokens = {
                    self._extract_city_token(item, fallback_city)
                    for item in items
                    if self._extract_city_token(item, fallback_city)
                }
                if len(city_tokens) >= 2:
                    conflicts.append(
                        ConflictItem(
                            type=ConflictType.DISTANCE_TIME,
                            day=day,
                            description=f"第{day}天存在跨城安排：{ '、'.join(sorted(city_tokens)) }，同日串联执行风险较高。",
                            severity="error",
                        )
                    )
                for item in items:
                    transport_minutes = self._parse_transport_minutes(item.get("transport"))
                    if transport_minutes > 120:
                        conflicts.append(
                            ConflictItem(
                                type=ConflictType.DISTANCE_TIME,
                                day=day,
                                description=f"第{day}天存在单段超过 {transport_minutes} 分钟的交通，连续游玩体验可能明显下降。",
                                severity="error" if transport_minutes >= 150 else "warning",
                            )
                        )
                        break

                major_poi_count = sum(1 for item in items if self._is_major_poi(item))
                intensity_limit = intensity_limit_map.get(intensity)
                if intensity_limit is not None and major_poi_count > intensity_limit:
                    conflicts.append(
                        ConflictItem(
                            type=ConflictType.PACE_DENSITY,
                            day=day,
                            description=f"第{day}天主要景点约 {major_poi_count} 个，已超过当前强度建议上限 {intensity_limit} 个。",
                            severity="error" if major_poi_count >= intensity_limit + 2 else "warning",
                        )
                    )

                lunch_window_hit = False
                nap_window_hit = False
                for item in items:
                    minutes_range = self._parse_time_range_minutes(item.get("time"))
                    if not minutes_range:
                        continue
                    start_minutes, end_minutes = minutes_range
                    if end_minutes > 11 * 60 + 30 and start_minutes < 13 * 60 + 30 and self._is_rest_related_item(item):
                        lunch_window_hit = True
                    if end_minutes > 12 * 60 and start_minutes < 14 * 60 and self._is_rest_related_item(item):
                        nap_window_hit = True
                if not lunch_window_hit:
                    conflicts.append(
                        ConflictItem(
                            type=ConflictType.REST_WINDOW,
                            day=day,
                            description=f"第{day}天在 11:30-13:30 区间未识别到明确的用餐或休息安排。",
                            severity="warning",
                        )
                    )
                if bool(special.get("need_nap")) and not nap_window_hit:
                    conflicts.append(
                        ConflictItem(
                            type=ConflictType.REST_WINDOW,
                            day=day,
                            description=f"第{day}天未满足 12:00-14:00 的午休需求，建议补充休息窗口。",
                            severity="error",
                        )
                    )

            # 同类同天冲突只保留一条最明确描述，避免前端警告区域被重复信息淹没。
            deduped: Dict[tuple[str, int, str], ConflictItem] = {}
            for item in conflicts:
                key = (item.type.value, item.day, item.description)
                deduped[key] = item
            deduped_conflicts = list(deduped.values())
            return ConflictReport(has_conflicts=bool(deduped_conflicts), conflicts=deduped_conflicts, alternatives=[])
        except Exception as exc:
            print(f"[{_ts()}][LlmManager] 冲突检测降级，原因={exc}")
            return ConflictReport(has_conflicts=False)

    def _generate_alternatives(
        self,
        trip_data: Dict[str, Any],
        conflicts: List[ConflictItem],
        constraints: Optional[Dict[str, Any]] = None,
    ) -> List[AlternativePlan]:
        if not conflicts:
            return []
        try:
            normalized_constraints = self._normalize_conflict_constraints(constraints)
            prompt = f"""
你是旅行方案修正助手。请基于当前行程与冲突描述，输出两个替代方案：
1. Plan A：更保守、更可执行，优先消除冲突。
2. Plan B：尽量保留原偏好，但允许保留少量风险。

请严格输出 JSON：
{{
  "alternatives": [
    {{
      "label": "Plan A",
      "strategy": "一句话说明策略",
      "trip_data": {json.dumps(trip_data, ensure_ascii=False)}
    }},
    {{
      "label": "Plan B",
      "strategy": "一句话说明策略",
      "trip_data": {json.dumps(trip_data, ensure_ascii=False)}
    }}
  ]
}}

当前行程：
{json.dumps(trip_data, ensure_ascii=False)}

当前约束：
{json.dumps(normalized_constraints, ensure_ascii=False)}

冲突列表：
{json.dumps([item.model_dump() for item in conflicts], ensure_ascii=False)}

要求：
- trip_data 必须保持现有结构，包含 destination/days/daily_plan。
- 必须真的调整存在冲突的天，不要只改 strategy 文案。
- 只输出 JSON，不要输出 Markdown 或解释。
"""
            raw_response = self.generation_llm.invoke(prompt)
            response_text = raw_response.content if hasattr(raw_response, "content") else raw_response
            cleaned_response = _strip_think_content(response_text)
            clean_json = self.extract_json_from_string(cleaned_response)
            parsed = json.loads(clean_json)
            alternatives_raw = parsed.get("alternatives") if isinstance(parsed, dict) else []
            alternatives: List[AlternativePlan] = []
            for index, item in enumerate(alternatives_raw or []):
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or f"Plan {'AB'[index] if index < 2 else index + 1}").strip()
                strategy = str(item.get("strategy") or "").strip()
                alternative_trip = item.get("trip_data") or {}
                if not isinstance(alternative_trip, dict) or not alternative_trip.get("daily_plan"):
                    continue
                alternatives.append(
                    AlternativePlan(
                        label=label,
                        strategy=strategy or "已调整冲突较高的行程段落",
                        trip_data=self._copy_trip_data(alternative_trip),
                    )
                )
            return alternatives[:2]
        except Exception as exc:
            print(f"[{_ts()}][LlmManager] 替代方案生成降级，原因={exc}")
            return []

    def build_conflict_report(self, trip_data: Dict[str, Any], constraints: Optional[Dict[str, Any]] = None) -> ConflictReport:
        conflict_report = self._detect_conflicts(trip_data, constraints)
        if not conflict_report.has_conflicts:
            self._metrics.record("conflict_detected", {"detected": False})
            self._metrics.record("plan_alternative_generated", {"generated": False})
            return conflict_report
        alternatives = self._generate_alternatives(trip_data, conflict_report.conflicts, constraints)
        self._metrics.record("conflict_detected", {"detected": True, "count": len(conflict_report.conflicts)})
        self._metrics.record("plan_alternative_generated", {"generated": bool(alternatives), "count": len(alternatives)})
        return ConflictReport(
            has_conflicts=True,
            conflicts=conflict_report.conflicts,
            alternatives=alternatives,
        )

    def _build_constraints_context(self, user_input: Dict[str, Any]) -> str:
        destination = user_input.get("destination")
        days = user_input.get("days")
        if not destination or not days:
            return ""
        return f"行程硬约束信息：目的地 {destination}，天数 {days}。天气、交通时长与费用、POI 开放时间与票价等将通过外部工具和 API 获取，并在规划和修改行程时作为需要严格遵守的约束条件。"

    def extract_json_from_string(self, response: str) -> str:
        print(f"[{_ts()}][LlmManager] extract_json_from_string 原始响应长度={len(str(response))}")

        # < think >
        # 好，我来分析一下用户的需求。用户说要从上海出发去广州，5
        # 天，预算1000元，侧重美食，需要每天的行程，并且按照半小时规划内容。
        # 首先，用户的意图类型应该是“generate_trip”，因为他是在生成一个行程计划，而不是修改现有的行程、添加或删除景点等。
        # 接下来提取关键参数：目的地是广州，天数是5天，预算为1000元，偏好美食。用户希望每天的行程按照半小时来规划内容。
        # 总结一下，用户需要一份5天的行程计划，从上海到广州，预算有限，重点放在美食体验上，并且每天的时间安排要详细到半小时级别。
        # 最后，是否需要更多信息？看起来用户已经提供了足够的信息，包括出发地、目的地、天数、预算和偏好，所以不需要额外的信息。
        # < / think >
        # ```json
        # {
        #     "intent": "generate_trip",
        #     "parameters": {
        #         "destination": "广州",
        #         "days": 5,
        #         "budget": 1000,
        #         "preference": "美食",
        #         "day_number": [1, 2, 3, 4, 5]
        #     },
        #     "summary": "为用户规划从上海到广州的5天行程，预算1000元，侧重美食体验，并按半小时详细安排每天的内容。",
        #     "needs_more_info": false
        # }
        # ```

        # 1. 移除 DeepSeek 的思考过程，就是上面的<think></think>的内容
        content = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()

        # 1.1 尝试修复常见的 JSON 错误：数组元素或对象之间缺少逗号
        # 例如：} {  ->  }, {
        # 以及 ] [ -> ], [ (虽然较少见)
        content = re.sub(r'\}\s*\{', '}, {', content)
        content = re.sub(r'\]\s*\[', '], [', content)

        # 1.2 修复常见的 Python 常量到 JSON 的错误
        # None -> null, True -> true, False -> false
        # 使用非捕获组 (?:...) 和 lookbehind/lookahead 可能会复杂，这里使用简单的替换
        # 假设这些关键字出现在冒号之后，且作为值
        content = re.sub(r':\s*None\b', ': null', content)
        content = re.sub(r':\s*True\b', ': true', content)
        content = re.sub(r':\s*False\b', ': false', content)
        # 修复单引号 (简单处理，可能会误伤，但在 JSON 上下文中通常是键或值被单引号包裹)
        # 更好的做法是只替换键的单引号，或者依赖解析器的宽容度（但标准 JSON 不支持单引号）
        # 这里暂不处理单引号，因为风险较大，且 LLM 生成 JSON 通常能遵守双引号规则

        # 2. 优先匹配 Markdown 代码块，就是上面的```json```的内容
        code_block = re.search(r'```json\s*(\{.*?\})\s*```', content, re.DOTALL)
        if code_block:
            return code_block.group(1)

        # 3. 如果没有 Markdown，尝试提取最外层的 JSON 对象
        # 找到第一个 '{' 和最后一个 '}'
        start_idx = content.find('{')
        end_idx = content.rfind('}')

        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
             return content[start_idx : end_idx + 1]

        return content

    def _execute_trip_generation(self, prompt: str) -> Optional[Dict[str, Any]]:
        """执行行程生成的通用循环逻辑（重试、解析、指标记录）"""
        attempt = 1
        self._metrics.record(
            "llm_generate_trip_start",
            {"attempt": attempt, "prompt_len": len(prompt)},
        )
        start_ts = datetime.now()
        print(f"[{_ts()}][LlmManager] 第{attempt}次行程生成调用开始")
        request_id = f"generation-{start_ts.strftime('%H%M%S%f')}"
        cfg = self._generation_config
        print(
            f"[{_ts()}][LlmManager] llm_http_request_start stage=trip_generation_invoke role=generation "
            f"provider={cfg.get('provider')} base_url={cfg.get('base_url')} model={cfg.get('model_name')} "
            f"request_id={request_id} attempt={attempt}"
        )
        raw_response = self.generation_llm.invoke(prompt)
        if hasattr(raw_response, "content"):
            response_text = raw_response.content
        else:
            response_text = raw_response

        cleaned_response_text = _strip_think_content(response_text)
        _log_llm_output("trip_generation_response", cleaned_response_text)
        clean_response = self.extract_json_from_string(cleaned_response_text)
        trip_data = self.parser.parse(clean_response)

        cost = (datetime.now() - start_ts).total_seconds()
        print(
            f"[{_ts()}][LlmManager] llm_http_request_end stage=trip_generation_invoke role=generation "
            f"elapsed_s={cost:.2f} response_len={len(str(response_text))} request_id={request_id} attempt={attempt}"
        )
        print(f"[{_ts()}][LlmManager] 第{attempt}次生成成功，耗时 {cost:.2f}s")
        self._metrics.record(
            "llm_generate_trip_success",
            {"attempt": attempt, "elapsed_ms": int(cost * 1000), "output_len": len(str(response_text))},
        )

        if hasattr(trip_data, "model_dump") and callable(getattr(trip_data, "model_dump")):
            return trip_data.model_dump()
        if isinstance(trip_data, dict):
            return trip_data
        raise TypeError(f"解析器返回了意外类型: {type(trip_data)}")

    def generate_trip_pure(self, user_input: Dict[str, Any], context: List[str],
                           edit_cmd: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        纯生成行程：不包含内置工具调用逻辑，仅基于传入的上下文和约束生成行程。
        专供 Agent 模式使用，因为 Agent 已通过独立的 Task 完成了工具调用。
        """
        constraints_text = self._build_constraints_context(user_input)
        merged_context: List[str] = []
        if context:
            merged_context.extend(context)
        if constraints_text:
            merged_context.append(constraints_text)
            
        prompt = self.build_prompt(user_input, merged_context, edit_cmd)
        
        print(f"[{_ts()}][LlmManager] generate_trip_pure start, destination={user_input.get('destination')}, days={user_input.get('days')}")
        return self._execute_trip_generation(prompt)

    def generate_trip(self, user_input: Dict[str, Any], context: List[str],
                      edit_cmd: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        print("-------------------------------一次性generate_trip（非流式）生成 触发-------------------------------")
        """生成行程：通过可视化界面操作输入文字进行行程的生成"""
        prompt = self.build_trip_prompt(user_input, context, edit_cmd)

        print(f"[{_ts()}][LlmManager] generate_trip start, destination={user_input.get('destination')}, days={user_input.get('days')}, attempt_count=2")
        return self._execute_trip_generation(prompt)

    def analyze_user_message(
        self,
        query: str,
        context: Optional[List[Dict[str, str]]] = None,
        current_trip: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """仅做第 1 次调用：实体抽取 + 意图识别"""
        analysis_prompt = self._build_analysis_prompt(query, context, current_trip)
        print(f"[{_ts()}][LlmManager] 实体抽取+意图识别 prompt 构建完成，query={query}")

        self._metrics.record(
            "llm_analyze_start",
            {"prompt_len": len(analysis_prompt), "context_size": len(context or [])},
        )
        start_ts = datetime.now()
        print(f"[{_ts()}][LlmManager] 实体抽取+意图识别 => LLM 调用开始")
        request_id = f"analysis-{start_ts.strftime('%H%M%S%f')}"
        raw_analysis_response = self.analysis_llm.invoke(analysis_prompt)
        if hasattr(raw_analysis_response, "content"):
            analysis_response = raw_analysis_response.content
        else:
            analysis_response = raw_analysis_response

        cost = (datetime.now() - start_ts).total_seconds()
        cleaned_response = _strip_think_content(analysis_response)
        _log_llm_output("analyze_user_message_response", cleaned_response)
        print(f"[{_ts()}][LlmManager] 实体抽取+意图识别 LLM 调用结束，耗时 {cost:.2f}s，原始响应长度={len(str(analysis_response))}")

        intent_data = self._parse_intent(cleaned_response)
        print(f"[{_ts()}][LlmManager] 实体抽取+意图识别 解析出的意图数据={intent_data}")
        self._metrics.record(
            "llm_analyze_success",
            {"elapsed_ms": int(cost * 1000), "output_len": len(str(analysis_response))},
        )
        return intent_data

    def change_trip(
        self,
        query: str,
        context: List[Dict[str, str]] = None,
        current_trip: Dict = None,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        根据用户查询调整行程，支持多轮对话上下文（不仅仅是调整行程，还支持初始化生成行程）
        """
        intent_data = self.analyze_user_message(query, context, current_trip)

        if intent_data["intent"] == "general_conversation":
            # 对话型请求优先走工具查询，避免误触发行程生成
            tool_call = self.call_tool_by_llm(query, context)
            if tool_call.get("needs_tool") and tool_call.get("result"):
                result_payload = tool_call.get("result")
                if isinstance(result_payload, dict) and result_payload.get("success"):
                    return {
                        "response": f"工具结果：{json.dumps(result_payload.get('data'), ensure_ascii=False)}",
                        "trip_data": None,
                        "intent": "tool_response"
                    }

        if intent_data["intent"] == "generate_trip":
            return self._handle_trip_generation(intent_data, context)
        elif intent_data["intent"] in ["modify_trip", "add_attraction", "delete_attraction", "reorder_trip"]:
            if current_trip:
                return self._handle_trip_modification(intent_data, current_trip, context, constraints=constraints)
            else:
                return {
                    "response": "我需要先为您生成一个基础行程，然后才能进行调整。请先提供目的地、天数和预算信息。",
                    "trip_data": None
                }
        else:
            return {
                "response": f"我理解您想{intent_data.get('summary', '进一步讨论行程')}. 请告诉我更多细节，比如目的地、旅行天数和您的偏好，我可以为您规划具体的行程。",
                "trip_data": None
            }

    def _build_analysis_prompt(self, query: str, context: List[Dict[str, str]], current_trip: Dict) -> str:
        """构建意图分析提示词"""
        context_text = ""
        if context:
            recent_messages = context[-6:]  # 取最近6条消息
            context_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_messages])

        trip_summary = ""
        if current_trip:
            trip_summary = f"""
            当前行程概况:
            目的地: {current_trip.get('destination', '未知')}
            天数: {current_trip.get('days', '未知')}天
            每日安排: {len(current_trip.get('daily_plan', {}))}天已规划
            """

        template = """
        你是一个专业的旅游规划助手，需要分析用户的意图并提取关键信息。

        【对话上下文】
        {context_text}

        【当前行程状态】
        {trip_summary}

        【用户最新输入】
        {query}

        请分析用户意图，并用JSON格式返回以下信息:
        1. intent: 意图类型（"generate_trip", "modify_trip", "add_attraction", "delete_attraction", "reorder_trip", "general_conversation"）
        2. parameters: 提取的关键参数（destination, days, budget, preference, attraction_name, day_number, etc.）
        3. summary: 用一句话总结用户需求
        4. needs_more_info: 是否需要更多信息才能执行操作 (true/false)
        5. missing_info: 如果 needs_more_info 为 true，列出具体缺少的关键信息列表（如 ["目的地", "天数", "预算"]），否则返回空列表 []

        注意：只返回JSON格式，不要包含其他文本，不包含任何解释性文字或Markdown格式（如```json```），必须严格遵守以下 JSON Schema 结构，确保每一行键值对后都有正确的逗号，且最后一个键值对后不加逗号，禁止在 JSON 中使用 range()、tuple() 等 Python 函数。day_number 必须是一个整数列表，例如 [1, 2, 3, 4, 5]。
        """

        return template.format(
            context_text=context_text or "无历史对话",
            trip_summary=trip_summary or "无当前行程",
            query=query
        )

    def _parse_intent(self, response: str) -> Dict[str, Any]:
        """解析意图分析结果"""
        try:
            # 提取JSON内容
            clean_response = self.extract_json_from_string(response)
            # 处理可能的非JSON响应
            if not clean_response.startswith('{'):
                # 尝试从文本中提取JSON
                json_match = re.search(r'\{.*\}', clean_response, re.DOTALL)
                if json_match:
                    clean_response = json_match.group(0)
                else:
                    # 如果找不到JSON，返回默认结构
                    return self._get_default_intent()

            intent_data = json.loads(clean_response)

            # 验证必需字段
            if "intent" not in intent_data:
                intent_data["intent"] = "general_conversation"

            # 确保 parameters 是字典
            if "parameters" not in intent_data or not isinstance(intent_data["parameters"], dict):
                intent_data["parameters"] = {}

            # 确保所有参数字段都有安全的默认值
            safe_params = {}
            for key in ["destination", "days", "budget", "preference", "attraction_name", "day_number"]:
                value = intent_data["parameters"].get(key)
                if value is None or value == "null" or value == "":
                    if key in ["days", "budget", "day_number"]:
                        # JSON 中不允许 None，使用空字符串或 0 占位
                        safe_params[key] = ""
                    elif key in ["destination", "attraction_name"]:
                        safe_params[key] = []
                    elif key == "preference":
                        safe_params[key] = []
                else:
                    safe_params[key] = value

            days_raw = safe_params.get("days")
            if isinstance(days_raw, list):
                numeric_days: List[int] = []
                for v in days_raw:
                    try:
                        numeric_days.append(int(v))
                    except (TypeError, ValueError):
                        continue
                safe_params["days"] = max(numeric_days) if numeric_days else ""
            elif isinstance(days_raw, (int, float)):
                safe_params["days"] = int(days_raw)
            elif isinstance(days_raw, str) and days_raw.strip():
                try:
                    safe_params["days"] = int(float(days_raw.strip()))
                except ValueError:
                    safe_params["days"] = ""

            budget_raw = safe_params.get("budget")
            if isinstance(budget_raw, list):
                numeric_budget: List[float] = []
                for v in budget_raw:
                    try:
                        numeric_budget.append(float(v))
                    except (TypeError, ValueError):
                        continue
                safe_params["budget"] = int(numeric_budget[0]) if numeric_budget else ""
            elif isinstance(budget_raw, (int, float)):
                safe_params["budget"] = int(budget_raw)
            elif isinstance(budget_raw, str) and budget_raw.strip():
                try:
                    safe_params["budget"] = int(float(budget_raw.strip()))
                except ValueError:
                    safe_params["budget"] = ""

            pref_raw = safe_params.get("preference")
            if isinstance(pref_raw, str) and pref_raw.strip():
                safe_params["preference"] = [p.strip() for p in pref_raw.split(",") if p.strip()]
            intent_data["parameters"] = safe_params



            # 确保 needs_more_info 存在
            if "needs_more_info" not in intent_data:
                # 如果关键参数缺失，设置为需要更多信息
                key_params = ["destination", "days", "budget"]
                missing_key_params = [p for p in key_params if not safe_params.get(p)]
                intent_data["needs_more_info"] = len(missing_key_params) > 0
                if intent_data["needs_more_info"]:
                     intent_data["missing_info"] = [{"destination": "目的地", "days": "旅行天数", "budget": "预算"}.get(k, k) for k in missing_key_params]

            if "missing_info" not in intent_data:
                intent_data["missing_info"] = []



            return intent_data

        except Exception as e:
            print(f"[{_ts()}][LlmManager] 意图解析失败: {str(e)}, 使用默认意图")
            return self._get_default_intent()

    def _get_default_intent(self) -> Dict[str, Any]:
        """返回默认的意图结构"""
        return {
            "intent": "general_conversation",
            "parameters": {
                "destination": None,
                "days": None,
                "budget": None,
                "preference": None,
                "attraction_name": None,
                "day_number": None
            },
            "summary": "用户想要了解或调整行程",
            "needs_more_info": True,
            "missing_info": []
        }

    def _handle_trip_generation(self, intent_data: Dict[str, Any], context: List[Dict[str, str]]) -> Dict[str, Any]:
        """处理新行程生成请求"""
        prepared = self.prepare_trip_request_from_intent(intent_data, context)
        if prepared.get("needs_more_info"):
            missing_info = prepared.get("missing_info", [])
            return {
                "response": f"我需要更多信息才能为您生成行程。请提供以下信息：{', '.join(missing_info)}。例如：'我想去成都玩3天，预算5000元'",
                "trip_data": None,
                "needs_more_info": True,
            }

        user_input = prepared.get("user_input") or {}
        context_texts = prepared.get("context_texts") or []
        # 生成行程
        trip_data = self.generate_trip(user_input, context_texts)

        if isinstance(trip_data, dict):
            daily_plan = trip_data.get("daily_plan") or {}
            total_items = 0
            if isinstance(daily_plan, dict):
                for _, v in daily_plan.items():
                    if isinstance(v, list):
                        total_items += len(v)
            print(f"[{_ts()}][LlmManager] 生成的行程数据概览: days={trip_data.get('days')}, daily_plan_days={len(daily_plan)}, items={total_items}")
        else:
            print(f"[{_ts()}][LlmManager] 生成的行程数据类型: {type(trip_data)}, str_len={len(str(trip_data))}")

        if trip_data:
            destination = user_input.get("destination", "目的地")
            days = user_input.get("days", "未知")
            budget = user_input.get("budget", "未知")
            return {
                "response": f"太棒了！我已经为您规划了去{destination}的{days}天行程，预算{budget}元。行程已生成，请查看右侧地图和详细安排！",
                "trip_data": trip_data,
                "intent": "trip_generated"
            }
        else:
            destination = user_input.get("destination", "未知")
            days = user_input.get("days", "未知")
            budget = user_input.get("budget", "未知")
            fallback_response = (
                "抱歉，我无法生成满足您要求的行程。"
                f"我尝试使用以下参数：目的地={destination}, 天数={days}, 预算={budget}。"
                "请尝试调整这些参数，或者提供更具体的需求。"
            )
            return {
                "response": fallback_response,
                "trip_data": None
            }

    def _handle_trip_modification(
        self,
        intent_data: Dict[str, Any],
        current_trip: Dict,
        context: List[Dict[str, str]],
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        print(f"[{_ts()}][LlmManager] 处理行程修改请求，intent={intent_data.get('intent')}, params_keys={list((intent_data.get('parameters') or {}).keys())}")
        prepared = self.prepare_trip_request_from_modification(intent_data, current_trip, context, constraints=constraints)
        user_input = prepared.get("user_input") or {}
        context_texts = prepared.get("context_texts") or []
        edit_cmd = prepared.get("edit_cmd")
        action_desc = prepared.get("action_desc") or "已调整行程"
        modified_trip = self.generate_trip(user_input, context_texts, edit_cmd)

        if isinstance(modified_trip, dict):
            daily_plan = modified_trip.get("daily_plan") or {}
            total_items = 0
            if isinstance(daily_plan, dict):
                for _, v in daily_plan.items():
                    if isinstance(v, list):
                        total_items += len(v)
            print(f"[{_ts()}][LlmManager] 处理行程修改完成: daily_plan_days={len(daily_plan)}, items={total_items}")
        else:
            print(f"[{_ts()}][LlmManager] 处理行程修改完成: type={type(modified_trip)}, str_len={len(str(modified_trip))}")

        if modified_trip:
            return {
                "response": f"{action_desc}。新的行程已生成，请查看更新后的安排！",
                "trip_data": modified_trip,
                "intent": "trip_modified"
            }
        else:
            return {
                "response": "行程调整失败。请尝试更具体的修改要求，或重新生成行程。",
                "trip_data": None,
            }

    def prepare_trip_request_from_modification(
        self,
        intent_data: Dict[str, Any],
        current_trip: Dict[str, Any],
        context: List[Dict[str, str]],
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params = intent_data.get("parameters", {})
        intent_type = intent_data.get("intent")
        edit_cmd = None
        if intent_type == "add_attraction":
            edit_cmd = {
                "type": "add",
                "attraction": params.get("attraction_name", "新景点"),
                "day": int(params.get("day_number", 1)),
            }
        elif intent_type == "delete_attraction":
            edit_cmd = {
                "type": "delete",
                "attraction": params.get("attraction_name", "景点"),
                "day": int(params.get("day_number", 1)),
            }
        elif intent_type == "reorder_trip":
            edit_cmd = {
                "type": "reorder",
                "msg": "调整行程顺序",
            }
        elif intent_type == "modify_trip":
            summary = intent_data.get("summary", "调整行程")
            edit_cmd = {
                "type": "modify",
                "msg": summary,
                "updates": {
                    "destination": params.get("destination"),
                    "days": params.get("days"),
                    "budget": params.get("budget"),
                    "preference": params.get("preference"),
                },
            }
        updates = edit_cmd.get("updates", {}) if edit_cmd else {}
        merged_constraints = constraints or current_trip.get("constraints_used") or {}
        user_input = {
            "destination": updates.get("destination") or current_trip.get("destination", "成都"),
            "days": updates.get("days") or current_trip.get("days", 3),
            "budget": updates.get("budget") or current_trip.get("budget", 5000),
            "preference": updates.get("preference") or current_trip.get("preference", ["美食", "历史"]),
            "budget_level": merged_constraints.get("budget_level", "balanced"),
            "intensity": merged_constraints.get("intensity", "standard"),
            "pace": merged_constraints.get("pace", "cultural"),
            "special_constraints": merged_constraints.get("special_constraints") or {},
            "guide_links": [],
        }
        context_texts = [msg["content"] for msg in context[-3:]] if context else []
        action_desc = "已调整行程"
        if edit_cmd and "type" in edit_cmd:
            action_desc = {
                "add": f"已成功在第{edit_cmd.get('day')}天添加{edit_cmd.get('attraction')}",
                "delete": f"已成功从第{edit_cmd.get('day')}天删除{edit_cmd.get('attraction')}",
                "reorder": "已重新优化行程顺序",
                "modify": edit_cmd.get("msg", "已根据您的要求调整行程"),
            }.get(edit_cmd["type"], "已调整行程")
        return {
            "user_input": user_input,
            "context_texts": context_texts,
            "edit_cmd": edit_cmd,
            "action_desc": action_desc,
        }
