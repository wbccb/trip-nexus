from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
import torch  # 仍然保留，以防其他部分使用，但在 __init__ 中不再强制需要

# 导入 Ollama 替代 HuggingFacePipeline
from langchain_community.llms import Ollama


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


class TripGenerator:
    # 默认使用本地安装的 Ollama deepseek-r1:7b
    def __init__(self, model_name: str = "deepseek-r1:7b", ollama_base_url: str = "http://localhost:11434"):

        print(f"✅ 初始化 Ollama 模型: {model_name}...")

        # 1. 实例化 Ollama LLM
        # LangChain 的 Ollama 接口会通过 HTTP API 与 Ollama 服务器通信
        self.llm = Ollama(
            base_url=ollama_base_url,
            model=model_name,
            # 设置一些生成参数，与你原代码中的 pipeline 保持一致
            temperature=0.7,
            num_ctx=4096,  # 默认上下文大小，可以根据需要调整
        )

        # 2. 初始化解析器
        self.parser = JsonOutputParser(pydantic_object=TripPlan)

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

    def generate_trip(self, user_input: Dict[str, Any], context: List[str],
                      edit_cmd: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """生成行程（支持修改指令）"""
        prompt = self.build_prompt(user_input, context, edit_cmd)

        # 由于 Ollama 模型的响应速度通常较快，我们进行两次尝试以确保 JSON 格式正确。
        for attempt in range(2):
            try:
                # 使用 self.llm.invoke() 方法直接调用 Ollama API
                response = self.llm.invoke(prompt)

                # 尝试清洗响应，移除可能的 Markdown 格式
                # deepseek-r1 模型可能仍然会返回 Markdown 块
                if "```json" in response:
                    # 尝试精确提取 JSON 块
                    start = response.find("```json") + 7
                    end = response.find("```", start)
                    clean_response = response[start:end].strip()
                else:
                    clean_response = response.strip()

                trip_data = self.parser.parse(clean_response)
                print(f"✅ 第{attempt + 1}次生成成功。")
                return trip_data.model_dump()
            except Exception as e:
                print(f"❌ 第{attempt + 1}次生成失败，尝试重新生成。错误：{str(e)}")
                if attempt == 1:
                    return None