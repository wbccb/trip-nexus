from langchain.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field
from langchain_community.llms import HuggingFacePipeline
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    pipeline,
    BitsAndBytesConfig
)
from typing import Dict, List, Optional, Any


# 行程数据模型（Pydantic 2.x兼容）
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
    def __init__(self, model_path: str = "meta-llama/Llama-3-8B-Instruct"):
        # 4位量化配置
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=float
        )

        # 加载模型（适配torch 2.4.1）
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
            low_cpu_mem_usage=True
        )

        # 文本生成管道
        self.pipeline = pipeline(
            "text-generation",
            model=self.model,
            tokenizer=self.tokenizer,
            max_new_tokens=1500,
            temperature=0.7,
            top_p=0.95,
            do_sample=True,
            pad_token_id=self.tokenizer.eos_token_id
        )

        self.llm = HuggingFacePipeline(pipeline=self.pipeline)
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

        template = """
        你是专业旅游规划师，严格按以下要求生成行程：

        1. 仅返回JSON，字段与指定格式完全一致，无额外文字。
        2. 行程约束：
           - 目的地：{destination}
           - 天数：{days}天
           - 预算：{budget}元/人（交通、餐饮合理分配）
           - 偏好：{preference}
           - 额外要求：{edit_note}
        3. 必须参考攻略信息（优先采纳）：{context}
        4. 细节要求：
           - 每天行程8:00-18:00，无时间冲突
           - 地址精确到街道（如"成都市青羊区青华路9号"）
           - 交通方式具体（如"地铁2号线人民公园站B口出"）

        输出格式示例：
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
        for attempt in range(2):
            try:
                response = self.llm.invoke(prompt)
                clean_response = response.split("```json")[1].split("```")[0] if "```json" in response else response
                trip_data = self.parser.parse(clean_response)
                return trip_data.model_dump()
            except Exception as e:
                print(f"第{attempt + 1}次生成失败：{str(e)}")
                if attempt == 1:
                    return None