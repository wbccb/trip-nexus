import { API_BASE, apiPost } from "./httpClient.js";
export {
  getSessionHistory,
  getSessionTrip,
  listSessions,
  deleteSession,
  sendChatMessage,
  startSession,
} from "./sessionApi.js";
// 行程相关接口导出
export {
  generateTrip,
  renderTripGeojson,
  renderTripMap,
  replanTripDay,
  streamTripGeneration,
  updateTripData,
} from "./tripApi.js";
export {
  createKnowledgeBase,
  deleteKnowledgeBase,
  generateKnowledgeAnswer,
  listKnowledgeBases,
  searchKnowledge,
  uploadKnowledgeDocument,
} from "./knowledgeApi.js";
export { API_BASE, apiPost } from "./httpClient.js";

export async function runAgent(payload) {
  return apiPost("/api/agent/run", payload);
}

export function buildAgentStreamUrl(threadId, lastSequence) {
  const sequenceValue = Number.isFinite(lastSequence) ? lastSequence : 0;
  return `${API_BASE}/api/agent/stream?thread_id=${encodeURIComponent(threadId)}&last_sequence=${sequenceValue}`;
}
