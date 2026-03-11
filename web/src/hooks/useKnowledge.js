import { useCallback, useEffect, useState } from "react"
import { message } from "antd"
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  listKnowledgeBases,
  searchKnowledge,
  uploadKnowledgeDocument,
} from "../api/index.js"

export function useKnowledge() {
  const [knowledgeQuery, setKnowledgeQuery] = useState("")
  const [knowledgeResult, setKnowledgeResult] = useState(null)
  const [loadingKnowledge, setLoadingKnowledge] = useState(false)
  const [knowledgeBases, setKnowledgeBases] = useState([])
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState("")
  const [knowledgeGenerateQuery, setKnowledgeGenerateQuery] = useState("")
  const [loadingKnowledgeBases, setLoadingKnowledgeBases] = useState(false)
  const [uploadingKnowledge, setUploadingKnowledge] = useState(false)

  const refreshKnowledgeBases = useCallback(async () => {
    try {
      setLoadingKnowledgeBases(true)
      const data = await listKnowledgeBases()
      const items = Array.isArray(data?.items) ? data.items : []
      setKnowledgeBases(items)
      if (items.length === 0) {
        setSelectedKnowledgeBaseId("")
        return
      }
      const hasCurrent = items.some((item) => item.knowledge_base_id === selectedKnowledgeBaseId)
      if (!hasCurrent) {
        setSelectedKnowledgeBaseId(items[0].knowledge_base_id)
      }
    } catch (error) {
      message.error(`知识库列表加载失败：${error.message}`)
    } finally {
      setLoadingKnowledgeBases(false)
    }
  }, [selectedKnowledgeBaseId])

  useEffect(() => {
    refreshKnowledgeBases()
  }, [refreshKnowledgeBases])

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

  const handleCreateKnowledgeBase = useCallback(async (name) => {
    const trimmedName = String(name || "").trim()
    if (!trimmedName) {
      message.warning("请输入知识库名称")
      return null
    }
    const result = await createKnowledgeBase(trimmedName)
    await refreshKnowledgeBases()
    if (result?.knowledge_base_id) {
      setSelectedKnowledgeBaseId(result.knowledge_base_id)
    }
    return result || null
  }, [refreshKnowledgeBases])

  const handleDeleteKnowledgeBase = useCallback(async (knowledgeBaseId) => {
    const normalizedId = String(knowledgeBaseId || "").trim()
    if (!normalizedId) {
      return
    }
    await deleteKnowledgeBase(normalizedId)
    await refreshKnowledgeBases()
  }, [refreshKnowledgeBases])

  const handleUploadKnowledgeDocument = useCallback(async (file) => {
    if (!selectedKnowledgeBaseId) {
      message.warning("请先选择知识库")
      return null
    }
    if (!file) {
      return null
    }
    try {
      setUploadingKnowledge(true)
      const result = await uploadKnowledgeDocument(selectedKnowledgeBaseId, file)
      message.success(`上传成功，已入库 ${result?.chunks || 0} 个分块`)
      return result || null
    } catch (error) {
      message.error(`上传失败：${error.message}`)
      return null
    } finally {
      setUploadingKnowledge(false)
    }
  }, [selectedKnowledgeBaseId])

  return {
    handleCreateKnowledgeBase,
    handleDeleteKnowledgeBase,
    handleKnowledgeSearch,
    handleUploadKnowledgeDocument,
    knowledgeBases,
    knowledgeGenerateQuery,
    knowledgeQuery,
    knowledgeResult,
    loadingKnowledge,
    loadingKnowledgeBases,
    refreshKnowledgeBases,
    selectedKnowledgeBaseId,
    setKnowledgeGenerateQuery,
    setKnowledgeQuery,
    setSelectedKnowledgeBaseId,
    uploadingKnowledge,
  }
}
