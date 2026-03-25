import { useCallback, useMemo, useState } from "react";
import { message } from "antd";
import {
  replanFlowDay,
  streamMainFlow,
  updateFlowTripData,
} from "../api/index.js";
import {
  DEFAULT_DEVICE_ID,
  DEFAULT_USER_ID,
  SESSION_STORAGE_KEY,
} from "../constants/appConfig.js";
import { normalizeTripDays } from "../utils/tripUtils.js";

function createEmptyConflictReport() {
  return {
    has_conflicts: false,
    conflicts: [],
    alternatives: [],
  };
}

function normalizeFlowConstraints(values) {
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
  const [loadingTrip, setLoadingTrip] = useState(false);
  const tripDays = useMemo(() => normalizeTripDays(tripResult), [tripResult]);
  const updateTripResult = useCallback((data) => {
    setTripResult(data || null);
    setConflictReport(data?.conflict_report || createEmptyConflictReport());
    setSelectedAlternative(null);
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
    async (nextTrip, sessionOverride) => {
      if (!nextTrip) {
        return;
      }
      const payload = {
        user_id: DEFAULT_USER_ID,
        device_id: DEFAULT_DEVICE_ID,
        session_id: sessionOverride || activeSessionId,
        trip_data: nextTrip,
        constraints: resolveTripConstraints(nextTrip),
      };
      const data = await updateFlowTripData(payload);
      if (data?.session_id && data.session_id !== activeSessionId) {
        setActiveSessionId(data.session_id);
        localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);
      }
    },
    [activeSessionId, setActiveSessionId],
  );

  // 单日重排：触发主流程的指定天重规划能力
  const handleFlowReplanDay = useCallback(
    async (day) => {
      if (!day) {
        return null;
      }
      const payload = {
        user_id: DEFAULT_USER_ID,
        device_id: DEFAULT_DEVICE_ID,
        session_id: activeSessionId,
        day,
        constraints: resolveTripConstraints(tripResult),
      };
      const data = await replanFlowDay(payload);
      if (data?.session_id && data.session_id !== activeSessionId) {
        setActiveSessionId(data.session_id);
        localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);
      }
      if (data?.trip_data) {
        setTripResult(data.trip_data);
        setConflictReport(data.trip_data?.conflict_report || createEmptyConflictReport());
        setSelectedAlternative(null);
      }
      return data?.trip_data || null;
    },
    [activeSessionId, setActiveSessionId, tripResult],
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
          // 用户 ID
          user_id: DEFAULT_USER_ID,
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
          // 上下文文本
          context_texts: [],
          // 知识库 ID
          knowledge_base_id: selectedKnowledgeBaseId || null,
          // 知识库检索查询
          knowledge_query: knowledgeGenerateQuery || buildDefaultKnowledgeQuery(values),
          knowledge_scope: knowledgeScope || "private_plus_public",
          ...normalizeFlowConstraints(values),
        };
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
        // 暂存会话 ID
        let streamSessionId = null;
        // 暂存行程结果
        let streamTripData = null;
        await streamMainFlow(payload, (event) => {
          const eventType = event?.event || "";
          const deltaText = event?.content_delta || "";
          const step = event?.step || "";
          if (eventType === "start" && onStreamStart) {
            onStreamStart(event);
          }
          if (eventType === "delta") {
            if (step === "generate") {
              accumulatedText += deltaText;
            }
            if (step === "warning") {
              const warningReport = event?.payload?.conflict_report || createEmptyConflictReport();
              setConflictReport(warningReport);
              setSelectedAlternative(null);
            }
            if (onStreamDelta) {
              onStreamDelta(accumulatedText, event);
            }
          }
          if (eventType === "end" && step === "finalize") {
            streamSessionId = event?.session_id || null;
            const eventPayload = event?.payload || {};
            streamTripData = eventPayload?.trip_data || null;
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
        // 若拿到行程数据则更新
        if (streamTripData) {
          // 更新行程结果
          setTripResult(streamTripData);
        }
        // 刷新会话列表
        if (refreshSessions) {
          // 等待刷新完成
          await refreshSessions();
        }
      } catch (error) {
        // 提示错误信息
        message.error(`行程生成失败：${error.message}`);
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
    selectedAlternative,
    setConflictReport,
    setSelectedAlternative,
    loadingTrip,
    tripDays,
    tripResult,
    persistFlowTripResult,
    handleFlowReplanDay,
    updateTripResult,
  };
}
