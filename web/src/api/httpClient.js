// 统一封装前端 API 请求方法

// 获取 API 基础地址，默认指向本地后端
const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

// 封装 GET 请求
export async function apiGet(path) {
  // 拼接完整 URL
  const url = `${API_BASE}${path}`;
  // 发起请求
  const response = await fetch(url, {
    method: "GET",
  });
  // 若请求失败，抛出错误
  if (!response.ok) {
    throw new Error(`GET ${path} failed with status ${response.status}`);
  }
  // 返回 JSON 结果
  return response.json();
}

// 封装 POST 请求
export async function apiPost(path, body) {
  // 拼接完整 URL
  const url = `${API_BASE}${path}`;
  // 发起请求
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body || {}),
  });
  // 若请求失败，抛出错误
  if (!response.ok) {
    throw new Error(`POST ${path} failed with status ${response.status}`);
  }
  // 返回 JSON 结果
  return response.json();
}

export async function apiDelete(path) {
  const url = `${API_BASE}${path}`;
  const response = await fetch(url, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`DELETE ${path} failed with status ${response.status}`);
  }
  return response.json();
}
