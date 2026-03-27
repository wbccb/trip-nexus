import { apiGet, apiPut } from "./httpClient.js";

export async function listAdminUsers(params = {}) {
  // Admin 列表查询把分页和关键词都放在 query string，
  // 这样后端可以直接按运营后台的标准查询模式扩展。
  const search = new URLSearchParams();
  if (params.page) {
    search.set("page", String(params.page));
  }
  if (params.pageSize) {
    search.set("page_size", String(params.pageSize));
  }
  if (params.keyword) {
    search.set("keyword", String(params.keyword));
  }
  const suffix = search.toString();
  return apiGet(`/api/admin/users${suffix ? `?${suffix}` : ""}`);
}

export async function updateAdminUserStatus(userId, status) {
  // 封禁/解封是幂等状态写入，不走 toggle 接口，后端状态更清晰。
  return apiPut(`/api/admin/users/${encodeURIComponent(userId)}/status`, { status });
}

export async function updateAdminUserQuota(userId, tokenQuota) {
  // 配额更新单独拆接口，便于后续接审计日志或额度变更记录。
  return apiPut(`/api/admin/users/${encodeURIComponent(userId)}/quota`, {
    token_quota: tokenQuota,
  });
}

export async function getAdminDashboard() {
  return apiGet("/api/admin/dashboard");
}

export async function getAdminUserTokenUsage(userId, params = {}) {
  const search = new URLSearchParams();
  if (params.limit) {
    search.set("limit", String(params.limit));
  }
  const suffix = search.toString();
  return apiGet(
    `/api/admin/users/${encodeURIComponent(userId)}/token-usage${suffix ? `?${suffix}` : ""}`,
  );
}

export async function getAdminAuditLogs(params = {}) {
  // 这里统一把“请求时间线”的筛选条件串到 query string，
  // 让 Admin 页既能看全局日志，也能快速缩到某个 user/session/message 的局部轨迹。
  const search = new URLSearchParams();
  if (params.limit) {
    search.set("limit", String(params.limit));
  }
  if (params.action) {
    search.set("action", String(params.action));
  }
  if (params.userId) {
    search.set("user_id", String(params.userId));
  }
  if (params.sessionId) {
    search.set("session_id", String(params.sessionId));
  }
  if (params.messageId) {
    search.set("message_id", String(params.messageId));
  }
  if (params.requestPath) {
    search.set("request_path", String(params.requestPath));
  }
  const suffix = search.toString();
  return apiGet(`/api/admin/audit-logs${suffix ? `?${suffix}` : ""}`);
}
