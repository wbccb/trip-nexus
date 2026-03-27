import { apiGet, apiPost, apiPut } from "./httpClient.js";

export async function registerUser(payload) {
  // 注册成功后后端会直接返回 token + profile，
  // 前端因此可以无缝做“注册即登录”。
  return apiPost("/api/auth/register", payload || {});
}

export async function loginUser(payload) {
  // 登录接口和注册接口保持相同响应结构，方便 useAuth 统一收口。
  return apiPost("/api/auth/login", payload || {});
}

export async function refreshAuthToken() {
  return apiPost("/api/auth/refresh", {});
}

export async function getUserProfile() {
  // 启动恢复登录态、Header 展示当前用户、Admin 权限判断都会复用这条接口。
  return apiGet("/api/user/profile");
}

export async function updateUserProfile(payload) {
  return apiPut("/api/user/profile", payload || {});
}

export async function updateUserPassword(payload) {
  return apiPut("/api/user/password", payload || {});
}
