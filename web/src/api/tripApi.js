// 行程相关接口封装
import { apiPost } from "./httpClient.js"

// 生成行程
export async function generateTrip(payload) {
  return apiPost("/api/trip/generate", payload)
}
