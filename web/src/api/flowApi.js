// 主流程相关接口封装
// 引入基础地址与通用请求方法
import { API_BASE, apiPost, getAuthToken } from "./httpClient.js";
import { logDebug } from "../utils/debugLogger.js";

// 主流程流式接口：负责发起 /api/flow/stream 并消费 SSE 事件
export async function streamMainFlow(payload, onEvent, options = {}) {
  // 记录当前消息 ID，便于断线重连时续传
  let currentMessageId = options?.messageId || "";
  // 记录当前序列号，便于从指定 sequence 继续拉流
  let currentSequence = Number.isFinite(options?.lastSequence)
    ? options.lastSequence
    : 0;
  // 最大重试次数（网络抖动容错）
  const maxRetries = Number.isFinite(options?.maxRetries)
    ? options.maxRetries
    : 2;
  // 重试间隔毫秒
  const retryDelayMs = Number.isFinite(options?.retryDelayMs)
    ? options.retryDelayMs
    : 800;
  // 当前重试计数
  let attempt = 0;
  while (attempt <= maxRetries) {
    // 构建主流程流式 URL
    const url = new URL(`${API_BASE}/api/flow/stream`);
    // 透传 message_id，确保同一任务流上下文一致
    if (currentMessageId) {
      url.searchParams.set("message_id", currentMessageId);
    }
    // 透传 last_sequence，支持断线续传
    if (Number.isFinite(currentSequence)) {
      url.searchParams.set("last_sequence", currentSequence);
    }
    try {
      logDebug("主流程", "开始请求流式规划", {
        messageId: currentMessageId,
        lastSequence: currentSequence,
        attempt: attempt + 1,
      });
      // 发起流式请求
      const response = await fetch(url.toString(), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(getAuthToken() ? { Authorization: `Bearer ${getAuthToken()}` } : {}),
        },
        body: JSON.stringify(payload || {}),
      });
      // HTTP 非 2xx 直接抛错
      if (!response.ok) {
        throw new Error(
          `POST /api/flow/stream failed with status ${response.status}`,
        );
      }
      logDebug("主流程", "流式连接建立成功", {
        status: response.status,
        messageId: currentMessageId,
      });
      // 无 body 视为异常
      if (!response.body) {
        throw new Error("stream body is not available");
      }
      // 获取可读流 reader
      const reader = response.body.getReader();
      // 使用 UTF-8 解码器处理 chunk
      const decoder = new TextDecoder("utf-8");
      // SSE 分帧缓冲区
      let buffer = "";
      while (true) {
        // 持续读取网络流
        const { value, done } = await reader.read();
        // 读取完成则退出循环
        if (done) {
          break;
        }
        // 追加当前 chunk 到缓冲区
        buffer += decoder.decode(value, { stream: true });
        // SSE 事件按空行分隔
        const chunks = buffer.split("\n\n");
        // 最后一个分片可能不完整，保留到下轮拼接
        buffer = chunks.pop() || "";
        // 处理完整分片
        chunks.forEach((chunk) => {
          // 解析分片为 JSON 事件
          const payloadEvent = parseSseEvent(chunk);
          if (payloadEvent) {
            // 首次收到 message_id 时写入本地状态
            if (payloadEvent.message_id && !currentMessageId) {
              currentMessageId = payloadEvent.message_id;
            }
            // 跟进最新序列号，确保续传准确
            if (Number.isFinite(payloadEvent.sequence)) {
              currentSequence = payloadEvent.sequence;
            }
            // 向上抛出事件给业务层
            if (onEvent) {
              onEvent(payloadEvent);
            }
          }
        });
      }
      // 处理最后一段缓冲区内容
      if (buffer.trim()) {
        // 解析尾部分片
        const payloadEvent = parseSseEvent(buffer);
        if (payloadEvent) {
          // 同步 message_id
          if (payloadEvent.message_id && !currentMessageId) {
            currentMessageId = payloadEvent.message_id;
          }
          // 同步 sequence
          if (Number.isFinite(payloadEvent.sequence)) {
            currentSequence = payloadEvent.sequence;
          }
          // 回调尾部事件
          if (onEvent) {
            onEvent(payloadEvent);
          }
        }
      }
      // 一次请求成功完成后结束函数
      logDebug("主流程", "流式规划结束", {
        messageId: currentMessageId,
        lastSequence: currentSequence,
      });
      return;
    } catch (error) {
      // 累加重试次数
      attempt += 1;
      // 超过最大重试次数则上抛错误
      if (attempt > maxRetries) {
        logDebug("主流程", "流式规划失败且达到最大重试次数", {
          __level: "error",
          attempt,
          error: String(error?.message || error),
        });
        throw error;
      }
      logDebug("主流程", "流式规划失败，准备重试", {
        __level: "warn",
        attempt,
        error: String(error?.message || error),
      });
      // 等待后重试
      await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
    }
  }
}

// 解析 SSE 数据块：提取 data 行并转为 JSON
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
    logDebug("主流程", "SSE 事件解析失败", {
      __level: "warn",
      rawChunk: rawChunk,
      error: String(error?.message || error),
    });
    return null;
  }
}

// 主流程地图渲染接口：根据行程数据返回 HTML 地图片段
export async function renderFlowMap(payload) {
  return apiPost("/api/map/render", payload);
}

// 主流程地图 GeoJSON 接口：根据行程数据返回点线要素
export async function renderFlowGeojson(payload) {
  return apiPost("/api/map/geojson", payload);
}

// 主流程结果持久化接口：保存用户编辑后的行程数据
export async function updateFlowTripData(payload) {
  return apiPost("/api/trip/update", payload);
}

// 主流程单日重排接口：重新规划指定天的行程
export async function replanFlowDay(payload) {
  return apiPost("/api/trip/replan_day", payload);
}
