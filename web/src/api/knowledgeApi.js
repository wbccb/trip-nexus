// 知识库检索相关接口封装
import { apiPost } from "./httpClient.js"

// 检索知识库
export async function searchKnowledge(query, generateAnswer) {
  return apiPost("/api/knowledge/search", {
    query,
    generate_answer: Boolean(generateAnswer),
  })
}

export async function generateKnowledgeAnswer(query, evidence) {
  return apiPost("/api/knowledge/answer_from_evidence", {
    query,
    evidence,
  })
}
