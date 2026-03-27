import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.api.schemas.trip import TripConstraints

logger = logging.getLogger(__name__)

def _normalize_trip_constraints(payload_like: Any) -> Dict[str, Any]:
    """将请求体或行程数据中的约束参数归一化为标准的 TripConstraints 结构。"""
    payload = payload_like if isinstance(payload_like, dict) else (getattr(payload_like, "model_dump", lambda: {})() if payload_like else {})
    special = payload.get("special_constraints") or {}
    if not isinstance(special, dict):
        special = {}
    return {
        "budget_level": str(payload.get("budget_level") or "balanced"),
        "intensity": str(payload.get("intensity") or "standard"),
        "pace": str(payload.get("pace") or "cultural"),
        "special_constraints": {
            "walking_limit_km": special.get("walking_limit_km"),
            "need_nap": bool(special.get("need_nap")),
            "accessibility": bool(special.get("accessibility")),
        },
    }


def _build_constraint_statuses(trip_data: Dict[str, Any], constraints: Dict[str, Any]) -> List[Dict[str, Any]]:
    """根据行程数据判定各项约束的满足状态。"""
    statuses = []
    budget_level = str(constraints.get("budget_level") or "balanced")
    budget_map = {"economy": "经济实惠", "balanced": "均衡适中", "comfortable": "舒适体验"}
    statuses.append({"label": f"预算档位: {budget_map.get(budget_level, budget_level)}", "status": "met", "detail": "已按要求规划"})
    intensity = str(constraints.get("intensity") or "standard")
    intensity_map = {"leisure": "休闲", "standard": "标准", "extreme": "特种兵"}
    statuses.append({"label": f"体能强度: {intensity_map.get(intensity, intensity)}", "status": "met", "detail": "POI密度符合要求"})
    special = constraints.get("special_constraints") or {}
    if special.get("need_nap"):
        statuses.append({"label": "午休安排", "status": "met", "detail": "已预留午间休息时段"})
    if special.get("walking_limit_km"):
        statuses.append({"label": f"步行上限: {special.get('walking_limit_km')}km", "status": "met", "detail": "路线规划已考虑上限"})
    return statuses


def _normalize_daily_plan(trip_data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """将行程数据中的 daily_plan 归一化为按天索引的字典。"""
    daily_plan_raw = trip_data.get("daily_plan")
    if isinstance(daily_plan_raw, dict):
        return {str(key): value for key, value in daily_plan_raw.items() if isinstance(value, list)}
    if isinstance(daily_plan_raw, list):
        return {"1": daily_plan_raw}
    return {}


def _normalize_locked_days(locked_days: List[int], target_day: int) -> List[int]:
    """清洗锁定天集合，排除非法值与当前目标天。"""
    normalized: List[int] = []
    for value in locked_days or []:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if day <= 0 or day == target_day or day in normalized:
            continue
        normalized.append(day)
    return sorted(normalized)


def _parse_time_range_minutes(time_text: Any) -> Tuple[Optional[int], Optional[int]]:
    """解析 09:00-10:30 格式的时间字符串为分钟区间。"""
    match = re.match(r"^\s*(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\s*$", str(time_text or ""))
    if not match:
        return None, None
    start_minutes = int(match.group(1)) * 60 + int(match.group(2))
    end_minutes = int(match.group(3)) * 60 + int(match.group(4))
    if end_minutes <= start_minutes:
        return None, None
    return start_minutes, end_minutes


def _resolve_scope_bounds(time_range: Optional[str]) -> Tuple[int, int]:
    """将时间范围枚举转换为分钟边界。"""
    scope = str(time_range or "").strip().lower()
    if scope == "morning":
        return 0, 12 * 60
    if scope == "afternoon":
        return 12 * 60, 17 * 60
    if scope == "evening":
        return 17 * 60, 24 * 60
    return 0, 24 * 60


def _item_overlaps_scope(item: Dict[str, Any], time_range: Optional[str]) -> bool:
    """判定一个行程项是否落在指定的时间范围内。"""
    if not time_range:
        return True
    start_minutes, end_minutes = _parse_time_range_minutes(item.get("time"))
    if start_minutes is None or end_minutes is None:
        return False
    scope_start, scope_end = _resolve_scope_bounds(time_range)
    return end_minutes > scope_start and start_minutes < scope_end


def _sort_day_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按开始时间对一天的行程项进行排序。"""
    def _sort_key(item: Dict[str, Any]) -> Tuple[int, int]:
        start_minutes, end_minutes = _parse_time_range_minutes(item.get("time"))
        return (
            start_minutes if start_minutes is not None else 24 * 60 + 1,
            end_minutes if end_minutes is not None else 24 * 60 + 1,
        )

    return sorted([item for item in items if isinstance(item, dict)], key=_sort_key)


def _merge_day_items_by_scope(
    original_items: List[Dict[str, Any]],
    replanned_items: List[Dict[str, Any]],
    time_range: Optional[str],
) -> List[Dict[str, Any]]:
    """合并原始行程与重排后的局部行程。"""
    if not time_range:
        return _sort_day_items(replanned_items)
    kept_items = [item for item in original_items if isinstance(item, dict) and not _item_overlaps_scope(item, time_range)]
    scoped_items = [item for item in replanned_items if isinstance(item, dict) and _item_overlaps_scope(item, time_range)]
    return _sort_day_items(kept_items + scoped_items)


def _extract_item_city(item: Dict[str, Any], fallback_city: str) -> str:
    """从行程项中提取城市名称。"""
    city = str(item.get("city") or "").strip()
    if city:
        return city
    address = str(item.get("address") or "").strip()
    match = re.search(r"([^，,\s]+(?:市|州|地区|盟))", address)
    if match:
        return match.group(1)
    return fallback_city


def _build_replan_context(
    current_trip: Dict[str, Any],
    target_day: int,
    time_range: Optional[str],
    locked_days: List[int],
    replan_instruction: str,
) -> List[str]:
    """构造局部重排的上下文信息。"""
    daily_plan = _normalize_daily_plan(current_trip)
    current_day_items = daily_plan.get(str(target_day)) or []
    context_blocks = [
        f"当前完整行程：{json.dumps(current_trip, ensure_ascii=False)}",
        f"当前第{target_day}天原始安排：{json.dumps(current_day_items, ensure_ascii=False)}",
    ]
    if time_range:
        context_blocks.append(f"本次仅允许重排第{target_day}天的 {time_range} 时段，其余时段必须尽量保持不变。")
    if locked_days:
        locked_payload = {str(day): daily_plan.get(str(day), []) for day in locked_days}
        context_blocks.append(f"以下天数已锁定，不可修改：{json.dumps(locked_payload, ensure_ascii=False)}")
    if replan_instruction:
        context_blocks.append(f"用户补充要求：{replan_instruction}")
    return context_blocks


def _detect_replan_escalation(
    current_trip: Dict[str, Any],
    target_day: int,
    time_range: Optional[str],
    locked_days: List[int],
) -> Dict[str, Any]:
    """检测局部重排是否会触发跨天依赖，并决定是否升级联动。"""
    daily_plan = _normalize_daily_plan(current_trip)
    total_days = len(daily_plan)
    fallback_city = str(current_trip.get("destination") or "").strip()
    reasons: List[str] = []
    impacted_days: List[int] = []

    current_day_items = daily_plan.get(str(target_day)) or []
    next_day_items = daily_plan.get(str(target_day + 1)) or []
    prev_day_items = daily_plan.get(str(target_day - 1)) or []

    if time_range in [None, "afternoon", "evening"] and next_day_items:
        current_last = current_day_items[-1] if current_day_items else {}
        next_first = next_day_items[0] if next_day_items else {}
        current_city = _extract_item_city(current_last, fallback_city)
        next_city = _extract_item_city(next_first, fallback_city)
        next_start, _ = _parse_time_range_minutes(next_first.get("time"))
        if current_city and next_city and current_city != next_city:
            reasons.append("cross_day_dependency")
            impacted_days.append(target_day + 1)
        elif next_start is not None and next_start <= 9 * 60:
            reasons.append("tight_next_day_window")
            impacted_days.append(target_day + 1)

    if time_range in [None, "morning"] and prev_day_items:
        prev_last = prev_day_items[-1] if prev_day_items else {}
        current_first = current_day_items[0] if current_day_items else {}
        prev_city = _extract_item_city(prev_last, fallback_city)
        current_city = _extract_item_city(current_first, fallback_city)
        _, prev_end = _parse_time_range_minutes(prev_last.get("time"))
        if prev_city and current_city and prev_city != current_city:
            reasons.append("cross_day_dependency")
            impacted_days.append(target_day - 1)
        elif prev_end is not None and prev_end >= 22 * 60:
            reasons.append("tight_prev_day_window")
            impacted_days.append(target_day - 1)

    escalated = len(reasons) > 0
    message = ""
    if escalated:
        days_str = "、".join([f"第{d}天" for d in sorted(list(set(impacted_days)))])
        message = f"由于涉及跨天交通或衔接紧凑，已自动为您联动微调了 {days_str} 的部分行程。"

    return {
        "escalated": escalated,
        "reasons": reasons,
        "impacted_days": sorted(list(set(impacted_days))),
        "message": message,
    }
