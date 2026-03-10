// 行程相关接口封装
// 引入基础地址与通用请求方法
import { API_BASE, apiPost } from "./httpClient.js";

// 生成行程
export async function generateTrip(payload) {
  return apiPost("/api/trip/generate", payload);
}

// 行程生成流式接口
export async function streamTripGeneration(payload, onEvent, options = {}) {
  let currentMessageId = options?.messageId || "";
  let currentSequence = Number.isFinite(options?.lastSequence)
    ? options.lastSequence
    : 0;
  const maxRetries = Number.isFinite(options?.maxRetries)
    ? options.maxRetries
    : 2;
  const retryDelayMs = Number.isFinite(options?.retryDelayMs)
    ? options.retryDelayMs
    : 800;
  let attempt = 0;
  while (attempt <= maxRetries) {
    const url = new URL(`${API_BASE}/api/trip/stream`);
    if (currentMessageId) {
      url.searchParams.set("message_id", currentMessageId);
    }
    if (Number.isFinite(currentSequence)) {
      url.searchParams.set("last_sequence", currentSequence);
    }
    try {
      const response = await fetch(url.toString(), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload || {}),
      });
      if (!response.ok) {
        throw new Error(
          `POST /api/trip/stream failed with status ${response.status}`,
        );
      }
      if (!response.body) {
        throw new Error("stream body is not available");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";
        chunks.forEach((chunk) => {
          const payloadEvent = parseSseEvent(chunk);
          if (payloadEvent) {
            if (payloadEvent.message_id && !currentMessageId) {
              currentMessageId = payloadEvent.message_id;
            }
            if (Number.isFinite(payloadEvent.sequence)) {
              currentSequence = payloadEvent.sequence;
            }
            if (onEvent) {
              onEvent(payloadEvent);
            }
          }
        });
      }
      if (buffer.trim()) {
        const payloadEvent = parseSseEvent(buffer);
        if (payloadEvent) {
          if (payloadEvent.message_id && !currentMessageId) {
            currentMessageId = payloadEvent.message_id;
          }
          if (Number.isFinite(payloadEvent.sequence)) {
            currentSequence = payloadEvent.sequence;
          }
          if (onEvent) {
            onEvent(payloadEvent);
          }
        }
      }
      return;
    } catch (error) {
      attempt += 1;
      if (attempt > maxRetries) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
    }
  }
}

// 解析 SSE 数据块
function parseSseEvent(rawChunk) {
  // 拆分每一行
  const lines = String(rawChunk || "").split("\n");
  // 收集 data 行
  const dataLines = [];
  // 遍历行内容
  lines.forEach((line) => {
    // 仅处理 data 前缀
    if (line.startsWith("data:")) {
      // 去掉前缀并去空格
      dataLines.push(line.replace(/^data:\s?/, ""));
    }
  });
  // 若无 data 行则返回空
  if (dataLines.length === 0) {
    // 返回空值
    return null;
  }
  // 拼接完整 JSON 字符串
  const dataText = dataLines.join("\n");
  // 解析 JSON 数据
  try {
    // 返回解析后的对象
    return JSON.parse(dataText);
  } catch (error) {
    // 解析失败时返回空
    return null;
  }
}

export async function renderTripMap(payload) {
  return apiPost("/api/map/render", payload);
}
