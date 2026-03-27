// 统一封装前端 API 请求方法
import { AUTH_TOKEN_KEY } from "../constants/appConfig.js";

// 获取 API 基础地址，默认指向本地后端
export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

export function getAuthToken() {
  // 所有页面都通过这一个入口读取 token，避免不同模块各自拼 localStorage key。
  return localStorage.getItem(AUTH_TOKEN_KEY) || "";
}

export function setAuthToken(token) {
  // 登录成功后统一在这里持久化 token，
  // 后续普通请求和 SSE 都复用同一份登录态。
  if (!token) {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    return;
  }
  localStorage.setItem(AUTH_TOKEN_KEY, token);
}

export function clearAuthToken() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

function buildHeaders(extraHeaders = {}) {
  // 请求头统一在这里自动注入 Authorization，
  // 这样业务 API 不需要每个文件手动拼 Bearer Token。
  const token = getAuthToken();
  const headers = {
    ...extraHeaders,
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

function emitUnauthorized() {
  // httpClient 不直接操控 React 状态，而是发一个全局事件。
  // useAuth 监听这个事件后再做清状态/跳登录，这样层次更清晰。
  window.dispatchEvent(new CustomEvent("tripnexus:unauthorized"));
}

function buildFriendlyErrorMessage(status, detail, method, path) {
  const detailText = String(detail || "").trim();
  if (status === 429) {
    if (detailText.includes("Token 额度已用完")) {
      return "当前账号的 AI 调用额度已用完，请联系管理员调整额度。";
    }
    if (detailText.includes("请求过于频繁")) {
      return "你的操作有点频繁了，请稍等片刻再试。";
    }
  }
  if (status === 403 && detailText.includes("账号已被禁用")) {
    return "当前账号已被禁用，请联系管理员。";
  }
  if (detailText) {
    return detailText;
  }
  return `${method} ${path} failed with status ${status}`;
}

async function parseJsonResponse(response, method, path) {
  // 401 在这里统一收口，避免每个 API 调用点都自己判断一次未登录。
  if (response.status === 401) {
    emitUnauthorized();
  }
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      detail = payload?.detail || "";
    } catch (error) {
      detail = "";
    }
    throw new Error(buildFriendlyErrorMessage(response.status, detail, method, path));
  }
  return response.json();
}

// 封装 GET 请求
export async function apiGet(path) {
  // 拼接完整 URL
  const url = `${API_BASE}${path}`;
  // 发起请求
  const response = await fetch(url, {
    method: "GET",
    headers: buildHeaders(),
  });
  return parseJsonResponse(response, "GET", path);
}

// 封装 POST 请求
export async function apiPost(path, body) {
  // 拼接完整 URL
  const url = `${API_BASE}${path}`;
  // 发起请求
  const response = await fetch(url, {
    method: "POST",
    headers: buildHeaders({
      "Content-Type": "application/json",
    }),
    body: JSON.stringify(body || {}),
  });
  return parseJsonResponse(response, "POST", path);
}

export async function apiDelete(path) {
  const url = `${API_BASE}${path}`;
  const response = await fetch(url, {
    method: "DELETE",
    headers: buildHeaders(),
  });
  return parseJsonResponse(response, "DELETE", path);
}

export async function apiPut(path, body) {
  // Admin / profile 这类写操作统一走 apiPut，和 apiPost 共享同一套鉴权与错误处理语义。
  const url = `${API_BASE}${path}`;
  const response = await fetch(url, {
    method: "PUT",
    headers: buildHeaders({
      "Content-Type": "application/json",
    }),
    body: JSON.stringify(body || {}),
  });
  return parseJsonResponse(response, "PUT", path);
}
