import React from "react"
import { Button, List, Spin } from "antd"

export default function SessionSider({
  sessions,
  activeSessionId,
  loadingSessions,
  onCreateSession,
  onSelectSession,
  className,
}) {
  return (
    <div className={`app-sider ${className || ""}`.trim()}>
      <div className="sider-header">
        <div className="sider-title">会话列表</div>
        <Button size="small" className="new-session-button" onClick={onCreateSession}>
          新建会话
        </Button>
      </div>
      <Spin spinning={loadingSessions}>
        <List
          className="session-list"
          dataSource={sessions}
          renderItem={(item, index) => (
            <List.Item
              className={
                item.session_id === activeSessionId ? "session-item active" : "session-item"
              }
              onClick={() => onSelectSession(item.session_id)}
            >
              <div className="session-day">DAY {String(index + 1).padStart(2, "0")}</div>
              <div className="session-content">
                <div className="session-name">{item.name || item.session_id}</div>
                <div className="session-time">{item.update_time}</div>
              </div>
            </List.Item>
          )}
        />
      </Spin>
    </div>
  )
}
