import React, { useEffect, useMemo, useState } from "react"
import { Button, Card, Checkbox, Divider, Input, InputNumber, List, Popconfirm, Select, Space, Spin, Tabs, Tag, Typography, Upload } from "antd"
import { generateKnowledgeAnswer } from "../api/index.js"

const EMPTY_EVIDENCE = {}

function isSelectedMapEqual(prevMap, nextMap) {
  const prevKeys = Object.keys(prevMap || {})
  const nextKeys = Object.keys(nextMap || {})
  if (prevKeys.length !== nextKeys.length) {
    return false
  }
  return prevKeys.every((key) => Boolean(prevMap[key]) === Boolean(nextMap[key]))
}

export default function KnowledgeTab({
  knowledgeBases,
  knowledgeGenerateQuery,
  knowledgeQuery,
  knowledgeResult,
  loadingKnowledge,
  loadingKnowledgeBases,
  onChangeGenerateQuery,
  onChangeQuery,
  onCreateKnowledgeBase,
  onDeleteKnowledgeBase,
  onSearch,
  onSelectKnowledgeBase,
  onUploadKnowledgeDocument,
  selectedKnowledgeBaseId,
  uploadingKnowledge,
}) {
  const [knowledgeBaseName, setKnowledgeBaseName] = useState("")
  const [filterKeyword, setFilterKeyword] = useState("")
  const [minConfidence, setMinConfidence] = useState(0)
  const [onlySelected, setOnlySelected] = useState(false)
  const [selectedMap, setSelectedMap] = useState({})
  const [answer, setAnswer] = useState("")
  const [loadingAnswer, setLoadingAnswer] = useState(false)
  const evidence = useMemo(() => knowledgeResult?.evidence || EMPTY_EVIDENCE, [knowledgeResult?.evidence])
  const knowledgeBaseOptions = Array.isArray(knowledgeBases)
    ? knowledgeBases.map((item) => ({
        label: `${item.name} (${item.knowledge_base_id})`,
        value: item.knowledge_base_id,
      }))
    : []

  const buildEvidenceId = (section, item, index) => {
    const source = item?.source || ""
    const title = item?.title || ""
    const text = item?.text || ""
    return `${section}-${index}-${source}-${title}-${text}`.replace(/\s+/g, "")
  }

  const normalizeEvidenceSection = (section) => {
    const payload = evidence?.[section] || {}
    const candidates = Array.isArray(payload.candidates) ? payload.candidates : []
    const items = Array.isArray(payload.items) ? payload.items : []
    const baseList = candidates.length > 0 ? candidates : items
    return baseList.map((item, index) => ({
      ...item,
      _section: section,
      _id: buildEvidenceId(section, item, index),
      _isCandidate: candidates.length > 0,
    }))
  }

  const summaryEntries = useMemo(() => normalizeEvidenceSection("summary"), [evidence])
  const bodyEntries = useMemo(() => normalizeEvidenceSection("body"), [evidence])

  useEffect(() => {
    if (!knowledgeResult) {
      setSelectedMap((prev) => (Object.keys(prev).length ? {} : prev))
      setAnswer((prev) => (prev ? "" : prev))
      return
    }
    const nextSelected = {}
    const allEntries = [...summaryEntries, ...bodyEntries]
    allEntries.forEach((entry) => {
      nextSelected[entry._id] = true
    })
    setSelectedMap((prev) => (isSelectedMapEqual(prev, nextSelected) ? prev : nextSelected))
    setAnswer((prev) => {
      const nextAnswer = knowledgeResult.answer || ""
      return prev === nextAnswer ? prev : nextAnswer
    })
  }, [knowledgeResult, summaryEntries, bodyEntries])

  const filterEntries = (entries) => {
    const keyword = filterKeyword.trim()
    return entries.filter((entry) => {
      const selected = Boolean(selectedMap[entry._id])
      if (onlySelected && !selected) {
        return false
      }
      const confidence = Number.isFinite(entry?.confidence) ? Number(entry.confidence) : null
      if (confidence !== null && confidence < minConfidence) {
        return false
      }
      if (!keyword) {
        return true
      }
      const haystack = `${entry?.title || ""} ${entry?.text || ""} ${entry?.source || ""}`
      return haystack.includes(keyword)
    })
  }

  const buildEvidenceForAnswer = () => {
    const buildSection = (entries, section) => {
      const selectedItems = entries
        .filter((entry) => selectedMap[entry._id])
        .map(({ _section, _id, _isCandidate, ...rest }) => rest)
      if (selectedItems.length === 0) {
        return {}
      }
      const payload = evidence?.[section] || {}
      return {
        ...payload,
        items: selectedItems,
      }
    }
    return {
      summary: buildSection(summaryEntries, "summary"),
      body: buildSection(bodyEntries, "body"),
    }
  }

  const handleGenerateAnswer = async () => {
    if (!knowledgeResult?.query) {
      return
    }
    try {
      setLoadingAnswer(true)
      const payloadEvidence = buildEvidenceForAnswer()
      const data = await generateKnowledgeAnswer(knowledgeResult.query, payloadEvidence)
      setAnswer(data?.answer || "")
    } catch (error) {
      setAnswer("")
    } finally {
      setLoadingAnswer(false)
    }
  }

  const handleCreateKnowledgeBase = async () => {
    if (!onCreateKnowledgeBase) {
      return
    }
    const result = await onCreateKnowledgeBase(knowledgeBaseName)
    if (result?.knowledge_base_id) {
      setKnowledgeBaseName("")
    }
  }

  const renderEvidenceList = (entries) => {
    const filteredEntries = filterEntries(entries)
    return (
      <List
        dataSource={filteredEntries}
        locale={{ emptyText: "暂无匹配证据" }}
        renderItem={(item, index) => {
          const confidenceValue = Number.isFinite(item?.confidence) ? Number(item.confidence) : null
          const sourceValue = item?.source || ""
          return (
            <List.Item className="evidence-item">
              <div className="evidence-item-header">
                <Checkbox
                  checked={Boolean(selectedMap[item._id])}
                  onChange={(event) =>
                    setSelectedMap((prev) => ({
                      ...prev,
                      [item._id]: event.target.checked,
                    }))
                  }
                >
                  证据 {index + 1}
                </Checkbox>
                <Space size="small">
                  {item?._isCandidate && <Tag color="purple">候选</Tag>}
                  {confidenceValue !== null && <Tag color="blue">置信度 {confidenceValue.toFixed(2)}</Tag>}
                </Space>
              </div>
              {item?.title && <div className="evidence-title">{item.title}</div>}
              {item?.text && <div className="evidence-text">{item.text}</div>}
              <div className="evidence-meta">
                <span>来源：</span>
                {sourceValue ? (
                  <a href={sourceValue} target="_blank" rel="noreferrer">
                    {sourceValue}
                  </a>
                ) : (
                  "未知来源"
                )}
              </div>
            </List.Item>
          )
        }}
      />
    )
  }

  return (
    <div className="knowledge-layout">
      <Card title="旅行灵感检索" className="panel-card">
        <Space direction="vertical" className="full-width">
          <Card size="small" title="知识库管理" className="evidence-card">
            <Space direction="vertical" className="full-width" size="small">
              <Space.Compact className="full-width">
                <Input
                  placeholder="新知识库名称"
                  value={knowledgeBaseName}
                  onChange={(event) => setKnowledgeBaseName(event.target.value)}
                />
                <Button onClick={handleCreateKnowledgeBase}>创建</Button>
              </Space.Compact>
              <Space size="small" wrap>
                <Select
                  className="knowledge-base-select"
                  placeholder="选择知识库"
                  value={selectedKnowledgeBaseId || undefined}
                  options={knowledgeBaseOptions}
                  loading={loadingKnowledgeBases}
                  onChange={(value) => onSelectKnowledgeBase && onSelectKnowledgeBase(value)}
                />
                <Popconfirm
                  title="确认删除当前知识库？"
                  onConfirm={() => onDeleteKnowledgeBase && onDeleteKnowledgeBase(selectedKnowledgeBaseId)}
                  okText="删除"
                  cancelText="取消"
                >
                  <Button danger disabled={!selectedKnowledgeBaseId}>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
              <Upload
                showUploadList={false}
                beforeUpload={(file) => {
                  if (onUploadKnowledgeDocument) {
                    onUploadKnowledgeDocument(file)
                  }
                  return false
                }}
                disabled={!selectedKnowledgeBaseId || uploadingKnowledge}
              >
                <Button loading={uploadingKnowledge} disabled={!selectedKnowledgeBaseId}>
                  上传 PDF/Markdown/文本
                </Button>
              </Upload>
            </Space>
          </Card>
          <Input
            placeholder="行程生成时的知识库检索条件（可选）"
            value={knowledgeGenerateQuery}
            onChange={(event) => onChangeGenerateQuery && onChangeGenerateQuery(event.target.value)}
          />
          <Input
            placeholder="输入要检索的问题或主题"
            value={knowledgeQuery}
            onChange={(event) => onChangeQuery(event.target.value)}
          />
          <Button type="primary" onClick={onSearch} loading={loadingKnowledge}>
            开始检索
          </Button>
        </Space>
      </Card>
      <Card title="检索结果" className="panel-card">
        <Spin spinning={loadingKnowledge}>
          {!knowledgeResult && <div className="empty-tip">暂无检索结果</div>}
          {knowledgeResult && (
            <Space direction="vertical" size="middle" className="full-width">
              <div className="trip-summary">查询：{knowledgeResult.query}</div>
              <Divider />
              <Card size="small" title="证据筛选" className="evidence-card">
                <Space direction="vertical" size="small" className="full-width">
                  <Input
                    placeholder="关键词筛选"
                    value={filterKeyword}
                    onChange={(event) => setFilterKeyword(event.target.value)}
                  />
                  <Space size="middle" wrap>
                    <div className="evidence-filter">
                      <span>最低置信度</span>
                      <InputNumber
                        min={0}
                        max={1}
                        step={0.05}
                        value={minConfidence}
                        onChange={(value) => setMinConfidence(Number(value) || 0)}
                      />
                    </div>
                    <Checkbox checked={onlySelected} onChange={(event) => setOnlySelected(event.target.checked)}>
                      仅看已勾选
                    </Checkbox>
                  </Space>
                </Space>
              </Card>
              <Tabs
                items={[
                  {
                    key: "summary",
                    label: `摘要证据 (${summaryEntries.length})`,
                    children: renderEvidenceList(summaryEntries),
                  },
                  {
                    key: "body",
                    label: `正文证据 (${bodyEntries.length})`,
                    children: renderEvidenceList(bodyEntries),
                  },
                ]}
              />
              <Space direction="vertical" size="small" className="full-width">
                <Button type="primary" onClick={handleGenerateAnswer} loading={loadingAnswer}>
                  用已选证据生成回答
                </Button>
                {answer && (
                  <Card size="small" title="生成回答" className="evidence-answer">
                    <Typography.Paragraph>{answer}</Typography.Paragraph>
                  </Card>
                )}
              </Space>
            </Space>
          )}
        </Spin>
      </Card>
    </div>
  )
}
