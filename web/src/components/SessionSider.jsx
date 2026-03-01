import React from "react"
import { Button, List, Popconfirm, Spin, Tooltip } from "antd"

export default function SessionSider({
  sessions,
  activeSessionId,
  loadingSessions,
  onCreateSession,
  onDeleteSession,
  onSelectSession,
  className,
}) {
  return (
    <div className={`app-sider ${className || ""}`.trim()}>
      <Button
        size="large"
        className="new-session-button"
        onClick={onCreateSession}
        type="primary"
        style={{ width: "100%", marginBottom: "20px"}}
      >
        新建会话
      </Button>
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
              <div className="session-row">
                <div className="session-content">
                  <Tooltip title={item.name || item.session_id}>
                    <div className="session-name">{item.name || item.session_id}</div>
                  </Tooltip>
                  <div className="session-time">
                    {new Date(item.update_time).toLocaleString("zh-CN", {
                      year: "numeric",
                      month: "2-digit",
                      day: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                      second: "2-digit",
                      hour12: false,
                    })}
                  </div>
                </div>
                <div className="session-actions" onClick={(event) => event.stopPropagation()}>
                  <Popconfirm
                    title="确认删除该会话？"
                    okText="删除"
                    cancelText="取消"
                    onConfirm={() => onDeleteSession?.(item.session_id)}
                  >
                    <Button size="small" danger type="text" className="session-delete-btn">
                      删除
                    </Button>
                  </Popconfirm>
                </div>
              </div>
            </List.Item>
          )}
        />
      </Spin>
    </div>
  )
}
