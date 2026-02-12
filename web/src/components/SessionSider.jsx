import React from "react"
import { Button, Layout, List, Spin } from "antd"

const { Sider } = Layout

export default function SessionSider({
  sessions,
  activeSessionId,
  loadingSessions,
  onCreateSession,
  onSelectSession,
}) {
  return (
    <Sider className="app-sider" width={280}>
      <div className="sider-title">会话列表</div>
      <Button className="new-session-button" onClick={onCreateSession}>
        新建会话
      </Button>
      <Spin spinning={loadingSessions}>
        <List
          className="session-list"
          dataSource={sessions}
          renderItem={(item) => (
            <List.Item
              className={
                item.session_id === activeSessionId ? "session-item active" : "session-item"
              }
              onClick={() => onSelectSession(item.session_id)}
            >
              <div className="session-name">{item.name || item.session_id}</div>
              <div className="session-time">{item.update_time}</div>
            </List.Item>
          )}
        />
      </Spin>
    </Sider>
  )
}
