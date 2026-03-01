import React from "react"
import { Card, Divider, List, Space, Spin } from "antd"

export default function TripTab({ loadingTrip, tripDays, tripResult }) {
  return (
    <div className="trip-layout">
      <Card title="行程详情" className="panel-card">
        <Spin spinning={loadingTrip}>
          {!tripResult && <div className="empty-tip">暂无行程结果</div>}
          {tripResult && (
            <Space direction="vertical" size="middle" className="full-width">
              <div className="trip-summary">
                目的地：{tripResult.destination} · 天数：{tripResult.days}
              </div>
              <Divider />
              <List
                dataSource={tripDays}
                renderItem={(day) => (
                  <List.Item className="day-item">
                    <Card title={`第 ${day.day} 天`} size="small" className="day-card">
                      <List
                        dataSource={day.items}
                        renderItem={(item, index) => (
                          <List.Item className="poi-item">
                            <div className="poi-title">
                              {index + 1}. {item.attraction}
                            </div>
                            <div className="poi-meta">
                              时间：{item.time} · 地址：{item.address}
                            </div>
                            <div className="poi-meta">
                              交通：{item.transport} · 停留：{item.duration}
                            </div>
                          </List.Item>
                        )}
                      />
                    </Card>
                  </List.Item>
                )}
              />
            </Space>
          )}
        </Spin>
      </Card>
    </div>
  )
}
