import { useCallback, useMemo, useState } from "react";
import { message } from "antd";
import {
  previewConflictAlternative,
  replanFlowDay,
  streamMainFlow,
  updateFlowTripData,
} from "../api/index.js";
import {
  DEFAULT_DEVICE_ID,
  SESSION_STORAGE_KEY,
} from "../constants/appConfig.js";
import { normalizeTripDays } from "../utils/tripUtils.js";
import { logDebug } from "../utils/debugLogger.js";

function createEmptyConflictReport() {
  return {
    has_conflicts: false,
    conflicts: [],
    alternatives: [],
  };
}

function normalizeFlowConstraints(values) {
  // 前端表单层允许空值和局部字段更新，这里统一补默认值并转成后端约定的 constraints 结构，
  // 避免 flow/stream、trip/update、trip/replan_day 三条链路各自拼装一套。
  const budgetLevel = ["economy", "balanced", "comfortable"].includes(values?.budget_level)
    ? values.budget_level
    : "balanced";
  const intensity = ["leisure", "standard", "extreme"].includes(values?.intensity)
    ? values.intensity
    : "standard";
  const pace = ["cultural", "efficient", "family_friendly"].includes(values?.pace)
    ? values.pace
    : "cultural";
  const walkingLimitRaw = values?.walking_limit_km;
  const walkingLimit = Number.isFinite(Number(walkingLimitRaw))
    ? Number(walkingLimitRaw)
    : null;
  return {
    budget_level: budgetLevel,
    intensity,
    pace,
    special_constraints: {
      walking_limit_km: walkingLimit && walkingLimit > 0 ? walkingLimit : null,
      need_nap: Boolean(values?.need_nap),
      accessibility: Boolean(values?.accessibility),
    },
  };
}

function resolveTripConstraints(source) {
  // 修改行程、拖拽保存、局部重排时优先复用当前 tripResult 里的 constraints_used，
  // 保证后续编辑不会把首次生成时的结构化约束丢掉。
  const constraints = source?.constraints_used || source?.constraints || source || {};
  return normalizeFlowConstraints({
    budget_level: constraints?.budget_level,
    intensity: constraints?.intensity,
    pace: constraints?.pace,
    walking_limit_km: constraints?.special_constraints?.walking_limit_km,
    need_nap: constraints?.special_constraints?.need_nap,
    accessibility: constraints?.special_constraints?.accessibility,
  });
}

function extractTripDataFromText(responseText) {
  const rawText = String(responseText || "").trim();
  if (!rawText) {
    return null;
  }
  const fencedMatch = rawText.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  const jsonText = fencedMatch?.[1] ? fencedMatch[1].trim() : rawText;
  try {
    const parsed = JSON.parse(jsonText);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

export function useTrip({
  activeSessionId,
  knowledgeGenerateQuery,
  knowledgeScope,
  selectedKnowledgeBaseId,
  refreshSessions,
  setActiveSessionId,
}) {
  const [tripResult, setTripResult] = useState(null);
  const [conflictReport, setConflictReport] = useState(createEmptyConflictReport());
  const [selectedAlternative, setSelectedAlternative] = useState(null);
  const [lockedDays, setLockedDays] = useState(new Set());
  const [loadingTrip, setLoadingTrip] = useState(false);
  const tripDays = useMemo(() => normalizeTripDays(tripResult), [tripResult]);
  const updateTripResult = useCallback((data) => {
    setTripResult(data || null);
    // 约束满足状态和冲突报告都挂在 trip_data 上，
    // 因此切换正式行程结果时要同步重置冲突 UI 与替代方案选择状态。
    setConflictReport(data?.conflict_report || createEmptyConflictReport());
    setSelectedAlternative(null);
    setLockedDays(new Set());
  }, []);
  const buildDefaultKnowledgeQuery = useCallback((values) => {
    const destination = String(values?.destination || "").trim();
    const preference = String(values?.preference || "").trim();
    if (!destination && !preference) {
      return "";
    }
    if (!preference) {
      return `目的地:${destination} 行程建议`;
    }
    return `目的地:${destination} 偏好:${preference} 行程建议`;
  }, []);

  // 持久化主流程产物：将当前行程结果保存到后端会话
  const persistFlowTripResult = useCallback(
    async (nextTrip, options = {}) => {
      if (!nextTrip) {
        return;
      }
      const sessionOverride =
        options && typeof options === "object" ? options.sessionIdOverride : options;
      const payload = {
        device_id: DEFAULT_DEVICE_ID,
        session_id: sessionOverride || activeSessionId,
        trip_data: nextTrip,
        constraints: resolveTripConstraints(nextTrip),
        update_source: typeof options === "object" ? options.updateSource || null : null,
        selected_alternative_label:
          typeof options === "object" ? options.selectedAlternativeLabel || null : null,
        selected_alternative_index:
          typeof options === "object" && Number.isInteger(options.selectedAlternativeIndex)
            ? options.selectedAlternativeIndex
            : null,
      };
      // 即使只是本地编辑后的持久化，也要显式带上 constraints，
      // 这样后端才能重算 constraints_satisfied 与 conflict_report。
      const data = await updateFlowTripData(payload);
      if (data?.session_id && data.session_id !== activeSessionId) {
        setActiveSessionId(data.session_id);
        localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);
      }
      setLockedDays(new Set());
    },
    [activeSessionId, setActiveSessionId],
  );

  // 记录用户当前预览的是哪个替代方案，便于后端终端日志还原“查看方案 -> 采用方案”的完整链路。
  const reportAlternativePreview = useCallback(
    async ({ alternativeLabel, alternativeIndex }) => {
      if (!alternativeLabel || !Number.isInteger(alternativeIndex)) {
        return null;
      }
      logDebug("行程", "用户当前预览冲突替代方案", {
        alternativeLabel,
        alternativeIndex,
        sessionId: activeSessionId,
      });
      return previewConflictAlternative({
        device_id: DEFAULT_DEVICE_ID,
        session_id: activeSessionId,
        alternative_label: alternativeLabel,
        alternative_index: alternativeIndex,
      });
    },
    [activeSessionId],
  );

  // 单日重排：触发主流程的指定天重规划能力
  const handleFlowReplanDay = useCallback(
    async (replanInput) => {
      const normalizedInput =
        typeof replanInput === "number"
          ? { day: replanInput }
          : replanInput && typeof replanInput === "object"
            ? replanInput
            : null;
      const day = Number(normalizedInput?.scope?.day || normalizedInput?.day || 0);
      if (!day) {
        return null;
      }
      const scope =
        normalizedInput?.scope && typeof normalizedInput.scope === "object"
          ? {
              day,
              time_range: normalizedInput.scope.time_range || null,
            }
          : null;
      // 锁定天由页面状态与本次操作显式传参共同决定，避免局部重排时丢锁定上下文。
      const mergedLockedDays = Array.from(
        new Set([
          ...Array.from(lockedDays),
          ...(Array.isArray(normalizedInput?.lockedDays) ? normalizedInput.lockedDays : []),
        ]),
      )
        .map((item) => Number(item))
        .filter((item) => Number.isInteger(item) && item > 0 && item !== day)
        .sort((a, b) => a - b);
      const payload = {
        device_id: DEFAULT_DEVICE_ID,
        session_id: activeSessionId,
        day,
        scope,
        locked_days: mergedLockedDays,
        replan_instruction: String(normalizedInput?.replanInstruction || "").trim() || null,
        constraints: resolveTripConstraints(tripResult),
      };
      // replan_day 不再只依赖后端从 current_trip 隐式继承约束，
      // 当前前端会把已生效的 constraints_used 显式带回去，保证局部重排继续受约束控制。
      const data = await replanFlowDay(payload);
      if (data?.session_id && data.session_id !== activeSessionId) {
        setActiveSessionId(data.session_id);
        localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);
      }
      if (data?.trip_data) {
        setTripResult(data.trip_data);
        setConflictReport(data?.conflict_report || data.trip_data?.conflict_report || createEmptyConflictReport());
        setSelectedAlternative(null);
      }
      return data || null;
    },
    [activeSessionId, lockedDays, setActiveSessionId, tripResult],
  );

  // 主流程提交：发起流式规划并消费统一 SSE 事件
  const handleFlowSubmit = useCallback(
    async (values, streamOptions = {}) => {
      try {
        // 标记加载状态
        setLoadingTrip(true);
        setConflictReport(createEmptyConflictReport());
        setSelectedAlternative(null);
        // 组装请求参数
        const payload = {
          // 设备 ID
          device_id: DEFAULT_DEVICE_ID,
          // 会话 ID
          session_id: activeSessionId,
          // 目的地
          destination: values.destination,
          // 天数
          days: values.days,
          mode: values.mode || "fast",
          // 预算
          budget: values.budget || "",
          // 偏好
          preference: values.preference || "",
          // 核心步骤：透传表单或者对话的原始 message 文本到后端进行持久化，避免被识别的摘要取代
          message: values.message || null,
          // 上下文文本
          context_texts: [],
          // 知识库 ID
          knowledge_base_id: selectedKnowledgeBaseId || null,
          // 知识库检索查询
          knowledge_query: knowledgeGenerateQuery || buildDefaultKnowledgeQuery(values),
          knowledge_scope: knowledgeScope || "private_plus_public",
          ...normalizeFlowConstraints(values),
        };
        logDebug("行程", "开始提交主流程", {
          destination: payload.destination,
          days: payload.days,
          knowledgeBaseId: payload.knowledge_base_id,
          knowledgeScope: payload.knowledge_scope,
          mode: payload.mode,
        });
        // 解构流式回调
        const {
          // 流开始回调
          onStreamStart,
          // 流增量回调
          onStreamDelta,
          // 流结束回调
          onStreamEnd,
          // 行程数据回调
          onTripData,
        } = streamOptions || {};
        // 初始化累计文本
        let accumulatedText = "";
        let accumulatedReasoningText = "";
        // 暂存会话 ID
        let streamSessionId = null;
        // 暂存行程结果
        let streamTripData = null;
        await streamMainFlow(payload, (event) => {
          const eventType = event?.event || "";
          const deltaText = event?.content_delta || "";
          const reasoningDelta = event?.reasoning_delta || "";
          const step = event?.step || "";
          
          if (eventType === "start" && onStreamStart) {
            onStreamStart(event);
          }
          
          // 对于所有有效的 event 都要触发 onStreamDelta 从而更新 UI
          // Include eventType === "start" to catch the first "intent" step
          if (eventType === "start" || eventType === "delta" || step) {
            if (step === "generate" && eventType === "delta") {
              if (deltaText) {
                accumulatedText += deltaText;
              }
              if (reasoningDelta) {
                accumulatedReasoningText += reasoningDelta;
              }
            }
            if (step === "warning") {
              const warningReport = event?.payload?.conflict_report || createEmptyConflictReport();
              setConflictReport(warningReport);
              setSelectedAlternative(null);
            }
            
            console.log("[useTrip] Passing event to onStreamDelta:", { eventType, step, currentAccumulatedText: accumulatedText, payload: event?.payload });
            if (onStreamDelta) {
              // We need to wait for a microtask so state updates don't overlap improperly
              setTimeout(() => onStreamDelta(accumulatedText, event, accumulatedReasoningText), 0);
            }
          }
          
          if (eventType === "end" && step === "finalize") {
            streamSessionId = event?.session_id || null;
            const eventPayload = event?.payload || {};
            streamTripData = eventPayload?.trip_data || null;
            // finalize 中的 trip_data/conflict_report 才是最终持久化版本，
            // 这里要覆盖 warning 阶段的临时状态，避免前后不一致。
            setConflictReport(eventPayload?.conflict_report || streamTripData?.conflict_report || createEmptyConflictReport());
            setSelectedAlternative(null);
            if (onTripData) {
              onTripData(streamTripData, event);
            }
            if (onStreamEnd) {
              onStreamEnd(accumulatedText, event);
            }
          }
        });
        // 若拿到会话 ID 则更新
        if (streamSessionId) {
          // 更新会话 ID
          setActiveSessionId(streamSessionId);
          // 持久化会话 ID
          localStorage.setItem(SESSION_STORAGE_KEY, streamSessionId);
        }
        if (!streamTripData && accumulatedText) {
          streamTripData = extractTripDataFromText(accumulatedText);
          if (streamTripData) {
            logDebug("行程", "主流程终态缺少 trip_data，已从流式文本回退解析", {
              destination: streamTripData?.destination,
              days: streamTripData?.days,
            });
          }
        }
        // 若拿到行程数据则更新
        if (streamTripData) {
          // 更新行程结果
          setTripResult(streamTripData);
          logDebug("行程", "主流程完成并产出行程", {
            destination: streamTripData?.destination,
            days: streamTripData?.days,
          });
        }
        // 刷新会话列表
        if (refreshSessions) {
          // 等待刷新完成
          await refreshSessions();
        }
      } catch (error) {
        // 提示错误信息
        message.error(`行程生成失败：${error.message}`);
        logDebug("行程", "主流程提交失败", {
          __level: "error",
          error: String(error?.message || error),
        });
      } finally {
        // 重置加载状态
        setLoadingTrip(false);
      }
    },
    [
      activeSessionId,
      buildDefaultKnowledgeQuery,
      knowledgeGenerateQuery,
      knowledgeScope,
      refreshSessions,
      selectedKnowledgeBaseId,
      setActiveSessionId,
    ],
  );

  return {
    conflictReport,
    handleFlowSubmit,
    lockedDays,
    selectedAlternative,
    setConflictReport,
    setLockedDays,
    setSelectedAlternative,
    loadingTrip,
    tripDays,
    tripResult,
    persistFlowTripResult,
    reportAlternativePreview,
    handleFlowReplanDay,
    updateTripResult,
  };
}
