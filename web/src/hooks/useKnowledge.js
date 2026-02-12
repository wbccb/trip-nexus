import { useCallback, useState } from "react"
import { message } from "antd"
import { searchKnowledge } from "../api/index.js"

export function useKnowledge() {
  const [knowledgeQuery, setKnowledgeQuery] = useState("")
  const [knowledgeResult, setKnowledgeResult] = useState(null)
  const [loadingKnowledge, setLoadingKnowledge] = useState(false)

  const handleKnowledgeSearch = useCallback(async () => {
    try {
      if (!knowledgeQuery.trim()) {
        message.warning("请输入检索问题")
        return
      }
      setLoadingKnowledge(true)
      const data = await searchKnowledge(knowledgeQuery.trim(), false)
      setKnowledgeResult(data || null)
    } catch (error) {
      message.error(`知识库检索失败：${error.message}`)
    } finally {
      setLoadingKnowledge(false)
    }
  }, [knowledgeQuery])

  return {
    handleKnowledgeSearch,
    knowledgeQuery,
    knowledgeResult,
    loadingKnowledge,
    setKnowledgeQuery,
  }
}
