import { useCallback, useMemo, useState } from "react";
import { message } from "antd";
import {
  replanTripDay,
  streamTripGeneration,
  updateTripData,
} from "../api/index.js";
import {
  DEFAULT_DEVICE_ID,
  DEFAULT_USER_ID,
  SESSION_STORAGE_KEY,
} from "../constants/appConfig.js";
import { normalizeTripDays } from "../utils/tripUtils.js";

export function useTrip({
  activeSessionId,
  knowledgeGenerateQuery,
  selectedKnowledgeBaseId,
  refreshSessions,
  setActiveSessionId,
}) {
  const [tripResult, setTripResult] = useState(null);
  const [loadingTrip, setLoadingTrip] = useState(false);
  const tripDays = useMemo(() => normalizeTripDays(tripResult), [tripResult]);
  const updateTripResult = useCallback((data) => {
    setTripResult(data || null);
  }, []);

  const persistTripResult = useCallback(
    async (nextTrip, sessionOverride) => {
      if (!nextTrip) {
        return;
      }
      const payload = {
        user_id: DEFAULT_USER_ID,
        device_id: DEFAULT_DEVICE_ID,
        session_id: sessionOverride || activeSessionId,
        trip_data: nextTrip,
      };
      const data = await updateTripData(payload);
      if (data?.session_id && data.session_id !== activeSessionId) {
        setActiveSessionId(data.session_id);
        localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);
      }
    },
    [activeSessionId, setActiveSessionId],
  );

  const handleReplanDay = useCallback(
    async (day) => {
      if (!day) {
        return null;
      }
      const payload = {
        user_id: DEFAULT_USER_ID,
        device_id: DEFAULT_DEVICE_ID,
        session_id: activeSessionId,
        day,
      };
      const data = await replanTripDay(payload);
      if (data?.session_id && data.session_id !== activeSessionId) {
        setActiveSessionId(data.session_id);
        localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);
      }
      if (data?.trip_data) {
        setTripResult(data.trip_data);
      }
      return data?.trip_data || null;
    },
    [activeSessionId, setActiveSessionId],
  );

  // 提交行程表单（支持流式）
  const handleTripSubmit = useCallback(
    async (values, streamOptions = {}) => {
      try {
        // 标记加载状态
        setLoadingTrip(true);
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
          knowledge_query: knowledgeGenerateQuery || "",
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
        await streamTripGeneration(payload, (event) => {
          const eventType = event?.event || "";
          const deltaText = event?.content_delta || "";
          const step = event?.step || "";
          if (eventType === "start" && onStreamStart) {
            onStreamStart(event);
          }
          if (eventType === "delta") {
            accumulatedText += deltaText;
            if (onStreamDelta) {
              onStreamDelta(accumulatedText, event);
            }
          }
          if (eventType === "end" && step === "finalize") {
            streamSessionId = event?.session_id || null;
            const eventPayload = event?.payload || {};
            streamTripData = eventPayload?.trip_data || null;
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
      knowledgeGenerateQuery,
      refreshSessions,
      selectedKnowledgeBaseId,
      setActiveSessionId,
    ],
  );

  return {
    handleTripSubmit,
    loadingTrip,
    tripDays,
    tripResult,
    persistTripResult,
    handleReplanDay,
    updateTripResult,
  };
}
