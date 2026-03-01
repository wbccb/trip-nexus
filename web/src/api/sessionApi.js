// 会话相关接口封装
import { apiGet, apiPost } from "./httpClient.js";

// 获取会话列表
export async function listSessions(userId) {
  const safeUserId = encodeURIComponent(userId || "");
  return apiGet(`/api/sessions/list?user_id=${safeUserId}`);
}

// 创建会话
export async function startSession(userId, deviceId) {
  return apiPost("/api/sessions/start", {
    user_id: userId,
    device_id: deviceId,
  });
}

export async function getSessionHistory(sessionId) {
  const safeSessionId = encodeURIComponent(sessionId || "");
  return apiGet(`/api/sessions/history?session_id=${safeSessionId}`);
}
