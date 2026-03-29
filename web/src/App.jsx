import React, { useCallback, useEffect, useMemo, useState } from "react"
import {
  Button,
  Card,
  Checkbox,
  Collapse,
  Drawer,
  Form,
  Input,
  InputNumber,
  Layout,
  Modal,
  Radio,
  Select,
  Spin,
  Tabs,
  Typography,
  message,
} from "antd"
import AdminPage from "./components/AdminPage.jsx"
import KnowledgeTab from "./components/KnowledgeTab.jsx"
import LoginPage from "./components/LoginPage.jsx"
import SessionSider from "./components/SessionSider.jsx"
import TripTab from "./components/TripTab.jsx"
import TripMap from "./components/TripMap.jsx"
import { getSessionHistory, getSessionTrip, sendChatMessage } from "./api/index.js"
import {
  DEFAULT_DEVICE_ID,
  SESSION_STORAGE_KEY,
} from "./constants/appConfig.js"
import { useAuth } from "./hooks/useAuth.js"
import { useKnowledge } from "./hooks/useKnowledge.js"
import { useSessions } from "./hooks/useSessions.js"
import { useTrip } from "./hooks/useTrip.js"

const { Header, Content } = Layout

const DEFAULT_TRIP_CONSTRAINTS = {
  // 这些默认值与后端 _normalize_trip_constraints 的兜底语义保持一致，
  // 保证用户不展开“约束设置”时，前后端看到的是同一份默认约束。
  budget_level: "balanced",
  intensity: "standard",
  pace: "cultural",
  walking_limit_km: null,
  need_nap: false,
  accessibility: false,
}

function buildConstraintSummary(values) {
  // 提交前先在聊天区/表单摘要里展示一份简短描述，
  // 方便用户确认这次生成将带着哪些结构化约束进入主流程。
  const budgetLabelMap = {
    economy: "经济",
    balanced: "均衡",
    comfortable: "舒适",
  }
  const intensityLabelMap = {
    leisure: "休闲",
    standard: "标准",
    extreme: "特种兵",
  }
  const paceLabelMap = {
    cultural: "文化探索",
    efficient: "打卡效率",
    family_friendly: "亲子友好",
  }
  const parts = [
    `预算档位 ${budgetLabelMap[values?.budget_level] || budgetLabelMap.balanced}`,
    `体能强度 ${intensityLabelMap[values?.intensity] || intensityLabelMap.standard}`,
    `节奏偏好 ${paceLabelMap[values?.pace] || paceLabelMap.cultural}`,
  ]
  if (Number.isFinite(Number(values?.walking_limit_km)) && Number(values.walking_limit_km) > 0) {
    parts.push(`步行上限 ${Number(values.walking_limit_km)}km/天`)
  }
  if (values?.need_nap) {
    parts.push("需要午休")
  }
  if (values?.accessibility) {
    parts.push("需要无障碍")
  }
  return parts.join(" | ")
}

export default function App() {
  // App 现在是“认证壳 + 业务工作台”双态结构：
  // 未登录只渲染 LoginPage，已登录后再装载会话、知识库、行程、管理后台等业务能力。
  const { authLoading, authReady, authUser, isAuthenticated, login, logout, register } = useAuth()
  const {
    activeSessionId,
    deleteSessionById,
    loadSessions,
    loadingSessions,
    selectSession,
    sessions,
    setActiveSessionId,
    startNewSession,
  } = useSessions({ isAuthenticated })
  const {
    handleCreateKnowledgeBase,
    handleDeleteKnowledgeBase,
    handleDeleteKnowledgeSource,
    handleIngestKnowledgeUrl,
    handleKnowledgeSearch,
    handleLoadKnowledgeDebugSnapshot,
    handlePreprocessKnowledgeUrl,
    handleUpdateKnowledgeSource,
    handleUploadKnowledgeDocument,
    ingestingKnowledge,
    knowledgeBases,
    knowledgeDebugSnapshot,
    knowledgeGenerateQuery,
    knowledgeQuery,
    knowledgeResult,
    knowledgeScope,
    knowledgeSources,
    knowledgeUrlPreprocessResult,
    lastIngestResult,
    loadingKnowledge,
    loadingKnowledgeBases,
    loadingKnowledgeDebugSnapshot,
    loadingKnowledgeSources,
    preprocessingKnowledgeUrl,
    refreshKnowledgeSources,
    selectedKnowledgeBaseId,
    setKnowledgeGenerateQuery,
    setKnowledgeQuery,
    setKnowledgeScope,
    setSelectedKnowledgeBaseId,
    sourceStats,
    uploadingKnowledge,
  } = useKnowledge({ isAuthenticated })
  const {
    conflictReport,
    handleFlowSubmit,
    handleFlowReplanDay,
    lockedDays,
    loadingTrip,
    persistFlowTripResult,
    selectedAlternative,
    setConflictReport,
    setLockedDays,
    setSelectedAlternative,
    tripDays,
    tripResult,
    updateTripResult,
  } = useTrip({
    activeSessionId,
    knowledgeGenerateQuery,
    knowledgeScope,
    refreshSessions: loadSessions,
    selectedKnowledgeBaseId,
    setActiveSessionId,
  })
  const [chatMessages, setChatMessages] = useState([])
  const [chatInput, setChatInput] = useState("")
  const [loadingChatHistory, setLoadingChatHistory] = useState(false)
  const [sendingChat, setSendingChat] = useState(false)
  const [isSessionDrawerOpen, setIsSessionDrawerOpen] = useState(false)
  const [isTripModalOpen, setIsTripModalOpen] = useState(false)
  const [isWorkspaceModalOpen, setIsWorkspaceModalOpen] = useState(false)
  const [selectedPoiId, setSelectedPoiId] = useState("")
  const [flowRuntimeStatus, setFlowRuntimeStatus] = useState({
    intent: "",
    latencyMs: 0,
    contextCount: 0,
    contextChars: 0,
    contextBudget: {
      max_items: 0,
      item_max_chars: 0,
      total_max_chars: 0,
    },
  })
  const [lastFlowKnowledgeDebug, setLastFlowKnowledgeDebug] = useState(null)
  const [tripForm] = Form.useForm()
  tripForm.setFieldsValue({
     destination: "成都",
      days: 1,
      budget: 3000,
      preference: "美食", 
      budget_level: "economy",
      intensity: "leisure",
      pace: "cultural",
      walking_limit_km: 5,
      need_nap: true,
      accessibility: false,
  })
  const isAdmin = authUser?.role === "admin"

  const sessionTitle = useMemo(() => {
    const activeSession = sessions.find((item) => item.session_id === activeSessionId)
    return "当前选择的会话：" + (activeSession?.name || activeSessionId || "未选择会话")
  }, [activeSessionId, sessions])

  const promptTemplate = useMemo(
    () =>
      "请根据以下信息生成行程：目的地 {destination}，天数 {days} 天，预算 {budget}，偏好 {preference}。请给出每天安排、交通方式、停留时长与地址。",
    []
  )

  const handleSelectPoi = useCallback((poiId) => {
    if (!poiId) {
      return
    }
    setSelectedPoiId(String(poiId))
  }, [])


  // 根据消息 ID 更新聊天内容
  const updateChatMessageById = useCallback((messageId, nextContent) => {
    // 更新消息列表
    setChatMessages((prev) =>
      // 映射生成新数组
      prev.map((item) =>
        // 匹配指定消息
        item.id === messageId
          ? {
              // 保留原字段
              ...item,
              // 更新内容
              content: nextContent,
            }
          : item
      )
    )
  }, [])
  const loadChatHistory = useCallback(async (sessionId) => {
    if (!sessionId) {
      setChatMessages([])
      updateTripResult(null)
      return
    }
    try {
      setLoadingChatHistory(true)
      const [historyData, tripData] = await Promise.all([
        getSessionHistory(sessionId),
        getSessionTrip(sessionId),
      ])
      const normalized = Array.isArray(historyData)
        ? historyData.map((item, index) => ({
            id: `${sessionId}-${item.timestamp || Date.now()}-${index}`,
            role: item.role,
            content: item.content,
          }))
        : []
      setChatMessages(normalized)
      if (tripData?.trip_data) {
        updateTripResult(tripData.trip_data)
      } else {
        updateTripResult(null)
      }
    } catch (error) {
      message.error(`聊天记录加载失败：${error.message}`)
      setChatMessages([])
      updateTripResult(null)
    } finally {
      setLoadingChatHistory(false)
    }
  }, [updateTripResult])

  useEffect(() => {
    loadChatHistory(activeSessionId)
  }, [activeSessionId, loadChatHistory])


  // 聊天消息点击发送
  const handleSendChat = async () => {
    const value = chatInput.trim()
    if (!value || sendingChat) {
      return
    }
    const userMessageId = `${Date.now()}-user`
    setChatMessages((prev) => [
      ...prev,
      {
        id: userMessageId,
        role: "user",
        content: value,
      },
    ])
    setChatInput("")
    try {
      setSendingChat(true)
      const payload = {
        device_id: DEFAULT_DEVICE_ID,
        session_id: activeSessionId,
        message: value,
      }
      const data = await sendChatMessage(payload)
      if (data?.session_id && data.session_id !== activeSessionId) {
        setActiveSessionId(data.session_id)
        localStorage.setItem(SESSION_STORAGE_KEY, data.session_id)
      }
      // 更新AI返回的消息到聊天框中
      if (data?.response) {
        setChatMessages((prev) => [
          ...prev,
          {
            id: `${Date.now()}-assistant`,
            role: "assistant",
            content: data.response,
          },
        ])
      }
      // 更新中间布局的行程详情
      if (data?.trip_data) {
        updateTripResult(data.trip_data)
      }
      // 重新加载会话列表
      if (loadSessions) {
        await loadSessions()
      }
    } catch (error) {
      message.error(`消息发送失败：${error.message}`)
    } finally {
      setSendingChat(false)
    }
  }

  // 处理主流程表单提交
  const handleFlowFormSubmit = async (values) => {
    setFlowRuntimeStatus({
      intent: "",
      latencyMs: 0,
      contextCount: 0,
      contextChars: 0,
      contextBudget: {
        max_items: 0,
        item_max_chars: 0,
        total_max_chars: 0,
      },
    })
    // 生成提示词文本
    const prompt = promptTemplate
      // 替换目的地
      .replace("{destination}", values.destination)
      // 替换天数
      .replace("{days}", values.days)
      // 替换预算
      .replace("{budget}", values.budget || "未填写")
      // 替换偏好
      .replace("{preference}", values.preference || "未填写")
    const constraintSummary = buildConstraintSummary(values)
    // 构造用户消息 ID
    const userMessageId = `${Date.now()}-user-trip`
    // 构造助手消息 ID
    const assistantMessageId = `${Date.now()}-assistant-trip`
    // 写入用户消息
    setChatMessages((prev) => [
      // 保留历史消息
      ...prev,
      {
        // 用户消息 ID
        id: userMessageId,
        // 角色标识
        role: "user",
        // 提示词内容
        content: `${prompt}\n约束：${constraintSummary}`,
      },
      {
        // 助手消息 ID
        id: assistantMessageId,
        // 角色标识
        role: "assistant",
        // 初始内容
        content: "行程生成中...",
      },
    ])
    // 关闭弹窗
    setIsTripModalOpen(false)
    // 重置表单
    tripForm.resetFields()
    // 初始化流式文本
    let streamingText = ""
    // 调用行程生成（流式）
    await handleFlowSubmit(values, {
      // 流开始回调
      onStreamStart: () => {
        // 更新为加载提示
        updateChatMessageById(assistantMessageId, "行程生成中...")
      },
      // 流增量回调
      onStreamDelta: (nextText, event) => {
        if (event?.step === "intent" && event?.payload?.intent) {
          setFlowRuntimeStatus((prev) => ({
            ...prev,
            intent: String(event.payload.intent || ""),
          }))
        }
        // 只有真正的生成文本才写入聊天气泡，避免把流程状态误当成最终回答
        if (event?.step === "generate") {
          streamingText = nextText || ""
          updateChatMessageById(assistantMessageId, streamingText || "行程生成中...")
        }
      },
      // 流结束回调
      onStreamEnd: (_, event) => {
        const payload = event?.payload || {}
        const metrics = payload?.metrics || {}
        const sourceEvidence = Array.isArray(payload?.source_evidence)
          ? payload.source_evidence
          : []
        const knowledgeDebug = payload?.knowledge_debug || {}
        const finalizedTripData = payload?.trip_data || null
        const finalizedResponseText = String(payload?.response_text || "").trim()
        setLastFlowKnowledgeDebug({
          knowledge_scope: String(knowledgeDebug?.knowledge_scope || metrics?.knowledge_scope || ""),
          allow_public_fusion: Boolean(knowledgeDebug?.allow_public_fusion),
          kb_context_count: Number(knowledgeDebug?.kb_context_count || 0),
          source_evidence_count: Number(knowledgeDebug?.source_evidence_count || sourceEvidence.length || 0),
          source_evidence: sourceEvidence,
        })
        if (sourceEvidence.length > 0) {
          message.success(`主流程命中私有知识 ${sourceEvidence.length} 条来源`)
        } else if (knowledgeScope === "private_plus_public" && selectedKnowledgeBaseId) {
          message.info("主流程未命中私有知识，请点击旅行灵感中的调试按钮查看导入内容")
        }
        setFlowRuntimeStatus((prev) => ({
          ...prev,
          intent: String(metrics?.intent || prev.intent || ""),
          latencyMs: Number(metrics?.latency_ms || 0),
          contextCount: Number(metrics?.context_count || 0),
          contextChars: Number(metrics?.context_chars || 0),
          contextBudget: {
            max_items: Number(metrics?.context_budget?.max_items || 0),
            item_max_chars: Number(metrics?.context_budget?.item_max_chars || 0),
            total_max_chars: Number(metrics?.context_budget?.total_max_chars || 0),
          },
        }))
        if (finalizedTripData) {
          updateChatMessageById(
            assistantMessageId,
            `已生成 ${values.destination} 的 ${values.days} 天游程，请查看中间行程详情。`
          )
        } else if (finalizedResponseText) {
          updateChatMessageById(assistantMessageId, finalizedResponseText)
        } else if (!streamingText) {
          updateChatMessageById(assistantMessageId, "本次流程已结束，但没有拿到可展示的行程结果，请稍后查看后端日志。")
        }
      },
      // 行程数据回调
      onTripData: () => {
        // 若流内容为空则提示完成
        if (!streamingText) {
          // 更新聊天内容
          updateChatMessageById(
            assistantMessageId,
            `已生成 ${values.destination} 的 ${values.days} 天游程，请查看中间行程详情。`
          )
        }
      },
    })
  }

  if (!authReady) {
    return (
      <div style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
        <Spin size="large" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return <LoginPage loading={authLoading} onLogin={login} onRegister={register} />
  }

  // Tab 列表在这里集中拼装，而不是散在 JSX 里临时判断，
  // 这样“普通用户无管理页，管理员多一个管理页”这类权限差异会更直观。
  const tripContent = (
    <TripTab
      conflictReport={conflictReport}
      lockedDays={lockedDays}
      loadingTrip={loadingTrip}
      selectedAlternative={selectedAlternative}
      tripDays={tripDays}
      tripResult={tripResult}
      selectedPoiId={selectedPoiId}
      onApplyAlternative={async (nextTrip) => {
        updateTripResult(nextTrip)
        setConflictReport({ has_conflicts: false, conflicts: [], alternatives: [] })
        setSelectedAlternative(null)
        await persistFlowTripResult(nextTrip)
      }}
      onConflictReportChange={setConflictReport}
      onLockedDaysChange={setLockedDays}
      onSelectAlternative={setSelectedAlternative}
      onSelectPoi={handleSelectPoi}
      onTripChange={async (nextTrip) => {
        updateTripResult(nextTrip)
        await persistFlowTripResult(nextTrip)
      }}
      onReplanDay={handleFlowReplanDay}
    />
  )

  const workspaceTabItems = [
    {
      key: "knowledge",
      label: "旅行灵感",
      children: (
        <KnowledgeTab
          knowledgeBases={knowledgeBases}
          knowledgeDebugSnapshot={knowledgeDebugSnapshot}
          knowledgeGenerateQuery={knowledgeGenerateQuery}
          knowledgeQuery={knowledgeQuery}
          knowledgeResult={knowledgeResult}
          knowledgeScope={knowledgeScope}
          knowledgeSources={knowledgeSources}
          lastFlowKnowledgeDebug={lastFlowKnowledgeDebug}
          sourceStats={sourceStats}
          loadingKnowledgeDebugSnapshot={loadingKnowledgeDebugSnapshot}
          loadingKnowledgeSources={loadingKnowledgeSources}
          ingestingKnowledge={ingestingKnowledge}
          preprocessingKnowledgeUrl={preprocessingKnowledgeUrl}
          knowledgeUrlPreprocessResult={knowledgeUrlPreprocessResult}
          lastIngestResult={lastIngestResult}
          loadingKnowledge={loadingKnowledge}
          loadingKnowledgeBases={loadingKnowledgeBases}
          onCreateKnowledgeBase={handleCreateKnowledgeBase}
          onDeleteKnowledgeBase={handleDeleteKnowledgeBase}
          onDeleteKnowledgeSource={handleDeleteKnowledgeSource}
          onIngestKnowledgeUrl={handleIngestKnowledgeUrl}
          onPreprocessKnowledgeUrl={handlePreprocessKnowledgeUrl}
          onLoadKnowledgeDebugSnapshot={handleLoadKnowledgeDebugSnapshot}
          onRefreshKnowledgeSources={refreshKnowledgeSources}
          onUpdateKnowledgeSource={handleUpdateKnowledgeSource}
          onSelectKnowledgeBase={setSelectedKnowledgeBaseId}
          onUploadKnowledgeDocument={handleUploadKnowledgeDocument}
          onChangeGenerateQuery={setKnowledgeGenerateQuery}
          onChangeQuery={setKnowledgeQuery}
          onChangeKnowledgeScope={setKnowledgeScope}
          onSearch={handleKnowledgeSearch}
          selectedKnowledgeBaseId={selectedKnowledgeBaseId}
          uploadingKnowledge={uploadingKnowledge}
        />
      ),
    },
  ]

  if (isAdmin) {
    workspaceTabItems.push({
      key: "admin",
      label: "管理",
      children: <AdminPage />,
    })
  }

  return (
    <Layout className="app-root">
      <Header className="app-header">
        <div className="header-left">
          <Typography.Title level={4} className="app-title">
            AI 行程助手
            <Button type="primary" size="small" onClick={() => setIsWorkspaceModalOpen(true)} style={{ marginLeft: 20 }}>
                旅行灵感与管理
            </Button>
          </Typography.Title>
          <Typography.Text className="app-subtitle">
            目的地规划 · 生成行程 · 地图概览
          </Typography.Text>
        </div>
        <div className="header-right" style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Typography.Text>
            {authUser?.nickname || authUser?.email || "已登录用户"}
          </Typography.Text>
          <Button size="small" onClick={logout}>
            退出登录
          </Button>
        </div>
      </Header>
      <Content className="app-content">
        <div className="app-body">
          <div className="app-left">
            <div className="chat-panel">
              <div className="chat-header">
                <div className="chat-title">
                  <div className="chat-title-main">
                    <div>AI 助手</div>
                    <div className="chat-actions">
                  <Button size="small" onClick={() => setIsTripModalOpen(true)}>
                    行程输入
                  </Button>
                  <Button size="small" onClick={() => setIsSessionDrawerOpen(true)}>
                    会话列表
                  </Button>
                </div>
                    </div>
                  <div className="chat-title-sub">{sessionTitle}</div>
                </div>
              </div>
              <div className="chat-history">
                {loadingChatHistory && chatMessages.length === 0 && (
                  <div className="empty-tip">聊天记录加载中...</div>
                )}
                {!loadingChatHistory && chatMessages.length === 0 && (
                  <div className="empty-tip">暂无聊天记录</div>
                )}
                {chatMessages.map((item) => (
                  <div key={item.id} className={`chat-message ${item.role}`}>
                    <div className="chat-bubble">{item.content}</div>
                  </div>
                ))}
              </div>
              <div className="chat-input-floating">
                <Input.TextArea
                  value={chatInput}
                  onChange={(event) => setChatInput(event.target.value)}
                  onKeyDown={(event) => {
                    // Enter 触发发送
                    if (event.key === "Enter" && !event.shiftKey) {
                      // 阻止默认换行
                      event.preventDefault()
                      // 发送聊天消息
                      handleSendChat()
                    }
                  }}
                  placeholder="输入要咨询的问题或想法"
                  autoSize={{ minRows: 1, maxRows: 3 }}
                />
                <Button type="primary" onClick={handleSendChat} loading={sendingChat}>
                  发送
                </Button>
              </div>
            </div>
          </div>
          <div className="app-main">
            {tripContent}
          </div>
          {/* <div className="app-right">
            <Card title="地图概览" className="panel-card map-card">
              {!tripResult && (
                <div className="map-placeholder map-large">
                  地图占位（后续可替换为地图 iframe/图片）
                </div>
              )}
              {tripResult && (
                <div className="map-frame">
                  <div className="map-meta">
                    <div className="trip-summary">
                      目的地：{tripResult.destination} · 天数：{tripResult.days}
                    </div>
                    <div className="poi-meta">
                      已规划天数：{tripDays.length} · 行程项：{tripDays.reduce((sum, day) => sum + day.items.length, 0)}
                    </div>
                  </div>
                  <div className="map-view">
                    <TripMap
                      tripResult={tripResult}
                      selectedPoiId={selectedPoiId}
                      onSelectPoi={handleSelectPoi}
                    />
                  </div>
                </div>
              )}
            </Card>
          </div> */}
        </div>
      </Content>
      <Drawer
        title="会话列表"
        placement="left"
        size="default"
        open={isSessionDrawerOpen}
        onClose={() => setIsSessionDrawerOpen(false)}
      >
        <SessionSider
          className="session-drawer"
          sessions={sessions}
          activeSessionId={activeSessionId}
          loadingSessions={loadingSessions}
          onCreateSession={startNewSession}
          onDeleteSession={deleteSessionById}
          onSelectSession={(sessionId) => {
            selectSession(sessionId)
            setIsSessionDrawerOpen(false)
          }}
        />
      </Drawer>
      <Modal
        title="行程输入"
        open={isTripModalOpen}
        onCancel={() => setIsTripModalOpen(false)}
        onOk={() => tripForm.submit()}
        okText="生成行程"
        confirmLoading={loadingTrip}
      >
        <Form
          form={tripForm}
          layout="vertical"
          initialValues={DEFAULT_TRIP_CONSTRAINTS}
          onFinish={handleFlowFormSubmit}
        >
          <Form.Item
            label="目的地"
            name="destination"
            rules={[{ required: true, message: "请输入目的地" }]}
          >
            <Input placeholder="例如：成都" />
          </Form.Item>
          <Form.Item label="天数" name="days" rules={[{ required: true, message: "请输入天数" }]}>
            <InputNumber min={1} className="full-width" placeholder="例如：3" />
          </Form.Item>
          <Form.Item label="预算" name="budget">
            <Input placeholder="例如：3000 元" />
          </Form.Item>
          <Form.Item label="偏好" name="preference">
            <Input placeholder="例如：人文、美食" />
          </Form.Item>
          <Collapse
            items={[
              {
                key: "constraints",
                label: "约束设置",
                children: (
                  <>
                    <Form.Item label="预算档位" name="budget_level">
                      <Radio.Group
                        optionType="button"
                        buttonStyle="solid"
                        options={[
                          { label: "经济", value: "economy" },
                          { label: "均衡", value: "balanced" },
                          { label: "舒适", value: "comfortable" },
                        ]}
                      />
                    </Form.Item>
                    <Form.Item label="体能强度" name="intensity">
                      <Radio.Group
                        optionType="button"
                        buttonStyle="solid"
                        options={[
                          { label: "休闲", value: "leisure" },
                          { label: "标准", value: "standard" },
                          { label: "特种兵", value: "extreme" },
                        ]}
                      />
                    </Form.Item>
                    <Form.Item label="节奏偏好" name="pace">
                      <Radio.Group
                        optionType="button"
                        buttonStyle="solid"
                        options={[
                          { label: "文化探索", value: "cultural" },
                          { label: "打卡效率", value: "efficient" },
                          { label: "亲子友好", value: "family_friendly" },
                        ]}
                      />
                    </Form.Item>
                    <Form.Item label="步行上限（km/天）" name="walking_limit_km">
                      <InputNumber min={1} max={50} className="full-width" placeholder="可选，例如 5" />
                    </Form.Item>
                    <Form.Item name="need_nap" valuePropName="checked">
                      <Checkbox>需要午休</Checkbox>
                    </Form.Item>
                    <Form.Item name="accessibility" valuePropName="checked">
                      <Checkbox>需要无障碍</Checkbox>
                    </Form.Item>
                  </>
                ),
              },
            ]}
          />
        </Form>
      </Modal>
      <Modal
        title="旅行灵感与管理"
        open={isWorkspaceModalOpen}
        onCancel={() => setIsWorkspaceModalOpen(false)}
        footer={null}
        width={1200}
        destroyOnHidden
      >
        <Tabs defaultActiveKey="knowledge" items={workspaceTabItems} />
      </Modal>
    </Layout>
  )
}
