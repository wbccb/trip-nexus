// 知识库检索相关接口封装
import { apiPost } from "./httpClient.js";
import { API_BASE } from "./httpClient.js";

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

export async function ingestKnowledgeUrl(knowledgeBaseId, payload) {
  return apiPost(
    `/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/ingest/url`,
    payload,
  );
}

export async function listKnowledgeSources(knowledgeBaseId) {
  const response = await fetch(
    `${API_BASE}/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/sources`,
  );
  if (!response.ok) {
    throw new Error(
      `GET /api/knowledge/bases/{id}/sources failed with status ${response.status}`,
    );
  }
  return response.json();
}

export async function getKnowledgeDebugSnapshot() {
  const response = await fetch(`${API_BASE}/api/knowledge/debug/snapshot`);
  if (!response.ok) {
    throw new Error(
      `GET /api/knowledge/debug/snapshot failed with status ${response.status}`,
    );
  }
  return response.json();
}

export async function deleteKnowledgeSource(knowledgeBaseId, sourceId) {
  const response = await fetch(
    `${API_BASE}/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/sources/${encodeURIComponent(sourceId)}`,
    {
      method: "DELETE",
    },
  );
  if (!response.ok) {
    throw new Error(
      `DELETE /api/knowledge/bases/{id}/sources/{source_id} failed with status ${response.status}`,
    );
  }
  return response.json();
}

export async function updateKnowledgeSource(knowledgeBaseId, sourceId, payload) {
  const response = await fetch(
    `${API_BASE}/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/sources/${encodeURIComponent(sourceId)}`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload || {}),
    },
  );
  if (!response.ok) {
    throw new Error(
      `PATCH /api/knowledge/bases/{id}/sources/{source_id} failed with status ${response.status}`,
    );
  }
  return response.json();
}
