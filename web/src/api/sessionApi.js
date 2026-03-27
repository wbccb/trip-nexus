// 会话相关接口封装
import { apiDelete, apiGet, apiPost } from "./httpClient.js";

// 获取会话列表
export async function listSessions() {
  return apiGet("/api/sessions/list");
}

// 创建会话
export async function startSession(deviceId) {
  return apiPost("/api/sessions/start", {
    device_id: deviceId,
  });
}

export async function getSessionHistory(sessionId) {
  const safeSessionId = encodeURIComponent(sessionId || "");
  return apiGet(`/api/sessions/history?session_id=${safeSessionId}`);
}

export async function getSessionTrip(sessionId) {
  const safeSessionId = encodeURIComponent(sessionId || "");
  return apiGet(`/api/sessions/trip?session_id=${safeSessionId}`);
}

export async function sendChatMessage(payload) {
  return apiPost("/api/chat/send", payload || {});
}

export async function deleteSession(sessionId) {
  const safeSessionId = encodeURIComponent(sessionId || "");
  return apiDelete(`/api/sessions/delete?session_id=${safeSessionId}`);
}
