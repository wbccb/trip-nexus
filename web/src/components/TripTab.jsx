import { Card, Divider, Space, Spin, Table } from "antd"

export default function TripTab({ loadingTrip, tripDays, tripResult }) {
  const tableData = Array.isArray(tripDays)
    ? tripDays.flatMap((day) =>
        (day.items || []).map((item, index) => ({
          key: `${day.day}-${index}`,
          day: day.day,
          time: item.time,
          attraction: item.attraction,
          address: item.address,
          transport: item.transport,
          duration: item.duration,
          intro: item.description || item.introduction || item.note || item.summary || "",
        }))
      )
    : []

  const columns = [
    {
      title: "时间",
      dataIndex: "time",
      key: "time",
      render: (value, record) => (
        <Space direction="vertical" size={0} style={{ width: "100px" }}>
          <div>{`第 ${record.day} 天`}</div>
          <div>{value || "未提供"}</div>
        </Space>
      ),
    },
    {
      title: "地点",
      dataIndex: "attraction",
      key: "attraction",
      render: (value, record) => (
        <Space direction="vertical" size={0}>
          <div>{value || "未提供"}</div>
          <div>{record.address || "未提供"}</div>
        </Space>
      ),
    },
    {
      title: "交通",
      dataIndex: "transport",
      key: "transport",
      render: (value) => value || "未提供",
    },
    {
      title: "停留时间",
      dataIndex: "duration",
      key: "duration",
      render: (value, record) => (
        <div style={{ width: "64px" }}>
          {record.intro ? `${value || "未提供"} · ${record.intro}` : value || "未提供"}
        </div>
      ),
    },
  ]

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
              <Table
                dataSource={tableData}
                columns={columns}
                pagination={false}
                size="small"
                locale={{ emptyText: "暂无行程安排" }}
              />
            </Space>
          )}
        </Spin>
      </Card>
    </div>
  )
}
