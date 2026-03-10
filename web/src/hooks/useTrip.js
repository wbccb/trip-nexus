import { useCallback, useMemo, useState } from "react";
import { message } from "antd";
// 引入行程生成接口与流式接口
import {
  generateTrip,
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
          // 预算
          budget: values.budget || "",
          // 偏好
          preference: values.preference || "",
          // 上下文文本
          context_texts: [],
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
        // 记录是否触发流事件
        let hasStreamEvent = false;
        // 暂存会话 ID
        let streamSessionId = null;
        // 暂存行程结果
        let streamTripData = null;
        // 记录流式错误
        let streamError = null;
        try {
          // 调用流式接口
          await streamTripGeneration(payload, (event) => {
            // 标记已收到流事件
            hasStreamEvent = true;
            // 读取事件类型
            const eventType = event?.event || "";
            // 读取增量文本
            const deltaText = event?.content_delta || "";
            // 流开始事件
            if (eventType === "start") {
              // 触发开始回调
              if (onStreamStart) {
                // 传递事件
                onStreamStart(event);
              }
            }
            // 流增量事件
            if (eventType === "delta") {
              // 追加增量文本
              accumulatedText += deltaText;
              // 触发增量回调
              if (onStreamDelta) {
                // 传递最新文本
                onStreamDelta(accumulatedText, event);
              }
            }
            // 流结束事件
            if (eventType === "end") {
              // 触发结束回调
              if (onStreamEnd) {
                // 传递最终文本
                onStreamEnd(accumulatedText, event);
              }
            }
            // 行程数据事件
            if (eventType === "trip_data") {
              // 暂存会话 ID
              streamSessionId = event?.session_id || null;
              // 暂存行程数据
              streamTripData = event?.trip_data || null;
              // 触发行程回调
              if (onTripData) {
                // 传递行程数据
                onTripData(streamTripData, event);
              }
            }
          });
        } catch (error) {
          // 记录流式错误
          streamError = error;
        }
        // 若流式失败则回退同步接口
        if (streamError || !hasStreamEvent) {
          // 调用同步接口
          const data = await generateTrip(payload);
          // 更新会话 ID
          if (data?.session_id) {
            // 同步会话 ID
            setActiveSessionId(data.session_id);
            // 持久化会话 ID
            localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);
          }
          // 更新行程结果
          setTripResult(data?.trip_data || null);
          // 刷新会话列表
          if (refreshSessions) {
            // 等待刷新完成
            await refreshSessions();
          }
          // 提前返回
          return;
        }
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
    [activeSessionId, refreshSessions, setActiveSessionId],
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
