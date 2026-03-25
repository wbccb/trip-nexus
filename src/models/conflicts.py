from enum import Enum
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


class ConflictType(str, Enum):
    DISTANCE_TIME = "distance_time"
    PACE_DENSITY = "pace_density"
    REST_WINDOW = "rest_window"


class ConflictItem(BaseModel):
    type: ConflictType = Field(..., description="冲突类别")
    day: int = Field(..., description="冲突所在天，1-indexed")
    description: str = Field(..., description="面向用户的冲突说明")
    severity: Literal["warning", "error"] = Field(..., description="warning=可接受风险, error=强烈不推荐")


class AlternativePlan(BaseModel):
    label: str = Field(..., description="方案标签，如 Plan A / Plan B")
    strategy: str = Field(..., description="方案策略摘要")
    trip_data: Dict[str, Any] = Field(default_factory=dict, description="替代行程的完整结构")


class ConflictReport(BaseModel):
    has_conflicts: bool = Field(False, description="是否存在冲突")
    conflicts: List[ConflictItem] = Field(default_factory=list, description="冲突列表")
    alternatives: List[AlternativePlan] = Field(default_factory=list, description="替代方案列表")
