import React, { useEffect, useMemo, useState } from "react"
import { Alert, Button, Card, Checkbox, Collapse, Divider, Input, InputNumber, List, Modal, Popconfirm, Radio, Select, Space, Spin, Tabs, Tag, Typography, Upload } from "antd"
import { generateKnowledgeAnswer } from "../api/index.js"
import { logDebug } from "../utils/debugLogger.js"

const EMPTY_EVIDENCE = {}

function getRiskTagColor(riskLevel) {
  if (riskLevel === "high") {
    return "red"
  }
  if (riskLevel === "medium") {
    return "gold"
  }
  return "green"
}

function buildFailureGuide(platform, errorCode, fallbackMessage = "解析失败，建议切换手动导入并补充正文") {
  // 这里把后端错误码映射成“下一步怎么操作”的文案，
  // 目标不是解释技术细节，而是减少用户在失败场景里的试错成本。
  const normalizedPlatform = String(platform || "unknown")
  const normalizedErrorCode = String(errorCode || "")
  const platformPrefix = normalizedPlatform === "unknown" ? "当前链接" : `${normalizedPlatform} 链接`
  const guideMap = {
    AUTO_PARSE_EMPTY: `${platformPrefix}正文提取为空，建议切换手动导入并粘贴完整正文`,
    AUTO_PARSE_LOW_QUALITY: `${platformPrefix}质量不足，建议粘贴去广告后的正文`,
    AUTO_PARSE_DUPLICATED: `${platformPrefix}已导入过相同来源，可直接复用历史内容`,
    AUTO_PARSE_LOGIN_REQUIRED: `${platformPrefix}需要登录可见，建议手动粘贴内容`,
    AUTO_PARSE_RISK_VERIFICATION: `${platformPrefix}触发风控验证，建议手动导入`,
    AUTO_PARSE_BLOCKED: `${platformPrefix}命中平台限制文案，建议手动导入`,
    AUTO_PARSE_PAYWALLED: `${platformPrefix}命中付费限制，建议补充可公开内容后导入`,
    URL_RESOLVE_TIMEOUT: "短链解跳超时，建议粘贴解跳后的原始链接再试",
    URL_RESOLVE_LOOP: "短链重定向异常，建议手动打开后复制最终链接",
  }
  if (!normalizedErrorCode) {
    return fallbackMessage
  }
  return guideMap[normalizedErrorCode] || `${fallbackMessage}（${normalizedErrorCode}）`
}

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
  knowledgeDebugSnapshot,
  knowledgeGenerateQuery,
  knowledgeQuery,
  knowledgeResult,
  knowledgeScope,
  knowledgeSources,
  lastFlowKnowledgeDebug,
  sourceStats,
  loadingKnowledgeDebugSnapshot,
  loadingKnowledgeSources,
  ingestingKnowledge,
  preprocessingKnowledgeUrl,
  knowledgeUrlPreprocessResult,
  lastIngestResult,
  loadingKnowledge,
  loadingKnowledgeBases,
  onChangeGenerateQuery,
  onChangeKnowledgeScope,
  onChangeQuery,
  onCreateKnowledgeBase,
  onDeleteKnowledgeBase,
  onDeleteKnowledgeSource,
  onIngestKnowledgeUrl,
  onPreprocessKnowledgeUrl,
  onLoadKnowledgeDebugSnapshot,
  onRefreshKnowledgeSources,
  onSearch,
  onSelectKnowledgeBase,
  onUpdateKnowledgeSource,
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
  const [ingestUrl, setIngestUrl] = useState("")
  const [ingestMode, setIngestMode] = useState("auto")
  const [manualText, setManualText] = useState("")
  const [ocrText, setOcrText] = useState("")
  const [subtitleText, setSubtitleText] = useState("")
  const [debugModalOpen, setDebugModalOpen] = useState(false)
  const [loadingDebugModal, setLoadingDebugModal] = useState(false)
  const [editSourceOpen, setEditSourceOpen] = useState(false)
  const [editingSource, setEditingSource] = useState(null)
  const [editingSourceUrl, setEditingSourceUrl] = useState("")
  const [editingSourceContent, setEditingSourceContent] = useState("")
  const [updatingSource, setUpdatingSource] = useState(false)
  const evidence = useMemo(() => knowledgeResult?.evidence || EMPTY_EVIDENCE, [knowledgeResult?.evidence])
  const normalizedKnowledgeSources = useMemo(
    () => (Array.isArray(knowledgeSources) ? knowledgeSources : []),
    [knowledgeSources]
  )
  // 社交来源列表单独展示在“社交链接导入”区域，初始化优先按“有链接”识别，避免历史数据 source_type 不标准导致列表空白。
  const socialSources = useMemo(
    () =>
      normalizedKnowledgeSources.filter((item) => {
        const sourceType = String(item?.source_type || "").trim().toLowerCase()
        const sourcePlatform = String(item?.source_platform || "").trim().toLowerCase()
        const sourceUrl = String(item?.source_url || "").trim()
        return (
          Boolean(sourceUrl) ||
          sourceType === "url" ||
          sourceType === "manual" ||
          sourceType === "ocr" ||
          sourcePlatform === "xiaohongshu" ||
          sourcePlatform === "weibo" ||
          sourcePlatform === "bilibili"
        )
      }),
    [normalizedKnowledgeSources]
  )
  const visibleSocialSources = useMemo(
    () =>
      socialSources.length > 0
        ? socialSources
        : normalizedKnowledgeSources.filter((item) => String(item?.source_url || "").trim()),
    [socialSources, normalizedKnowledgeSources]
  )
  const preprocessGuide = useMemo(() => {
    // preprocessGuide 的职责是把后端较底层的风控/质量信号翻译成面向用户的操作建议，
    // 例如“继续自动解析”还是“直接切手动导入”。
    const platform = String(knowledgeUrlPreprocessResult?.source_platform || "unknown")
    const riskLevel = String(knowledgeUrlPreprocessResult?.source_risk_level || "")
    const errorCode = String(knowledgeUrlPreprocessResult?.ingest_error_code || "")
    const qualityScore = knowledgeUrlPreprocessResult?.quality_score
    const extractorLayer = String(knowledgeUrlPreprocessResult?.extractor_layer || "")
    if (errorCode) {
      return {
        message: buildFailureGuide(platform, errorCode, "预处理预判当前链接自动解析成功率较低，建议直接切换手动/OCR 导入"),
        type: "warning",
      }
    }
    if (riskLevel === "high") {
      return {
        message: "该链接为高风险平台，建议优先手动导入（粘贴正文/OCR）",
        type: "warning",
      }
    }
    if (riskLevel === "medium") {
      return {
        message: "该链接可尝试自动解析，建议同时准备手动文本作为备份",
        type: "info",
      }
    }
    if (riskLevel === "low") {
      return {
        message:
          qualityScore !== undefined && qualityScore !== null
            ? `该链接预判可自动解析，当前质量分 ${qualityScore}${extractorLayer ? `，命中 ${extractorLayer}` : ""}`
            : "该链接支持自动解析，点击导入即可",
        type: "success",
      }
    }
    return null
  }, [knowledgeUrlPreprocessResult])
  const failedGuide = useMemo(() => {
    const platform = String(lastIngestResult?.metadata?.source_platform || "unknown")
    const errorCode = String(lastIngestResult?.metadata?.ingest_error_code || "")
    return buildFailureGuide(platform, errorCode, "解析失败，建议切换手动导入模式并粘贴文本/OCR 内容")
  }, [lastIngestResult])
  const effectiveKnowledgeDebug = useMemo(() => {
    // 优先展示主流程返回的命中来源；
    // 若当前还没有跑主流程，则回退到知识检索接口返回的调试信息。
    if (lastFlowKnowledgeDebug) {
      return lastFlowKnowledgeDebug
    }
    if (!knowledgeResult) {
      return null
    }
    const knowledgeDebug = knowledgeResult?.knowledge_debug || {}
    const sourceEvidence = Array.isArray(knowledgeResult?.source_evidence)
      ? knowledgeResult.source_evidence
      : []
    return {
      knowledge_scope: String(knowledgeDebug?.knowledge_scope || knowledgeScope || ""),
      allow_public_fusion: Boolean(knowledgeDebug?.allow_public_fusion),
      kb_context_count: Number(knowledgeDebug?.kb_context_count || 0),
      source_evidence_count: Number(knowledgeDebug?.source_evidence_count || sourceEvidence.length || 0),
      source_evidence: sourceEvidence,
    }
  }, [knowledgeResult, knowledgeScope, lastFlowKnowledgeDebug])
  const knowledgeBaseOptions = Array.isArray(knowledgeBases)
    ? knowledgeBases.map((item) => ({
        label: `${item.name} (${item.knowledge_base_id}) · ${item.document_count || 0}分块 · ${item.last_updated_at ? `更新:${item.last_updated_at.slice(0, 16).replace("T", " ")}` : "未更新"}`,
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

  useEffect(() => {
    if (!selectedKnowledgeBaseId || !onRefreshKnowledgeSources) {
      return
    }
    // 每次进入或切换知识库时主动刷新来源，保证重载后社交列表立即可见。
    onRefreshKnowledgeSources(selectedKnowledgeBaseId)
  }, [onRefreshKnowledgeSources, selectedKnowledgeBaseId])

  useEffect(() => {
    if (lastIngestResult?.ingest_status !== "failed") {
      return
    }
    // 自动解析失败后要把 UI 强制切到 manual：
    // 仅展示文本框还不够，如果 radio 状态不跟着切，用户会误以为当前仍在“自动解析”模式。
    setIngestMode("manual")
    const failedSourceUrl = String(lastIngestResult?.metadata?.source_url || "").trim()
    if (!failedSourceUrl) {
      return
    }
    setIngestUrl((prev) => (String(prev || "").trim() ? prev : failedSourceUrl))
  }, [lastIngestResult])

  useEffect(() => {
    logDebug("知识库", "社交来源列表状态更新", {
      selectedKnowledgeBaseId: String(selectedKnowledgeBaseId || ""),
      knowledgeSourcesCount: normalizedKnowledgeSources.length,
      socialSourcesCount: socialSources.length,
      visibleSocialSourcesCount: visibleSocialSources.length,
      firstVisibleSourceId: String(visibleSocialSources[0]?.source_id || ""),
    })
  }, [normalizedKnowledgeSources, selectedKnowledgeBaseId, socialSources, visibleSocialSources])

  useEffect(() => {
    const sourceUrl = String(ingestUrl || "").trim()
    if (!sourceUrl || !onPreprocessKnowledgeUrl) {
      return
    }
    let isCanceled = false
    const timer = window.setTimeout(async () => {
      if (isCanceled) {
        return
      }
      const result = await onPreprocessKnowledgeUrl(sourceUrl)
      if (!result) {
        return
      }
      // 这里做防抖预处理，避免用户每输入一个字符就触发一次后端请求；
      // 同时依据预处理结果自动建议默认模式，减少高风险平台上的无效点击。
      if (
        String(result?.source_risk_level || "") === "high" ||
        (Boolean(result?.requires_user_assist) && Boolean(String(result?.ingest_error_code || "").trim()))
      ) {
        setIngestMode("manual")
      }
      if (
        String(result?.source_risk_level || "") === "low" &&
        !Boolean(result?.requires_user_assist) &&
        ingestMode !== "manual"
      ) {
        setIngestMode("auto")
      }
    }, 400)
    return () => {
      isCanceled = true
      window.clearTimeout(timer)
    }
  }, [ingestMode, ingestUrl, onPreprocessKnowledgeUrl])

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
    // 重新组装用户勾选后的 evidence，
    // 让“二次生成回答”只基于用户认可的证据，而不是默认吃下全部候选项。
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

  const handleIngestUrl = async () => {
    if (!onIngestKnowledgeUrl) {
      return
    }
    const currentUrl = String(ingestUrl || "").trim()
    // subtitleText 并到 ocr_text，是因为当前后端把 OCR/字幕都视为“辅助补全文本”，
    // 最终统一走 fallback 入库链路，避免再拆一条平行导入协议。
    const result = await onIngestKnowledgeUrl({
      url: currentUrl,
      mode: ingestMode,
      manual_text: manualText,
      ocr_text: [ocrText, subtitleText].filter(Boolean).join("\n"),
    })
    if (result?.success) {
      setManualText("")
      setOcrText("")
      setSubtitleText("")
      setIngestUrl("")
      return
    }
    // 失败时显式回填 URL，避免组件重渲染或状态刷新后把用户刚输入的链接丢掉，
    // 这样用户可以直接补正文继续重试，而不需要重新粘贴链接。
    const fallbackUrl =
      String(result?.metadata?.source_url || "").trim() || currentUrl
    if (fallbackUrl) {
      setIngestUrl(fallbackUrl)
    }
  }

  const handleOpenDebugModal = async () => {
    if (!onLoadKnowledgeDebugSnapshot) {
      return
    }
    setDebugModalOpen(true)
    try {
      setLoadingDebugModal(true)
      await onLoadKnowledgeDebugSnapshot()
    } finally {
      setLoadingDebugModal(false)
    }
  }

  const handleOpenEditSource = (item) => {
    // 编辑弹窗既可修改 parsed/fallback 来源，也可用于修复 failed 来源，
    // 因此前置回填 source_url 和正文预览，尽量减少用户重复输入。
    setEditingSource(item || null)
    setEditingSourceUrl(String(item?.source_url || ""))
    setEditingSourceContent(String(item?.parsed_content_preview || ""))
    setEditSourceOpen(true)
  }

  const handleUpdateSourceContent = async () => {
    if (!onUpdateKnowledgeSource || !editingSource?.source_id) {
      return
    }
    try {
      setUpdatingSource(true)
      await onUpdateKnowledgeSource(editingSource.source_id, {
        content: editingSourceContent,
        source_url: editingSourceUrl,
      })
      setEditSourceOpen(false)
    } finally {
      setUpdatingSource(false)
    }
  }

  const debugKnowledgeBases = Array.isArray(knowledgeDebugSnapshot?.items)
    ? knowledgeDebugSnapshot.items
    : []

  const renderEvidenceList = (entries) => {
    const filteredEntries = filterEntries(entries)
    return (
      <List
        dataSource={filteredEntries}
        locale={{ emptyText: "暂无匹配证据" }}
        renderItem={(item, index) => {
          const confidenceValue = Number.isFinite(item?.confidence) ? Number(item.confidence) : null
          const sourceValue = item?.source || ""
          const sourceTypeValue = String(item?.source_type || "").toLowerCase()
          const sourcePlatformValue = String(item?.source_platform || "").toLowerCase()
          const isPrivateEvidence = Boolean(sourceTypeValue) || String(sourceValue).startsWith("private://")
          const isUploadSource = ["pdf", "markdown", "txt"].includes(sourceTypeValue)
          const isSocialSource = ["url", "manual", "ocr"].includes(sourceTypeValue)
          // v0.0.6 的证据面板需要明确区分“公网检索”和“私有来源”，
          // 私有来源里又进一步区分上传文件与社交导入，便于用户理解答案依据。
          const sourceCategoryLabel = isPrivateEvidence
            ? isUploadSource
              ? "私有·上传文件"
              : isSocialSource
                ? "私有·社交导入"
                : "私有·其他来源"
            : "公网检索"
          const sourceCategoryColor = isPrivateEvidence ? (isSocialSource ? "magenta" : "cyan") : "geekblue"
          const engineValue = String(item?.engine || "")
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
                  <Tag color={sourceCategoryColor}>{sourceCategoryLabel}</Tag>
                  {sourcePlatformValue && <Tag>{sourcePlatformValue}</Tag>}
                  {engineValue && <Tag color="gold">引擎 {engineValue}</Tag>}
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
      <Card title="知识库管理" className="panel-card">
        <Space orientation="vertical" className="full-width">
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
            <Button onClick={handleOpenDebugModal} loading={loadingDebugModal || loadingKnowledgeDebugSnapshot}>
              调试数据
            </Button>
          </Space>
        </Space>
      </Card>
      <Card title="数据来源" className="panel-card">
        <Space orientation="vertical" className="full-width">
          <Card size="small" title="文档上传（来源类型：upload）" className="evidence-card">
            <Space orientation="vertical" className="full-width" size="small">
              <Typography.Text type="secondary">上传后的数据会写入当前知识库并参与私有检索。</Typography.Text>
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
          <Card size="small" title="社交链接导入（来源类型：social_url/manual/ocr）" className="evidence-card">
            <Space orientation="vertical" className="full-width" size="small">
              <Input
                placeholder="粘贴公开可访问链接"
                value={ingestUrl}
                onChange={(event) => setIngestUrl(event.target.value)}
              />
              <Space size="small" wrap>
                {preprocessingKnowledgeUrl && <Tag color="processing">预处理分析中</Tag>}
                {knowledgeUrlPreprocessResult?.source_platform && (
                  <Tag>平台 {knowledgeUrlPreprocessResult.source_platform}</Tag>
                )}
                {knowledgeUrlPreprocessResult?.source_risk_level && (
                  <Tag
                    color={getRiskTagColor(knowledgeUrlPreprocessResult.source_risk_level)}
                  >
                    风险 {knowledgeUrlPreprocessResult.source_risk_level}
                  </Tag>
                )}
                {knowledgeUrlPreprocessResult?.resolve_error_code && (
                  <Tag color="volcano">resolve {knowledgeUrlPreprocessResult.resolve_error_code}</Tag>
                )}
                {knowledgeUrlPreprocessResult?.ingest_error_code && (
                  <Tag color="volcano">error {knowledgeUrlPreprocessResult.ingest_error_code}</Tag>
                )}
                {knowledgeUrlPreprocessResult?.extractor_layer && (
                  <Tag>layer {knowledgeUrlPreprocessResult.extractor_layer}</Tag>
                )}
                {knowledgeUrlPreprocessResult?.quality_score !== undefined &&
                  knowledgeUrlPreprocessResult?.quality_score !== null && (
                    <Tag color="blue">quality {knowledgeUrlPreprocessResult.quality_score}</Tag>
                  )}
                {knowledgeUrlPreprocessResult?.parsed_content_chars ? (
                  <Tag>chars {knowledgeUrlPreprocessResult.parsed_content_chars}</Tag>
                ) : null}
                {knowledgeUrlPreprocessResult?.requires_user_assist ? (
                  <Tag color="orange">建议手动辅助</Tag>
                ) : null}
              </Space>
              {preprocessGuide && (
                <Alert
                  showIcon
                  type={preprocessGuide.type}
                  message={preprocessGuide.message}
                />
              )}
              <Radio.Group
                value={ingestMode}
                onChange={(event) => setIngestMode(event.target.value)}
                options={[
                  { label: "自动解析", value: "auto" },
                  { label: "手动导入", value: "manual" },
                ]}
              />
              {(ingestMode === "manual" || lastIngestResult?.ingest_status === "failed") && (
                <Space orientation="vertical" className="full-width" size="small">
                  <Input.TextArea
                    rows={4}
                    placeholder="手动粘贴正文（解析失败时建议粘贴）"
                    value={manualText}
                    onChange={(event) => setManualText(event.target.value)}
                  />
                  <Input.TextArea
                    rows={3}
                    placeholder="可选：粘贴 OCR 文本"
                    value={ocrText}
                    onChange={(event) => setOcrText(event.target.value)}
                  />
                  <Input.TextArea
                    rows={3}
                    placeholder="可选：粘贴视频字幕文本（会并入 OCR 文本）"
                    value={subtitleText}
                    onChange={(event) => setSubtitleText(event.target.value)}
                  />
                </Space>
              )}
              <Button type="primary" onClick={handleIngestUrl} loading={ingestingKnowledge} disabled={!selectedKnowledgeBaseId}>
                导入社交内容
              </Button>
              <Space size="small" wrap>
                <Tag color={ingestingKnowledge ? "processing" : "default"}>{ingestingKnowledge ? "解析中" : "待导入"}</Tag>
                {lastIngestResult?.ingest_status === "parsed" && <Tag color="success">解析成功</Tag>}
                {lastIngestResult?.ingest_status === "fallback" && <Tag color="warning">降级成功</Tag>}
                {lastIngestResult?.ingest_status === "failed" && <Tag color="error">解析失败</Tag>}
              </Space>
              {lastIngestResult?.ingest_status === "failed" && (
                <Alert type="warning" showIcon message={failedGuide} />
              )}
              {lastIngestResult?.parsed_content_preview && (
                <Card size="small" title="本次解析正文预览">
                  <Space orientation="vertical" className="full-width" size={4}>
                    <Tag>字符数 {lastIngestResult?.parsed_content_chars || 0}</Tag>
                    <Input.TextArea
                      value={lastIngestResult?.parsed_content_preview || ""}
                      autoSize={{ minRows: 4, maxRows: 12 }}
                      readOnly
                    />
                  </Space>
                </Card>
              )}
              {knowledgeUrlPreprocessResult?.parsed_content_preview && (
                <Card size="small" title="预处理正文预览">
                  <Space orientation="vertical" className="full-width" size={4}>
                    <Tag>字符数 {knowledgeUrlPreprocessResult?.parsed_content_chars || 0}</Tag>
                    <Input.TextArea
                      value={knowledgeUrlPreprocessResult?.parsed_content_preview || ""}
                      autoSize={{ minRows: 4, maxRows: 10 }}
                      readOnly
                    />
                  </Space>
                </Card>
              )}
            </Space>
          </Card>
        </Space>
      </Card>
      <Card title="当前知识库来源列表" className="panel-card">
        <Space orientation="vertical" className="full-width" size="small">
          <Space size="small" wrap>
            <Tag>总数 {sourceStats?.total || 0}</Tag>
            <Tag color="success">parsed {sourceStats?.parsed || 0}</Tag>
            <Tag color="warning">fallback {sourceStats?.fallback || 0}</Tag>
            <Tag color="error">failed {sourceStats?.failed || 0}</Tag>
          </Space>
          <Spin spinning={loadingKnowledgeSources}>
            <List
              className="knowledge-source-list"
              dataSource={knowledgeSources || []}
              locale={{ emptyText: "暂无来源记录" }}
              renderItem={(item) => (
                <List.Item
                  actions={[
                    <Button
                      key={`edit-${item.source_id}`}
                      size="small"
                      onClick={() => handleOpenEditSource(item)}
                    >
                      {item.ingest_status === "failed" ? "重试" : "修改"}
                    </Button>,
                    <Popconfirm
                      key={`delete-${item.source_id}`}
                      title="确认删除该来源及分块？"
                      onConfirm={() => onDeleteKnowledgeSource && onDeleteKnowledgeSource(item.source_id)}
                      okText="删除"
                      cancelText="取消"
                    >
                      <Button size="small" danger>
                        删除
                      </Button>
                    </Popconfirm>,
                  ]}
                >
                  <Space orientation="vertical" size={2} className="full-width">
                    <Space size="small" wrap>
                      <Tag>{item.source_type || "unknown"}</Tag>
                      <Tag>{item.source_platform || "unknown"}</Tag>
                      <Tag color={item.ingest_status === "parsed" ? "success" : item.ingest_status === "fallback" ? "warning" : "error"}>
                        {item.ingest_status || "unknown"}
                      </Tag>
                      {item.ingest_error_code && <Tag color="volcano">error {item.ingest_error_code}</Tag>}
                      {item.source_risk_level && (
                        <Tag
                          color={getRiskTagColor(item.source_risk_level)}
                        >
                          risk {item.source_risk_level}
                        </Tag>
                      )}
                      {item.extractor_layer && <Tag>layer {item.extractor_layer}</Tag>}
                      {item.quality_score !== undefined && item.quality_score !== null && (
                        <Tag color="blue">quality {item.quality_score}</Tag>
                      )}
                      <Tag>chunks {item.chunks_count || 0}</Tag>
                      <Tag>chars {item.parsed_content_chars || 0}</Tag>
                    </Space>
                    <Typography.Text type="secondary">source_id: {item.source_id || "-"}</Typography.Text>
                    {item.source_url ? (
                      <a href={item.source_url} target="_blank" rel="noreferrer">
                        {item.source_url}
                      </a>
                    ) : (
                      <Typography.Text type="secondary">无原始链接</Typography.Text>
                    )}
                    <Typography.Text type="secondary">
                      {(item.parsed_content_preview || "").slice(0, 140) || "暂无正文预览"}
                    </Typography.Text>
                  </Space>
                </List.Item>
              )}
            />
          </Spin>
        </Space>
      </Card>
      <Card title="检索知识库" className="panel-card">
        <Space orientation="vertical" className="full-width">
          <Input
            placeholder="行程生成时的知识库检索条件（可选）"
            value={knowledgeGenerateQuery}
            onChange={(event) => onChangeGenerateQuery && onChangeGenerateQuery(event.target.value)}
          />
          <Select
            value={knowledgeScope || "private_plus_public"}
            options={[
              { label: "仅使用私有知识", value: "private_only" },
              { label: "私有+公网融合", value: "private_plus_public" },
            ]}
            onChange={(value) => onChangeKnowledgeScope && onChangeKnowledgeScope(value)}
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
      <Card size="small" title="私有知识命中调试" className="panel-card">
        {!effectiveKnowledgeDebug && <Typography.Text type="secondary">暂无调试信息（主流程或知识库检索后显示）</Typography.Text>}
        {effectiveKnowledgeDebug && (
          <Space orientation="vertical" size="small" className="full-width">
            <Space size="small" wrap>
              <Tag>scope {effectiveKnowledgeDebug?.knowledge_scope || "-"}</Tag>
              <Tag color={effectiveKnowledgeDebug?.allow_public_fusion ? "blue" : "default"}>
                {effectiveKnowledgeDebug?.allow_public_fusion ? "公网工具已开启" : "仅私有知识"}
              </Tag>
              <Tag color="success">私有片段 {effectiveKnowledgeDebug?.kb_context_count || 0}</Tag>
              <Tag color="purple">实际命中来源 {effectiveKnowledgeDebug?.source_evidence_count || 0}</Tag>
            </Space>
            <Typography.Text type="secondary">此处仅展示本次检索或主流程实际命中的来源；全部来源请查看上方来源列表。</Typography.Text>
          </Space>
        )}
        {Array.isArray(effectiveKnowledgeDebug?.source_evidence) && effectiveKnowledgeDebug.source_evidence.length > 0 && (
          <List
            size="small"
            dataSource={effectiveKnowledgeDebug.source_evidence}
            renderItem={(item) => (
              <List.Item>
                <Space size="small" wrap>
                  <Tag>{item?.source_type || "unknown"}</Tag>
                  <Tag>{item?.source_platform || "unknown"}</Tag>
                  <Tag color={item?.ingest_status === "parsed" ? "success" : item?.ingest_status === "fallback" ? "warning" : "error"}>
                    {item?.ingest_status || "unknown"}
                  </Tag>
                  {item?.hit_count ? <Tag color="blue">hits {item.hit_count}</Tag> : null}
                  {item?.source_url ? (
                    <a href={item.source_url} target="_blank" rel="noreferrer">
                      {item.source_url}
                    </a>
                  ) : (
                    <Typography.Text type="secondary">无来源链接</Typography.Text>
                  )}
                </Space>
              </List.Item>
            )}
          />
        )}
      </Card>
      <Card title="检索结果" className="panel-card">
        <Spin spinning={loadingKnowledge}>
          {!knowledgeResult && <div className="empty-tip">暂无检索结果</div>}
          {knowledgeResult && (
            <Space orientation="vertical" size="middle" className="full-width">
              <div className="trip-summary">查询：{knowledgeResult.query}</div>
              <Divider />
              <Card size="small" title="证据筛选" className="evidence-card">
                <Space orientation="vertical" size="small" className="full-width">
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
              <Space orientation="vertical" size="small" className="full-width">
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
      <Modal
        title={editingSource?.ingest_status === "failed" ? "补全文本并重试失败来源" : "修改社交来源正文"}
        open={editSourceOpen}
        onCancel={() => setEditSourceOpen(false)}
        onOk={handleUpdateSourceContent}
        okText={editingSource?.ingest_status === "failed" ? "提交重试" : "保存修改"}
        cancelText="取消"
        confirmLoading={updatingSource}
      >
        <Space orientation="vertical" className="full-width" size="small">
          {editingSource?.ingest_error_code && (
            <Alert type="warning" showIcon message={`失败原因：${editingSource.ingest_error_code}`} />
          )}
          <Input
            value={editingSourceUrl}
            onChange={(event) => setEditingSourceUrl(event.target.value)}
            placeholder="来源链接（可选）"
          />
          <Input.TextArea
            value={editingSourceContent}
            onChange={(event) => setEditingSourceContent(event.target.value)}
            autoSize={{ minRows: 6, maxRows: 14 }}
            placeholder="请输入更新后的正文内容"
          />
        </Space>
      </Modal>
      <Modal
        title="知识库调试快照"
        open={debugModalOpen}
        onCancel={() => setDebugModalOpen(false)}
        footer={null}
        width={920}
      >
        <Spin spinning={loadingDebugModal || loadingKnowledgeDebugSnapshot}>
          <Space orientation="vertical" className="full-width" size="middle">
            <Typography.Text type="secondary">
              快照时间：{knowledgeDebugSnapshot?.generated_at || "-"}
            </Typography.Text>
            {debugKnowledgeBases.length === 0 && <div className="empty-tip">暂无知识库调试数据</div>}
            {debugKnowledgeBases.map((kb) => (
              <Card
                key={kb.knowledge_base_id}
                size="small"
                title={`${kb.name} (${kb.knowledge_base_id})`}
                extra={<Tag>分块 {kb.document_count || 0}</Tag>}
              >
                <Space size="small" wrap>
                  <Tag>集合 {kb.collection_name || "-"}</Tag>
                  <Tag>来源 {kb.source_count || 0}</Tag>
                  <Tag>更新时间 {kb.last_updated_at || "-"}</Tag>
                </Space>
                <Collapse
                  style={{ marginTop: 8 }}
                  items={(Array.isArray(kb.sources) ? kb.sources : []).map((source) => ({
                    key: source.source_id,
                    label: `${source.source_type || "unknown"} · ${source.source_platform || "unknown"} · ${source.ingest_status || "unknown"} · chunks ${source.chunks_count || 0}`,
                    children: (
                      <Space orientation="vertical" className="full-width" size="small">
                        <Typography.Text>source_id: {source.source_id || "-"}</Typography.Text>
                        <Typography.Text>source_url: {source.source_url || "-"}</Typography.Text>
                        <Typography.Text>ingested_at: {source.ingested_at || "-"}</Typography.Text>
                        <Typography.Text>ingest_error_code: {source.ingest_error_code || "-"}</Typography.Text>
                        <Space size="small" wrap>
                          <Tag>解析字符 {source.parsed_content_chars || 0}</Tag>
                          <Tag>分块数 {source.chunks_count || 0}</Tag>
                        </Space>
                        <Input.TextArea
                          value={source.parsed_content_preview || ""}
                          autoSize={{ minRows: 5, maxRows: 12 }}
                          readOnly
                        />
                        <List
                          size="small"
                          dataSource={Array.isArray(source.chunks) ? source.chunks : []}
                          locale={{ emptyText: "暂无分块内容" }}
                          renderItem={(chunk) => (
                            <List.Item>
                              <Space orientation="vertical" className="full-width" size={2}>
                                <Typography.Text>
                                  chunk {chunk?.chunk_index || "-"} / {chunk?.chunk_total || "-"} · chunk_id: {chunk?.chunk_id || "-"} · chars: {chunk?.content_chars || 0}
                                </Typography.Text>
                                <Input.TextArea
                                  value={chunk?.content || ""}
                                  autoSize={{ minRows: 6, maxRows: 16 }}
                                  readOnly
                                />
                              </Space>
                            </List.Item>
                          )}
                        />
                      </Space>
                    ),
                  }))}
                />
              </Card>
            ))}
          </Space>
        </Spin>
      </Modal>
    </div>
  )
}
