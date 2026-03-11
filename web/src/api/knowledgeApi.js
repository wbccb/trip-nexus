// 知识库检索相关接口封装
import { apiPost } from "./httpClient.js";
import { API_BASE } from "./httpClient.js";

// 检索知识库
export async function searchKnowledge(query, generateAnswer) {
  return apiPost("/api/knowledge/search", {
    query,
    generate_answer: Boolean(generateAnswer),
  });
}

export async function generateKnowledgeAnswer(query, evidence) {
  return apiPost("/api/knowledge/answer_from_evidence", {
    query,
    evidence,
  });
}

export async function listKnowledgeBases() {
  const response = await fetch(`${API_BASE}/api/knowledge/bases`);
  if (!response.ok) {
    throw new Error(
      `GET /api/knowledge/bases failed with status ${response.status}`,
    );
  }
  return response.json();
}

export async function createKnowledgeBase(name) {
  return apiPost("/api/knowledge/bases", { name });
}

export async function deleteKnowledgeBase(knowledgeBaseId) {
  const response = await fetch(
    `${API_BASE}/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}`,
    {
      method: "DELETE",
    },
  );
  if (!response.ok) {
    throw new Error(
      `DELETE /api/knowledge/bases failed with status ${response.status}`,
    );
  }
  return response.json();
}

export async function uploadKnowledgeDocument(knowledgeBaseId, file) {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(
    `${API_BASE}/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/upload`,
    {
      method: "POST",
      body: formData,
    },
  );
  if (!response.ok) {
    throw new Error(
      `POST /api/knowledge/bases/{id}/upload failed with status ${response.status}`,
    );
  }
  return response.json();
}
