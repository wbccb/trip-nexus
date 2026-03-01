import React, { useMemo, useState } from "react"
import { Button, Card, Drawer, Form, Input, InputNumber, Layout, Modal, Tabs, Typography } from "antd"
import KnowledgeTab from "./components/KnowledgeTab.jsx"
import SessionSider from "./components/SessionSider.jsx"
import TripTab from "./components/TripTab.jsx"
import { useKnowledge } from "./hooks/useKnowledge.js"
import { useSessions } from "./hooks/useSessions.js"
import { useTrip } from "./hooks/useTrip.js"

const { Header, Content } = Layout

export default function App() {
  const {
    activeSessionId,
    loadSessions,
    loadingSessions,
    selectSession,
    sessions,
    setActiveSessionId,
    startNewSession,
  } = useSessions()
  const { handleTripSubmit, loadingTrip, tripDays, tripResult } = useTrip({
    activeSessionId,
    refreshSessions: loadSessions,
    setActiveSessionId,
  })
  const {
    handleKnowledgeSearch,
    knowledgeQuery,
    knowledgeResult,
    loadingKnowledge,
    setKnowledgeQuery,
  } = useKnowledge()
  const [chatMessages, setChatMessages] = useState([])
  const [chatInput, setChatInput] = useState("")
  const [isSessionDrawerOpen, setIsSessionDrawerOpen] = useState(false)
  const [isTripModalOpen, setIsTripModalOpen] = useState(false)
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

  const handleSendChat = () => {
    const value = chatInput.trim()
    if (!value) {
      return
    }
    setChatMessages((prev) => [
      ...prev,
      {
        id: `${Date.now()}-user`,
        role: "user",
        content: value,
      },
    ])
    setChatInput("")
  }

  const handleTripFormSubmit = async (values) => {
    const prompt = promptTemplate
      .replace("{destination}", values.destination)
      .replace("{days}", values.days)
      .replace("{budget}", values.budget || "未填写")
      .replace("{preference}", values.preference || "未填写")
    setChatMessages((prev) => [
      ...prev,
      {
        id: `${Date.now()}-user-trip`,
        role: "user",
        content: prompt,
      },
    ])
    setIsTripModalOpen(false)
    tripForm.resetFields()
    await handleTripSubmit(values)
    setChatMessages((prev) => [
      ...prev,
      {
        id: `${Date.now()}-assistant-trip`,
        role: "assistant",
        content: `已生成 ${values.destination} 的 ${values.days} 天游程，请查看中间行程详情。`,
      },
    ])
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
                {chatMessages.length === 0 && (
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
                  placeholder="输入要咨询的问题或想法"
                  autoSize={{ minRows: 1, maxRows: 3 }}
                />
                <Button type="primary" onClick={handleSendChat}>
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
                    />
                  ),
                },
                {
                  key: "knowledge",
                  label: "旅行灵感",
                  children: (
                    <KnowledgeTab
                      knowledgeQuery={knowledgeQuery}
                      knowledgeResult={knowledgeResult}
                      loadingKnowledge={loadingKnowledge}
                      onChangeQuery={setKnowledgeQuery}
                      onSearch={handleKnowledgeSearch}
                    />
                  ),
                },
              ]}
            />
          </div>
          <div className="app-right">
            <Card title="地图概览" className="panel-card map-card">
              <div className="map-placeholder map-large">
                地图占位（后续可替换为地图 iframe/图片）
              </div>
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
        </Form>
      </Modal>
    </Layout>
  )
}
