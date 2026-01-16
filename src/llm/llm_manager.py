from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
import torch  # 仍然保留，以防其他部分使用，但在 __init__ 中不再强制需要

from langchain_ollama import OllamaLLM
import json
import re


# 行程数据模型
class DailyPlanItem(BaseModel):
    time: str = Field(description="格式如'09:00-11:00'")
    attraction: str = Field(description="景点名称，必须准确")
    address: str = Field(description="详细地址，包含街道门牌号")
    transport: str = Field(description="具体交通方式，如'地铁2号线→步行5分钟'")
    duration: str = Field(description="停留时间，如'2小时'")


class TripPlan(BaseModel):
    destination: str = Field(description="旅行目的地城市")
    days: int = Field(description="旅行总天数")
    daily_plan: Dict[str, List[DailyPlanItem]] = Field(
        description="键为'1','2'等字符串，值为当天行程列表"
    )


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

        self._llm_config: Dict[str, Any] = {
            "provider": provider,
            "base_url": base_url,
            "model_name": model_name,
            "api_key": api_key,
            "temperature": temperature,
        }

        self.llm = self._create_llm()
        self.parser = JsonOutputParser(pydantic_object=TripPlan)

    def _create_llm(self):
        provider = self._llm_config.get("provider") or "ollama"
        model_name = self._llm_config.get("model_name") or "deepseek-r1:7b"
        base_url = self._llm_config.get("base_url") or "http://localhost:11434"
        temperature = self._llm_config.get("temperature") or 0.7

        if provider == "openai_compatible":
            try:
                from langchain_openai import ChatOpenAI  # 延迟导入，避免不兼容版本在启动时崩溃
            except ImportError as e:
                raise RuntimeError(
                    "当前环境的 langchain_openai 与 langchain_core 版本不兼容，暂不支持 OpenAI 兼容模型。"
                    "请升级相关依赖或切换 provider=ollama。"
                ) from e

            api_key = self._llm_config.get("api_key") or ""
            print(f"✅ 初始化 OpenAI 兼容模型: {model_name}...")
            return ChatOpenAI(
                model=model_name,
                api_key=api_key,
                base_url=base_url,
                temperature=temperature,
            )

        print(f"✅ 初始化 Ollama 模型: {model_name}...")
        return OllamaLLM(
            base_url=base_url,
            model=model_name,
            temperature=temperature,
            num_ctx=4096,
            timeout=300,
        )

    def update_llm_config(self, config: Dict[str, Any]) -> None:
        if not hasattr(self, "_llm_config"):
            self._llm_config = {}
        self._llm_config.update(config)
        self.llm = self._create_llm()

    def get_llm(self):
        return self.llm

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
                        "time": "09:00-11:00",
                        "attraction": "景点A",
                        "address": "地址A",
                        "transport": "交通A",
                        "duration": "2小时"
                    }},
                    {{
                        "time": "11:00-13:00",
                        "attraction": "午餐",
                        "address": "地址B",
                        "transport": "步行",
                        "duration": "2小时"
                    }}
                ]
            }}
        }}

        【行程约束】
        - 目的地：{destination}
        - 总天数：{days}天
        - 预算：{budget}元/人（请合理分配交通、餐饮开支）
        - 偏好：{preference}
        - 额外要求：{edit_note}

        【参考攻略（优先采纳）】
        {context}

        【细节规范】
        - 每天行程安排在8:00-18:00，时间必须连续且无冲突。
        - 地址必须精确到街道和门牌号（如"成都市青羊区青华路9号"）。
        - 交通方式具体（如"地铁2号线人民公园站B口出"）。

        【Schema 定义】
        {format_instructions}
        """
        prompt = PromptTemplate(
            template=template.strip(),
            input_variables=["destination", "days", "budget", "preference", "context", "edit_note"],
            partial_variables={
                "format_instructions": self.parser.get_format_instructions(),
                "json_example_correct": '[{"a":1}, {"b":2}]',
                "json_example_wrong": '[{"a":1} {"b":2}]'
            }
        )

        print(f"""构建提示词，用户输入为：{user_input}，上下文为：{context}，编辑指令为：{edit_note}""")

        # 安全处理 preference
        preference = user_input.get("preference", [])
        if isinstance(preference, str):
            preference = [preference]
        elif not isinstance(preference, list):
            preference = [str(preference)] if preference is not None else []
            
        preference_str = ", ".join([str(p) for p in preference if p])

        return prompt.format(
            destination=user_input["destination"],
            days=user_input["days"],
            budget=user_input["budget"],
            preference=preference_str,
            context="\n".join(context) if context else "无参考攻略",
            edit_note=edit_note
        )

    def _build_constraints_context(self, user_input: Dict[str, Any]) -> str:
        destination = user_input.get("destination")
        days = user_input.get("days")
        if not destination or not days:
            return ""
        return f"行程硬约束信息：目的地 {destination}，天数 {days}。天气、交通时长与费用、POI 开放时间与票价等将通过外部工具和 API 获取，并在规划和修改行程时作为需要严格遵守的约束条件。"

    def extract_json_from_string(self, response: str) -> str:
        print(f"DEBUG: 原始响应全文内容 ---> {response}")

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

    def generate_trip(self, user_input: Dict[str, Any], context: List[str],
                      edit_cmd: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """生成行程：通过可视化界面操作输入文字进行行程的生成"""
        constraints_text = self._build_constraints_context(user_input)
        merged_context: List[str] = []
        if context:
            merged_context.extend(context)
        if constraints_text:
            merged_context.append(constraints_text)
        prompt = self.build_prompt(user_input, merged_context, edit_cmd)

        # 进行两次尝试
        for attempt in range(2):
            try:
                raw_response = self.llm.invoke(prompt)
                if hasattr(raw_response, "content"):
                    response_text = raw_response.content
                else:
                    response_text = raw_response

                clean_response = self.extract_json_from_string(response_text)

                # 尝试解析
                # self.parser.parse() 应该返回一个 TripPlan 实例 或一个 dict
                trip_data = self.parser.parse(clean_response)

                print(f"✅ 第{attempt + 1}次生成成功。")

                # 🌟 Pydantic V2 安全处理：
                # 如果返回的是 Pydantic 实例，使用 .model_dump() 转换为 dict
                if hasattr(trip_data, 'model_dump') and callable(getattr(trip_data, 'model_dump')):
                    return trip_data.model_dump()
                # 如果返回的就是 dict，则直接返回
                elif isinstance(trip_data, dict):
                    return trip_data
                else:
                    # 处理未预期的返回类型
                    raise TypeError(f"解析器返回了意外类型: {type(trip_data)}")

            except Exception as e:
                # 捕获 JSON 解析错误
                print(f"❌ 第{attempt + 1}次生成失败，尝试重新生成。错误：{str(e)}")
                if attempt == 1:
                    return None

    def change_trip(self, query: str, context: List[Dict[str, str]] = None, current_trip: Dict = None) -> Dict[str, Any]:
        """
        根据用户查询调整行程，支持多轮对话上下文（不仅仅是调整行程，还支持初始化生成行程）
        1.理解用户意图：通过LLM分析用户输入，识别是生成新行程还是修改现有行程
        2.上下文感知：结合对话历史和当前行程状态做出智能决策
        3.实体提取：自动提取目的地、天数、预算、偏好等关键参数
        4.多种操作支持：支持添加景点、删除景点、调整顺序等操作
        5.结构化响应：返回包含对话回复和行程数据的结构化结果
        Args:
            query: 用户输入的调整指令
            context: 对话上下文列表，包含历史消息
            current_trip: 当前行程数据（如果存在）

        Returns:
            包含响应文本和可选行程数据的字典
        """
        # 1. 构建分析提示词，理解用户意图
        analysis_prompt = self._build_analysis_prompt(query, context, current_trip)
        print(f"change_trip解析意图的prompt为：{analysis_prompt}")

        raw_analysis_response = self.llm.invoke(analysis_prompt)
        if hasattr(raw_analysis_response, "content"):
            analysis_response = raw_analysis_response.content
        else:
            analysis_response = raw_analysis_response

        print(f"通过llm分析出用户的意图是： {analysis_response}")

        intent_data = self._parse_intent(analysis_response)

        print(f"解析llm返回的意图数据为：{intent_data}")

        # 3. 根据意图类型处理
        if intent_data["intent"] == "generate_trip":
            return self._handle_trip_generation(intent_data, context)
        elif intent_data["intent"] in ["modify_trip", "add_attraction", "delete_attraction", "reorder_trip"]:
            if current_trip:
                return self._handle_trip_modification(intent_data, current_trip, context)
            else:
                return {
                    "response": "我需要先为您生成一个基础行程，然后才能进行调整。请先提供目的地、天数和预算信息。",
                    "trip_data": None
                }
        else:  # general_conversation
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
            print(f"❌ 意图解析失败: {str(e)}, 使用默认意图")
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
        params = intent_data.get("parameters", {})
        needs_more_info = intent_data.get("needs_more_info", True)

        # 1. 首先检查是否需要更多信息
        if needs_more_info:
            missing_info = intent_data.get("missing_info", [])

            # 如果 LLM 没有返回 missing_info，尝试自动检测
            if not missing_info:
                # 检查必需参数
                if not params.get("destination") or params["destination"] in [None, [], ""]:
                    missing_info.append("目的地")
                if not params.get("days") or params["days"] is None:
                    missing_info.append("旅行天数")
                if not params.get("budget") or params["budget"] is None:
                    missing_info.append("预算")

            if missing_info:
                return {
                    "response": f"我需要更多信息才能为您生成行程。请提供以下信息：{', '.join(missing_info)}。例如：'我想去成都玩3天，预算5000元'",
                    "trip_data": None,
                    "needs_more_info": True
                }

        # 2. 安全地提取和转换参数
        # 处理 destination - 可能是列表或字符串
        destination_raw = params.get("destination", "成都")
        if isinstance(destination_raw, list) and destination_raw:
            destination = destination_raw[0]  # 取第一个目的地
        elif isinstance(destination_raw, str) and destination_raw.strip():
            destination = destination_raw.strip()
        else:
            destination = "成都"

        # 处理 days - 安全转换
        days_raw = params.get("days")
        try:
            days = int(days_raw) if days_raw not in [None, ""] else 3
            days = max(1, min(days, 15))  # 限制在1-15天范围内
        except (TypeError, ValueError):
            days = 3

        # 处理 budget - 安全转换
        budget_raw = params.get("budget")
        try:
            budget = int(budget_raw) if budget_raw not in [None, ""] else 5000
            budget = max(1000, min(budget, 50000))  # 限制在1000-50000元范围内
        except (TypeError, ValueError):
            budget = 5000

        # 处理 preference - 处理各种可能的格式
        preference_raw = params.get("preference")
        if isinstance(preference_raw, list) and preference_raw:
            # 确保列表中的元素都是字符串
            preference = [str(p) for p in preference_raw if p]
        elif isinstance(preference_raw, str) and preference_raw.strip():
            preference = [pref.strip() for pref in preference_raw.split(",") if pref.strip()]
        else:
            preference = ["美食", "历史"]

        # 3. 从上下文中补充缺失的信息
        if context:
            for msg in reversed(context[-5:]):  # 检查最近5条消息
                content = msg["content"].lower()

                # 从上下文中提取天数
                if days == 3:  # 如果使用的是默认值，尝试从上下文获取
                    day_match = re.search(r'(\d+)[\s]*天', content)
                    if day_match:
                        try:
                            days = max(1, min(int(day_match.group(1)), 15))
                        except ValueError:
                            pass

                # 从上下文中提取预算
                if budget == 5000:  # 如果使用的是默认值，尝试从上下文获取
                    budget_match = re.search(r'(\d+)[\s]*(?:元|块|rmb)', content)
                    if budget_match:
                        try:
                            budget = max(1000, min(int(budget_match.group(1)), 50000))
                        except ValueError:
                            pass

        # 4. 构建用户输入格式
        user_input = {
            "destination": destination,
            "days": days,
            "budget": budget,
            "preference": preference if isinstance(preference, list) else [preference],
            "guide_links": []
        }

        # 5. 从上下文中提取攻略信息
        context_texts = [msg["content"] for msg in context[-3:]] if context else []

        # 6. 生成行程
        trip_data = self.generate_trip(user_input, context_texts)

        print(f"""生成的行程数据为：{trip_data}""")

        if trip_data:
            return {
                "response": f"太棒了！我已经为您规划了去{destination}的{days}天行程，预算{budget}元。行程已生成，请查看右侧地图和详细安排！",
                "trip_data": trip_data,
                "intent": "trip_generated"
            }
        else:
            fallback_response = (
                "抱歉，我无法生成满足您要求的行程。"
                f"我尝试使用以下参数：目的地={destination}, 天数={days}, 预算={budget}。"
                "请尝试调整这些参数，或者提供更具体的需求。"
            )
            return {
                "response": fallback_response,
                "trip_data": None
            }

    def _handle_trip_modification(self, intent_data: Dict[str, Any], current_trip: Dict,
                                  context: List[Dict[str, str]]) -> Dict[str, Any]:
        """处理行程修改请求"""
        print(f"处理行程修改请求，目前意图数据是：{intent_data}")
        params = intent_data.get("parameters", {})
        intent_type = intent_data["intent"]

        edit_cmd = None

        if intent_type == "add_attraction":
            edit_cmd = {
                "type": "add",
                "attraction": params.get("attraction_name", "新景点"),
                "day": int(params.get("day_number", 1))
            }
        elif intent_type == "delete_attraction":
            edit_cmd = {
                "type": "delete",
                "attraction": params.get("attraction_name", "景点"),
                "day": int(params.get("day_number", 1))
            }
        elif intent_type == "reorder_trip":
            edit_cmd = {
                "type": "reorder",
                "msg": "调整行程顺序"
            }
        elif intent_type == "modify_trip":
            # 通用修改逻辑
            summary = intent_data.get("summary", "调整行程")
            edit_cmd = {
                "type": "modify",
                "msg": summary,
                # 尝试从参数中提取更新值，用于覆盖原有行程参数
                "updates": {
                    "destination": params.get("destination"),
                    "days": params.get("days"),
                    "budget": params.get("budget"),
                    "preference": params.get("preference")
                }
            }

        # 重新生成行程
        # 优先使用 updates 中的新参数，否则回退到 current_trip，最后回退到默认值
        updates = edit_cmd.get("updates", {}) if edit_cmd else {}
        
        user_input = {
            "destination": updates.get("destination") or current_trip.get("destination", "成都"),
            "days": updates.get("days") or current_trip.get("days", 3),
            "budget": updates.get("budget") or current_trip.get("budget", 5000),
            "preference": updates.get("preference") or current_trip.get("preference", ["美食", "历史"]),
            "guide_links": []
        }

        context_texts = [msg["content"] for msg in context[-3:]] if context else []

        modified_trip = self.generate_trip(user_input, context_texts, edit_cmd)

        print(f"处理行程修改完成，新的行程是: {modified_trip}")

        if modified_trip:
            if edit_cmd and "type" in edit_cmd:
                 action_desc = {
                    "add": f"已成功在第{edit_cmd.get('day')}天添加{edit_cmd.get('attraction')}",
                    "delete": f"已成功从第{edit_cmd.get('day')}天删除{edit_cmd.get('attraction')}",
                    "reorder": "已重新优化行程顺序",
                    "modify": edit_cmd.get("msg", "已根据您的要求调整行程")
                }.get(edit_cmd["type"], "已调整行程")
            else:
                 action_desc = "已调整行程"

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
