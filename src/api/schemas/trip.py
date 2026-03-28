from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field, field_validator

class FlowRequestBase(BaseModel):
    """主流程请求基础模型，描述一次规划任务的核心输入。"""
    user_id: Optional[str] = Field(None, description="用户唯一ID，鉴权模式下会自动从 JWT 提取")
    device_id: str = Field(..., description="设备唯一ID")
    session_id: Optional[str] = Field(None, description="会话ID，可为空以便新建")
    destination: str = Field(..., description="目的地城市")
    days: int = Field(..., description="行程天数")
    budget: Optional[str] = Field(None, description="预算（可选）")
    preference: Optional[str] = Field(None, description="偏好（可选）")
    message: Optional[str] = Field(None, description="用户自然语言任务描述（可选）")
    context_texts: List[str] = Field(default_factory=list, description="上下文文本列表")
    knowledge_base_id: Optional[str] = Field(None, description="知识库ID（可选）")
    knowledge_query: Optional[str] = Field(None, description="知识库检索查询（可选）")
    knowledge_scope: Optional[str] = Field("private_plus_public", description="知识范围 private_only/private_plus_public")
    budget_level: Optional[str] = Field("balanced", description="预算档位 economy/balanced/comfortable")
    intensity: Optional[str] = Field("standard", description="体能强度 leisure/standard/extreme")
    pace: Optional[str] = Field("cultural", description="节奏偏好 cultural/efficient/family_friendly")
    special_constraints: Optional[Dict[str, Any]] = Field(None, description="特殊约束配置")

    @field_validator("budget", mode="before")
    @classmethod
    def normalize_budget(cls, value: Any) -> Optional[str]:
        """兼容前端传入数字预算，并统一归一化为字符串。"""
        if value in [None, ""]:
            return None
        return str(value)


class FlowStreamRequest(FlowRequestBase):
    """主流程流式请求体，扩展执行模式字段。"""
    mode: Optional[str] = Field("fast", description="执行模式：fast/deep")


class SpecialConstraints(BaseModel):
    walking_limit_km: Optional[float] = Field(None, description="单日步行上限（公里）")
    need_nap: bool = Field(False, description="是否需要午休")
    accessibility: bool = Field(False, description="是否需要无障碍")


class TripConstraints(BaseModel):
    budget_level: str = Field("balanced", description="economy / balanced / comfortable")
    intensity: str = Field("standard", description="leisure / standard / extreme")
    pace: str = Field("cultural", description="cultural / efficient / family_friendly")
    special_constraints: SpecialConstraints = Field(default_factory=SpecialConstraints, description="特殊约束")


class ConstraintStatus(BaseModel):
    label: str = Field(..., description="约束描述")
    status: str = Field(..., description="met / partially_met / violated")
    detail: Optional[str] = Field(None, description="补充说明")


class TripDataResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    trip_data: Optional[Dict[str, Any]] = Field(None, description="结构化行程数据")


class TripUpdateRequest(BaseModel):
    user_id: Optional[str] = Field(None, description="用户唯一ID，鉴权模式下会自动从 JWT 提取")
    device_id: str = Field(..., description="设备唯一ID")
    session_id: Optional[str] = Field(None, description="会话ID，可为空以便新建")
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")
    constraints: Optional[Dict[str, Any]] = Field(None, description="可选约束参数，前端修改行程时显式透传")


class TripUpdateResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")


class ReplanScope(BaseModel):
    # v0.0.6 的局部重排支持“按天 + 按半天范围”两级粒度，
    # 前端传 morning/afternoon/evening 后，后端只替换对应时间段的行程项。
    day: int = Field(..., description="目标重排天（1-indexed）")
    time_range: Optional[str] = Field(None, description="重排范围：morning/afternoon/evening，None=整天")


class AgentEscalationInfo(BaseModel):
    # 当前的 escalation 更接近“相邻天联动说明 + 最小联动修补结果”，
    # 不是完整的 LangGraph 子图执行结果。
    escalated: bool = Field(False, description="是否触发相邻天联动重排")
    reasons: List[str] = Field(default_factory=list, description="触发原因列表")
    message: str = Field("", description="面向用户的说明文本")


class TripReplanDayRequest(BaseModel):
    # day 保留旧版整天重排兼容；scope 存在时优先使用 scope.day/time_range。
    # locked_days / replan_instruction / constraints 则共同约束“允许怎么改、哪些地方绝对不能动”。
    user_id: Optional[str] = Field(None, description="用户唯一ID，鉴权模式下会自动从 JWT 提取")
    device_id: str = Field(..., description="设备唯一ID")
    session_id: Optional[str] = Field(None, description="会话ID，可为空以便新建")
    day: int = Field(..., description="需要重新规划的天数")
    scope: Optional[ReplanScope] = Field(None, description="精细重排范围，存在时优先于 day")
    locked_days: List[int] = Field(default_factory=list, description="锁定不参与重排的天数")
    replan_instruction: Optional[str] = Field(None, description="用户补充重排指令")
    constraints: Optional[Dict[str, Any]] = Field(None, description="可选约束参数，重排单日时显式透传")


class TripReplanDayResponse(BaseModel):
    # 返回的不只是重排后的 trip_data，还会显式说明：
    # 这次到底改了哪一段、是否触发了相邻天联动、重排后冲突有没有变化。
    session_id: str = Field(..., description="会话ID")
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")
    replanned_scope: Dict[str, Any] = Field(default_factory=dict, description="实际重排范围")
    agent_escalation: Optional[AgentEscalationInfo] = Field(None, description="相邻天联动重排信息")
    conflict_report: Optional[Dict[str, Any]] = Field(None, description="重排后的冲突报告")
