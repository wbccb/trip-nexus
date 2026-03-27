from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field

class FlowMetricItem(BaseModel):
    message_id: str = Field(..., description="流式消息ID")
    session_id: str = Field(..., description="会话ID")
    user_id: str = Field(..., description="用户ID")
    device_id: str = Field(..., description="设备ID")
    mode: str = Field(..., description="执行模式")
    intent: str = Field(..., description="识别意图")
    status: str = Field(..., description="执行状态")
    latency_ms: int = Field(0, description="端到端耗时毫秒")
    tool_count: int = Field(0, description="工具调用次数")
    rag_hit: bool = Field(False, description="是否命中知识增强")
    agent_escalated: bool = Field(False, description="是否升级Agent")
    context_count: int = Field(0, description="上下文条目数")
    context_chars: int = Field(0, description="上下文字符数")
    context_budget: Dict[str, Any] = Field(default_factory=dict, description="上下文预算配置")
    escalation_reasons: List[str] = Field(default_factory=list, description="升级原因")
    error: Optional[str] = Field(None, description="失败错误信息")
    created_at: str = Field(..., description="记录时间")


class FlowMetricsListResponse(BaseModel):
    total: int = Field(0, description="满足条件的记录总数")
    items: List[FlowMetricItem] = Field(default_factory=list, description="指标明细")


class FlowMetricsSummaryResponse(BaseModel):
    total: int = Field(0, description="满足条件的样本总数")
    success_count: int = Field(0, description="成功样本数")
    failed_count: int = Field(0, description="失败样本数")
    avg_latency_ms: float = Field(0.0, description="平均耗时毫秒")
    p50_latency_ms: float = Field(0.0, description="P50 耗时毫秒")
    p90_latency_ms: float = Field(0.0, description="P90 耗时毫秒")
    agent_escalated_rate: float = Field(0.0, description="Agent 升级比例")
    rag_hit_rate: float = Field(0.0, description="RAG 命中比例")
    avg_tool_count: float = Field(0.0, description="平均工具调用次数")


class FlowControlRequest(BaseModel):
    """主流程控制请求体，支持暂停、恢复、重试。"""
    message_id: str = Field(..., description="目标流式消息ID")
    action: str = Field(..., description="控制动作：pause/resume/retry")


class FlowControlResponse(BaseModel):
    """主流程控制响应体，反馈控制动作是否生效。"""
    message_id: str = Field(..., description="目标流式消息ID")
    action: str = Field(..., description="控制动作")
    accepted: bool = Field(..., description="是否受理成功")
    status: str = Field(..., description="当前流状态")
    next_message_id: Optional[str] = Field(None, description="重试后新消息ID")
    detail: str = Field("", description="控制结果说明")


class FlowStatusResponse(BaseModel):
    """主流程状态响应体，展示运行态与可恢复信息。"""
    message_id: str = Field(..., description="流式消息ID")
    session_id: str = Field(..., description="会话ID")
    running: bool = Field(False, description="是否运行中")
    done: bool = Field(False, description="是否已结束")
    paused: bool = Field(False, description="是否暂停中")
    status: str = Field("running", description="状态：running/paused/done/failed")
    retry_count: int = Field(0, description="已执行重试次数")
    has_error: bool = Field(False, description="是否存在错误")
    last_error: Optional[str] = Field(None, description="最后一次错误信息")
    latest_sequence: int = Field(0, description="当前最新事件序号")
    event_count: int = Field(0, description="事件数量")
    created_at: float = Field(0.0, description="创建时间戳")
    updated_at: float = Field(0.0, description="更新时间戳")


class ReleaseChecklistItem(BaseModel):
    """发布检查项结果。"""
    key: str = Field(..., description="检查项唯一键")
    title: str = Field(..., description="检查项名称")
    required: bool = Field(True, description="是否发布门槛必选项")
    status: str = Field(..., description="检查状态：passed/failed/partial/unknown")
    detail: str = Field("", description="检查结论说明")


class ReleaseGateResponse(BaseModel):
    """最终验收清单与发布门槛响应体。"""
    generated_at: str = Field(..., description="生成时间")
    overall_status: str = Field(..., description="总体状态：passed/blocked")
    checklist: List[ReleaseChecklistItem] = Field(default_factory=list, description="逐项检查结果")
    metrics_snapshot: Dict[str, Any] = Field(default_factory=dict, description="指标快照")
    replay_snapshot: Dict[str, Any] = Field(default_factory=dict, description="回放报告快照")
