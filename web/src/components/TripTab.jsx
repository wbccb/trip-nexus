import React from "react"
import {
  Button,
  Card,
  Divider,
  Form,
  Input,
  InputNumber,
  List,
  Space,
  Spin,
} from "antd"

export default function TripTab({ loadingTrip, onSubmit, tripDays, tripResult }) {
  const [form] = Form.useForm()

  return (
    <div className="tab-panel">
      <Card title="行程输入" className="panel-card">
        <Form form={form} layout="vertical" onFinish={onSubmit}>
          <Form.Item label="目的地" name="destination" rules={[{ required: true, message: "请输入目的地" }]}>
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
          <Button type="primary" htmlType="submit" loading={loadingTrip}>
            生成行程
          </Button>
        </Form>
      </Card>
      <Card title="行程结果" className="panel-card">
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
      <Card title="地图占位" className="panel-card">
        <div className="map-placeholder">地图占位（后续可替换为地图 iframe/图片）</div>
      </Card>
    </div>
  )
}
