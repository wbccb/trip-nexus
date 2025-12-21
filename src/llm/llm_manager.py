from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
import torch  # 仍然保留，以防其他部分使用，但在 __init__ 中不再强制需要

# 导入 Ollama 替代 HuggingFacePipeline
from langchain_ollama import OllamaLLM  # 从独立包导入
# from langchain_ollama import OllamaLLM
import json
import re

# ----------------------------------------------------
# ⚠️ 注意：以下导入已不再需要，因为我们使用 Ollama API
# from langchain_community.llms import HuggingFacePipeline
# from transformers import (
#     AutoTokenizer,
#     AutoModelForCausalLM,
#     pipeline,
#     BitsAndBytesConfig # 已经不需要
# )
# ----------------------------------------------------


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
    def __init__(self, model_name: str = "deepseek-r1:7b", ollama_base_url: str = "http://localhost:11434"):

        print(f"✅ 初始化 Ollama 模型: {model_name}...")

        # 1. 实例化 Ollama LLM
        # LangChain 的 Ollama 接口会通过 HTTP API 与 Ollama 服务器通信
        self.llm = OllamaLLM(
            base_url=ollama_base_url,
            model=model_name,
            # 设置一些生成参数，与你原代码中的 pipeline 保持一致
            temperature=0.7,
            num_ctx=4096,  # 默认上下文大小，可以根据需要调整
        )

        # 2. 初始化解析器
        self.parser = JsonOutputParser(pydantic_object=TripPlan)

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

        # 优化提示词模板，更适合指令遵循型模型
        template = """
        你是专业旅游规划师，严格遵循以下所有要求生成行程，并仅返回JSON格式数据。

        【生成要求】
        1. 仅输出JSON对象，不包含任何解释性文字或Markdown格式（如```json```）。
        2. JSON字段必须与指定的格式完全一致。
        3. 行程约束：
           - 目的地：{destination}
           - 总天数：{days}天
           - 预算：{budget}元/人（请合理分配交通、餐饮开支）
           - 偏好：{preference}
           - 额外要求：{edit_note}
        4. 必须参考攻略信息（请优先采纳）：
        {context}

        【细节规范】
        - 每天行程安排在8:00-18:00，时间必须连续且无冲突。
        - 地址必须精确到街道和门牌号（如"成都市青羊区青华路9号"）。
        - 交通方式具体（如"地铁2号线人民公园站B口出"）。

        【输出格式】
        {format_instructions}
        """
        prompt = PromptTemplate(
            template=template.strip(),
            input_variables=["destination", "days", "budget", "preference", "context", "edit_note"],
            partial_variables={"format_instructions": self.parser.get_format_instructions()}
        )
        return prompt.format(
            destination=user_input["destination"],
            days=user_input["days"],
            budget=user_input["budget"],
            preference=", ".join(user_input["preference"]),
            context="\n".join(context) if context else "无参考攻略",
            edit_note=edit_note
        )

    def extract_json_from_string(self, text):
        print(f"从大模型拿到的文本: {text}")

        """尝试从文本中提取完整的JSON对象，处理Markdown块"""
        text = text.strip()

        # 1. 查找并清理 Markdown JSON 块
        match_md = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if match_md:
            return match_md.group(1).strip()

        # 2. 如果没有 Markdown 块，尝试返回整个文本
        return text

    def generate_trip(self, user_input: Dict[str, Any], context: List[str],
                      edit_cmd: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """生成行程：通过可视化界面操作输入文字进行行程的生成"""
        prompt = self.build_prompt(user_input, context, edit_cmd)

        # 进行两次尝试
        for attempt in range(2):
            try:
                response = self.llm.invoke(prompt)

                # 使用改进的 JSON 提取函数
                clean_response = self.extract_json_from_string(response)

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
        analysis_response = self.llm.invoke(analysis_prompt)

        print(f"change_trip解析意图的prompt为：{analysis_prompt}")

        # 2. 从响应中提取用户意图和参数
        intent_data = self._parse_intent(analysis_response)

        print(f"change_trip从响应中提取用户意图和参数：{intent_data}")

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

        只返回JSON格式，不要包含其他文本。
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
                        safe_params[key] = None
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
                intent_data["needs_more_info"] = len(missing_key_params) > 1


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
            "needs_more_info": True
        }

    def _handle_trip_generation(self, intent_data: Dict[str, Any], context: List[Dict[str, str]]) -> Dict[str, Any]:
        """处理新行程生成请求"""
        params = intent_data.get("parameters", {})
        needs_more_info = intent_data.get("needs_more_info", True)

        # 1. 首先检查是否需要更多信息
        if needs_more_info:
            missing_info = []

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
            preference = preference_raw
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

        # 重新生成行程
        user_input = {
            "destination": current_trip.get("destination", "成都"),
            "days": current_trip.get("days", 3),
            "budget": current_trip.get("budget", 5000),
            "preference": current_trip.get("preference", ["美食", "历史"]),
            "guide_links": []
        }

        context_texts = [msg["content"] for msg in context[-3:]] if context else []

        modified_trip = self.generate_trip(user_input, context_texts, edit_cmd)

        if modified_trip:
            action_desc = {
                "add": f"已成功在第{edit_cmd['day']}天添加{edit_cmd['attraction']}",
                "delete": f"已成功从第{edit_cmd['day']}天删除{edit_cmd['attraction']}",
                "reorder": "已重新优化行程顺序"
            }.get(edit_cmd["type"], "已调整行程")

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
