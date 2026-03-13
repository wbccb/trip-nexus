import React, { useMemo } from "react"
import {
  Button,
  Card,
  Checkbox,
  Divider,
  Form,
  Input,
  InputNumber,
  Space,
  Table,
  Tag,
} from "antd"

const resolveStatusColor = (status) => {
  if (status === "running" || status === "planned") {
    return "processing"
  }
  if (status === "done") {
    return "success"
  }
  if (status === "failed") {
    return "error"
  }
  if (status === "paused") {
    return "warning"
  }
  return "default"
}

export default function AgentTab({
  agentState,
  agentForm,
  agentConnecting,
  onRunAgent,
  onReconnectAgent,
}) {
  const agentNodeRows = useMemo(() => {
    const nodes = agentState.nodes || {}
    return Object.keys(nodes).map((key) => ({
      key,
      node: key,
      status: nodes[key]?.status || "idle",
      taskCount: (nodes[key]?.tasks || []).length,
    }))
  }, [agentState.nodes])

  const agentTaskRows = useMemo(() => {
    const tasks = agentState.tasks || {}
    return Object.keys(tasks).map((taskId) => ({
      key: taskId,
      task_id: taskId,
      task_type: tasks[taskId]?.task_type,
      tool: tasks[taskId]?.tool,
      node: tasks[taskId]?.node,
      status: tasks[taskId]?.status,
      description: tasks[taskId]?.description,
      retry: agentState.retries?.[taskId] || 0,
    }))
  }, [agentState.retries, agentState.tasks])

  const agentEventRows = useMemo(() => {
    const events = agentState.events || []
    return events.map((event) => ({
      key: event.id,
      sequence: event.sequence,
      event: event.event,
      node: event.node,
      status: event.status,
      payload: event.payload,
    }))
  }, [agentState.events])

  const agentNodeColumns = useMemo(
    () => [
      {
        title: "节点",
        dataIndex: "node",
        key: "node",
      },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        render: (value) => <Tag color={resolveStatusColor(value)}>{value}</Tag>,
      },
      {
        title: "任务数",
        dataIndex: "taskCount",
        key: "taskCount",
      },
    ],
    []
  )

  const agentTaskColumns = useMemo(
    () => [
      {
        title: "任务 ID",
        dataIndex: "task_id",
        key: "task_id",
      },
      {
        title: "类型",
        dataIndex: "task_type",
        key: "task_type",
      },
      {
        title: "节点",
        dataIndex: "node",
        key: "node",
      },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        render: (value) => <Tag color={resolveStatusColor(value)}>{value}</Tag>,
      },
      {
        title: "重试",
        dataIndex: "retry",
        key: "retry",
      },
    ],
    []
  )

  const agentEventColumns = useMemo(
    () => [
      {
        title: "序号",
        dataIndex: "sequence",
        key: "sequence",
      },
      {
        title: "事件",
        dataIndex: "event",
        key: "event",
      },
      {
        title: "节点",
        dataIndex: "node",
        key: "node",
      },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        render: (value) => <Tag color={resolveStatusColor(value)}>{value}</Tag>,
      },
      {
        title: "Payload",
        dataIndex: "payload",
        key: "payload",
        render: (value) => JSON.stringify(value || {}),
      },
    ],
    []
  )

  return (
    <Space direction="vertical" size="large" className="full-width">
      <Card
        title="Agent 控制"
        extra={<Tag color={resolveStatusColor(agentState.status)}>{agentState.status}</Tag>}
      >
        <Form
          form={agentForm}
          layout="vertical"
          initialValues={{
            destination: "",
            days: 2,
            budget: "",
            preference: "",
            poi_query: "热门景点",
            poi_top_k: 5,
            weather_days: 3,
            manual_rag_review: false,
          }}
        >
          <Form.Item
            label="目的地"
            name="destination"
            rules={[{ required: true, message: "请输入目的地" }]}
          >
            <Input placeholder="例如：上海" />
          </Form.Item>
          <Form.Item
            label="天数"
            name="days"
            rules={[{ required: true, message: "请输入行程天数" }]}
          >
            <InputNumber min={1} max={30} className="full-width" />
          </Form.Item>
          <Form.Item label="预算" name="budget">
            <Input placeholder="例如：2000" />
          </Form.Item>
          <Form.Item label="偏好" name="preference">
            <Input placeholder="例如：美食、人文" />
          </Form.Item>
          <Divider />
          <Form.Item label="POI 查询关键词" name="poi_query">
            <Input placeholder="例如：热门景点" />
          </Form.Item>
          <Form.Item label="POI 数量" name="poi_top_k">
            <InputNumber min={1} max={20} className="full-width" />
          </Form.Item>
          <Form.Item label="天气天数" name="weather_days">
            <InputNumber min={1} max={7} className="full-width" />
          </Form.Item>
          <Form.Item name="manual_rag_review" valuePropName="checked">
            <Checkbox>启用 RAG 人工复核</Checkbox>
          </Form.Item>
          <Space>
            <Button type="primary" onClick={() => onRunAgent(false)}>
              启动 Agent
            </Button>
            <Button onClick={() => onRunAgent(true)} disabled={!agentState.threadId}>
              恢复执行
            </Button>
            <Button onClick={onReconnectAgent} loading={agentConnecting}>
              重连流
            </Button>
          </Space>
          <Divider />
          <Space direction="vertical" size="small">
            <div>线程 ID：{agentState.threadId || "未启动"}</div>
            <div>最新序号：{agentState.lastSequence || 0}</div>
          </Space>
        </Form>
      </Card>
      <Card title="执行队列">
        {agentState.queue.length === 0 && <div className="empty-tip">暂无执行队列</div>}
        {agentState.queue.length > 0 && (
          <Space wrap>
            {agentState.queue.map((taskId) => (
              <Tag key={taskId}>{taskId}</Tag>
            ))}
          </Space>
        )}
      </Card>
      <Card title="节点状态">
        <Table
          dataSource={agentNodeRows}
          columns={agentNodeColumns}
          pagination={false}
          size="small"
        />
      </Card>
      <Card title="任务列表">
        <Table
          dataSource={agentTaskRows}
          columns={agentTaskColumns}
          pagination={{ pageSize: 6 }}
          size="small"
        />
      </Card>
      <Card title="事件流">
        <Table
          dataSource={agentEventRows}
          columns={agentEventColumns}
          pagination={{ pageSize: 6 }}
          size="small"
        />
      </Card>
    </Space>
  )
}
