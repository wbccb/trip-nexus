import {
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  apiPostForm,
} from "./httpClient.js";

// 检索知识库
export async function searchKnowledge(
  query,
  generateAnswer,
  knowledgeBaseId,
  knowledgeScope,
) {
  return apiPost("/api/knowledge/search", {
    query,
    generate_answer: Boolean(generateAnswer),
    knowledge_base_id: knowledgeBaseId || null,
    knowledge_scope: knowledgeScope || "private_plus_public",
  });
}

export async function generateKnowledgeAnswer(query, evidence) {
  return apiPost("/api/knowledge/answer_from_evidence", {
    query,
    evidence,
  });
}

export async function listKnowledgeBases() {
  return apiGet("/api/knowledge/bases");
}

export async function createKnowledgeBase(name) {
  return apiPost("/api/knowledge/bases", { name });
}

export async function deleteKnowledgeBase(knowledgeBaseId) {
  return apiDelete(
    `/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}`,
  );
}

export async function uploadKnowledgeDocument(knowledgeBaseId, file) {
  const formData = new FormData();
  formData.append("file", file);
  return apiPostForm(
    `/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/upload`,
    formData,
  );
}

export async function ingestKnowledgeUrl(knowledgeBaseId, payload) {
  // 导入接口会根据 payload.mode 返回 parsed / fallback / failed 三种状态，
  // 前端据此决定展示成功提示、失败引导或“补全文本重试”入口。
  return apiPost(
    `/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/ingest/url`,
    payload,
  );
}

export async function preprocessKnowledgeUrl(payload) {
  // 预处理接口不入库，只返回“这条链接值不值得走自动解析”的前置信号，
  // 供前端在用户点击导入前就展示平台、风险、质量分与失败原因。
  return apiPost("/api/knowledge/preprocess/url", payload || {});
}

export async function listKnowledgeSources(knowledgeBaseId) {
  // sources 接口返回的是“来源聚合视图”，不是底层分块列表；
  // 其中既包含已入库来源，也可能包含等待用户补救的 failed 来源。
  return apiGet(
    `/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/sources`,
  );
}

export async function getKnowledgeDebugSnapshot() {
  return apiGet("/api/knowledge/debug/snapshot");
}

export async function deleteKnowledgeSource(knowledgeBaseId, sourceId) {
  return apiDelete(
    `/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/sources/${encodeURIComponent(sourceId)}`,
  );
}

export async function updateKnowledgeSource(
  knowledgeBaseId,
  sourceId,
  payload,
) {
  return apiPatch(
    `/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/sources/${encodeURIComponent(sourceId)}`,
    payload || {},
  );
}
