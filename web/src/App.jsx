import React, { useCallback, useEffect, useMemo, useState } from "react"
import {
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Layout,
  Modal,
  Select,
  Spin,
  Tabs,
  Typography,
  message,
} from "antd"
import KnowledgeTab from "./components/KnowledgeTab.jsx"
import SessionSider from "./components/SessionSider.jsx"
import TripTab from "./components/TripTab.jsx"
import TripMap from "./components/TripMap.jsx"
import { getSessionHistory, getSessionTrip, sendChatMessage } from "./api/index.js"
import {
  DEFAULT_DEVICE_ID,
  DEFAULT_USER_ID,
  SESSION_STORAGE_KEY,
} from "./constants/appConfig.js"
import { useKnowledge } from "./hooks/useKnowledge.js"
import { useSessions } from "./hooks/useSessions.js"
import { useTrip } from "./hooks/useTrip.js"

const { Header, Content } = Layout

export default function App() {
  const {
    activeSessionId,
    deleteSessionById,
    loadSessions,
    loadingSessions,
    selectSession,
    sessions,
    setActiveSessionId,
    startNewSession,
  } = useSessions()
  const {
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
    selectedKnowledgeBaseId,
    setKnowledgeGenerateQuery,
    setKnowledgeQuery,
    setSelectedKnowledgeBaseId,
    uploadingKnowledge,
  } = useKnowledge()
  const {
    handleTripSubmit,
    handleReplanDay,
    loadingTrip,
    persistTripResult,
    tripDays,
    tripResult,
    updateTripResult,
  } = useTrip({
    activeSessionId,
    knowledgeGenerateQuery,
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
  const [selectedPoiId, setSelectedPoiId] = useState("")
  const [tripForm] = Form.useForm()

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
        user_id: DEFAULT_USER_ID,
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

  // 处理行程表单提交
  const handleTripFormSubmit = async (values) => {
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
        content: prompt,
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
    await handleTripSubmit(values, {
      // 流开始回调
      onStreamStart: () => {
        // 更新为加载提示
        updateChatMessageById(assistantMessageId, "行程生成中...")
      },
      // 流增量回调
      onStreamDelta: (nextText) => {
        // 缓存最新文本
        streamingText = nextText || ""
        // 更新聊天内容
        updateChatMessageById(assistantMessageId, streamingText || "行程生成中...")
      },
      // 流结束回调
      onStreamEnd: () => {
        // 若无流内容则使用默认提示
        if (!streamingText) {
          // 更新聊天内容
          updateChatMessageById(
            assistantMessageId,
            `已生成 ${values.destination} 的 ${values.days} 天游程，请查看中间行程详情。`
          )
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

  return (
    <Layout className="app-root">
      <Header className="app-header">
        <div className="header-left">
          <Typography.Title level={4} className="app-title">
            AI 行程助手
          </Typography.Title>
          <Typography.Text className="app-subtitle">
            目的地规划 · 生成行程 · 地图概览
          </Typography.Text>
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
            <Tabs
              defaultActiveKey="trip"
              items={[
                {
                  key: "trip",
                  label: "行程详情",
                  children: (
                    <TripTab
                      loadingTrip={loadingTrip}
                      tripDays={tripDays}
                      tripResult={tripResult}
                      selectedPoiId={selectedPoiId}
                      onSelectPoi={handleSelectPoi}
                      onTripChange={async (nextTrip) => {
                        updateTripResult(nextTrip)
                        await persistTripResult(nextTrip)
                      }}
                      onReplanDay={handleReplanDay}
                    />
                  ),
                },
                {
                  key: "knowledge",
                  label: "旅行灵感",
                  children: (
                    <KnowledgeTab
                      knowledgeBases={knowledgeBases}
                      knowledgeGenerateQuery={knowledgeGenerateQuery}
                      knowledgeQuery={knowledgeQuery}
                      knowledgeResult={knowledgeResult}
                      loadingKnowledge={loadingKnowledge}
                      loadingKnowledgeBases={loadingKnowledgeBases}
                      onCreateKnowledgeBase={handleCreateKnowledgeBase}
                      onDeleteKnowledgeBase={handleDeleteKnowledgeBase}
                      onSelectKnowledgeBase={setSelectedKnowledgeBaseId}
                      onUploadKnowledgeDocument={handleUploadKnowledgeDocument}
                      onChangeGenerateQuery={setKnowledgeGenerateQuery}
                      onChangeQuery={setKnowledgeQuery}
                      onSearch={handleKnowledgeSearch}
                      selectedKnowledgeBaseId={selectedKnowledgeBaseId}
                      uploadingKnowledge={uploadingKnowledge}
                    />
                  ),
                },
                {
                  key: "mode",
                  label: "执行模式",
                  children: (
                    <Card size="small" title="主流程模式">
                      当前已切换为单主流程架构。请在“行程输入”中选择极速模式（fast）或深度模式（deep）。
                    </Card>
                  ),
                },
              ]} 
            />
          </div>
          <div className="app-right">
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
          </div>
        </div>
      </Content>
      <Drawer
        title="会话列表"
        placement="left"
        width={320}
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
        <Form form={tripForm} layout="vertical" onFinish={handleTripFormSubmit}>
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
          <Form.Item label="执行模式" name="mode" initialValue="fast">
            <Select
              options={[
                { label: "极速模式（fast）", value: "fast" },
                { label: "深度模式（deep）", value: "deep" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Layout>
  )
}
