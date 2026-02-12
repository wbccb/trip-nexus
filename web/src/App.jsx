import React from "react"
import { Layout, Tabs, Typography } from "antd"
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

  return (
    <Layout className="app-root">
      <SessionSider
        sessions={sessions}
        activeSessionId={activeSessionId}
        loadingSessions={loadingSessions}
        onCreateSession={startNewSession}
        onSelectSession={selectSession}
      />
      <Layout>
        <Header className="app-header">
          <Typography.Title level={4} className="app-title">
            TripNexus · React + Vite
          </Typography.Title>
        </Header>
        <Content className="app-content">
          <Tabs
            defaultActiveKey="trip"
            items={[
              {
                key: "trip",
                label: "行程生成",
                children: (
                  <TripTab
                    loadingTrip={loadingTrip}
                    onSubmit={handleTripSubmit}
                    tripDays={tripDays}
                    tripResult={tripResult}
                  />
                ),
              },
              {
                key: "knowledge",
                label: "知识库检索",
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
        </Content>
      </Layout>
    </Layout>
  )
}
