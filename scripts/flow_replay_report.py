"""主流程样本回放与验收报告脚本。"""
import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional


def _build_default_cases() -> List[Dict[str, Any]]:
    """构建默认回放样本，覆盖常规、深度、问答与修改场景。"""
    return [
        {
            "case_id": "fast-generate-basic",
            "description": "极速模式常规生成",
            "mode": "fast",
            "destination": "成都",
            "days": 3,
            "budget": "3000元",
            "preference": "美食、人文",
            "message": "请给我一个轻松的3天成都行程",
            "expect_non_agent": True,
        },
        {
            "case_id": "fast-generate-family",
            "description": "极速模式家庭游生成",
            "mode": "fast",
            "destination": "杭州",
            "days": 2,
            "budget": "2500元",
            "preference": "亲子、轻松",
            "message": "规划适合家庭亲子出行的杭州2日游",
            "expect_non_agent": True,
        },
        {
            "case_id": "deep-generate-complex",
            "description": "深度模式复杂规划",
            "mode": "deep",
            "destination": "新疆",
            "days": 6,
            "budget": "9000元",
            "preference": "摄影、自驾",
            "message": "给我一个多天跨区域的深度路线，尽量覆盖经典风光",
            "expect_non_agent": False,
        },
        {
            "case_id": "fast-general-conversation",
            "description": "极速模式问答型请求",
            "mode": "fast",
            "destination": "上海",
            "days": 2,
            "budget": "",
            "preference": "城市漫步",
            "message": "这次旅行如果下雨，室内行程有什么建议？",
            "expect_non_agent": True,
        },
        {
            "case_id": "fast-modify-trip",
            "description": "极速模式修改型请求",
            "mode": "fast",
            "destination": "成都",
            "days": 3,
            "budget": "3000元",
            "preference": "美食、人文",
            "message": "把第二天改成博物馆和慢节奏路线",
            "expect_non_agent": True,
        },
    ]


def _safe_int(value: Any, default: int = 0) -> int:
    """将值转换为整数，失败时返回默认值。"""
    try:
        return int(value)
    except Exception:
        return int(default)


def _percentile(values: List[int], ratio: float) -> float:
    """计算百分位值，输入为空时返回 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    index = int(round((len(ordered) - 1) * max(0.0, min(1.0, ratio))))
    index = max(0, min(len(ordered) - 1, index))
    return float(ordered[index])


def _build_stream_url(base_url: str, message_id: str) -> str:
    """构造主流程流式请求地址。"""
    normalized_base = base_url.rstrip("/")
    query = urllib.parse.urlencode({"message_id": message_id})
    return f"{normalized_base}/api/flow/stream?{query}"


def _stream_flow_events(base_url: str, message_id: str, payload: Dict[str, Any], timeout_seconds: int) -> List[Dict[str, Any]]:
    """调用主流程流式接口并解析 SSE 事件。"""
    url = _build_stream_url(base_url, message_id)
    request_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=request_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    events: List[Dict[str, Any]] = []
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line or not line.startswith("data:"):
                continue
            data_text = line[len("data:"):].strip()
            if not data_text:
                continue
            try:
                event = json.loads(data_text)
            except Exception:
                continue
            if isinstance(event, dict):
                events.append(event)
                if bool(event.get("is_final")):
                    break
    return events


def _extract_finalize_event(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """提取最终事件作为单次回放结果依据。"""
    for event in reversed(events):
        if bool(event.get("is_final")):
            return event
    return None


def _run_replay_case(
    case: Dict[str, Any],
    base_url: str,
    user_id: str,
    device_id: str,
    timeout_seconds: int,
    session_id: Optional[str],
) -> Dict[str, Any]:
    """执行单个样本回放并输出结构化结果。"""
    message_id = f"replay-{case['case_id']}-{int(time.time() * 1000)}"
    payload = {
        "user_id": user_id,
        "device_id": device_id,
        "session_id": session_id,
        "destination": case.get("destination", ""),
        "days": _safe_int(case.get("days"), 1),
        "budget": case.get("budget", ""),
        "preference": case.get("preference", ""),
        "message": case.get("message", ""),
        "context_texts": [],
        "knowledge_base_id": None,
        "knowledge_query": "",
        "mode": case.get("mode", "fast"),
    }
    started_at = time.perf_counter()
    error_text = ""
    events: List[Dict[str, Any]] = []
    try:
        events = _stream_flow_events(base_url, message_id, payload, timeout_seconds)
    except Exception as exc:
        error_text = str(exc)
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    finalize_event = _extract_finalize_event(events)
    finalize_payload = (finalize_event or {}).get("payload") if isinstance(finalize_event, dict) else {}
    if not isinstance(finalize_payload, dict):
        finalize_payload = {}
    metrics = finalize_payload.get("metrics") if isinstance(finalize_payload.get("metrics"), dict) else {}
    status = str((finalize_event or {}).get("status") or ("failed" if error_text else "unknown"))
    result_session_id = str((finalize_event or {}).get("session_id") or session_id or "")
    return {
        "case_id": case.get("case_id"),
        "description": case.get("description"),
        "mode": case.get("mode", "fast"),
        "status": status,
        "message_id": str((finalize_event or {}).get("message_id") or message_id),
        "session_id": result_session_id,
        "intent": str(metrics.get("intent") or ""),
        "latency_ms": _safe_int(metrics.get("latency_ms"), elapsed_ms),
        "tool_count": _safe_int(metrics.get("tool_count"), 0),
        "rag_hit": bool(metrics.get("rag_hit")),
        "agent_escalated": bool(metrics.get("agent_escalated")),
        "context_count": _safe_int(metrics.get("context_count"), 0),
        "context_chars": _safe_int(metrics.get("context_chars"), 0),
        "context_budget": metrics.get("context_budget") if isinstance(metrics.get("context_budget"), dict) else {},
        "escalation_reasons": finalize_payload.get("escalation_reasons") if isinstance(finalize_payload.get("escalation_reasons"), list) else [],
        "event_count": len(events),
        "error": error_text or finalize_payload.get("error") or "",
        "expect_non_agent": bool(case.get("expect_non_agent")),
    }


def _build_acceptance_report(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """根据回放结果构建验收统计报告。"""
    total = len(results)
    success_items = [item for item in results if item.get("status") == "done"]
    failed_items = [item for item in results if item.get("status") != "done"]
    latencies = [_safe_int(item.get("latency_ms"), 0) for item in success_items if _safe_int(item.get("latency_ms"), 0) > 0]
    fast_items = [item for item in success_items if str(item.get("mode")) == "fast"]
    fast_non_agent_items = [item for item in fast_items if not bool(item.get("agent_escalated"))]
    expect_non_agent_items = [item for item in results if bool(item.get("expect_non_agent"))]
    expect_non_agent_success = [item for item in expect_non_agent_items if item.get("status") == "done"]
    expect_non_agent_hit = [item for item in expect_non_agent_success if not bool(item.get("agent_escalated"))]
    intent_distribution: Dict[str, int] = {}
    for item in success_items:
        intent_name = str(item.get("intent") or "unknown")
        intent_distribution[intent_name] = intent_distribution.get(intent_name, 0) + 1
    p50_latency_ms = _percentile(latencies, 0.5) if latencies else 0.0
    p90_latency_ms = _percentile(latencies, 0.9) if latencies else 0.0
    avg_latency_ms = float(sum(latencies) / len(latencies)) if latencies else 0.0
    fast_non_agent_ratio = float(len(fast_non_agent_items) / len(fast_items)) if fast_items else 0.0
    expect_non_agent_ratio = float(len(expect_non_agent_hit) / len(expect_non_agent_success)) if expect_non_agent_success else 0.0
    agent_escalated_rate = float(len([item for item in success_items if bool(item.get("agent_escalated"))]) / len(success_items)) if success_items else 0.0
    rag_hit_rate = float(len([item for item in success_items if bool(item.get("rag_hit"))]) / len(success_items)) if success_items else 0.0
    return {
        "generated_at": datetime.now().isoformat(),
        "total_cases": total,
        "success_cases": len(success_items),
        "failed_cases": len(failed_items),
        "success_rate": float(len(success_items) / total) if total else 0.0,
        "avg_latency_ms": avg_latency_ms,
        "p50_latency_ms": p50_latency_ms,
        "p90_latency_ms": p90_latency_ms,
        "fast_non_agent_ratio": fast_non_agent_ratio,
        "expect_non_agent_ratio": expect_non_agent_ratio,
        "agent_escalated_rate": agent_escalated_rate,
        "rag_hit_rate": rag_hit_rate,
        "intent_distribution": intent_distribution,
        "checks": {
            "check_fast_non_agent_ratio_ge_0_9": fast_non_agent_ratio >= 0.9,
            "check_expect_non_agent_ratio_ge_0_9": expect_non_agent_ratio >= 0.9,
            "check_all_cases_success": len(failed_items) == 0,
        },
        "results": results,
    }


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="主流程样本回放与验收报告")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API 服务地址")
    parser.add_argument("--user-id", default="replay-user", help="回放用户ID")
    parser.add_argument("--device-id", default="replay-device", help="回放设备ID")
    parser.add_argument("--timeout-seconds", type=int, default=180, help="单样本请求超时时间")
    parser.add_argument("--sleep-seconds", type=float, default=0.2, help="样本间隔秒数")
    parser.add_argument(
        "--output",
        default="",
        help="输出 JSON 文件路径，默认写入 scripts/flow_replay_report_<timestamp>.json",
    )
    return parser.parse_args()


def main() -> None:
    """执行样本回放并输出验收报告。"""
    args = _parse_args()
    cases = _build_default_cases()
    current_session_id: Optional[str] = None
    case_results: List[Dict[str, Any]] = []
    for case in cases:
        result = _run_replay_case(
            case=case,
            base_url=args.base_url,
            user_id=args.user_id,
            device_id=args.device_id,
            timeout_seconds=max(30, int(args.timeout_seconds)),
            session_id=current_session_id,
        )
        case_results.append(result)
        if str(result.get("session_id") or "").strip():
            current_session_id = str(result.get("session_id"))
        time.sleep(max(0.0, float(args.sleep_seconds)))
    report = _build_acceptance_report(case_results)
    output_path = args.output.strip() if isinstance(args.output, str) else ""
    if not output_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"scripts/flow_replay_report_{ts}.json"
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"output": output_path, "summary": {k: v for k, v in report.items() if k != "results"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
