import React from "react"
import { Button, Card, Divider, Input, List, Space, Spin } from "antd"

export default function KnowledgeTab({
  knowledgeQuery,
  knowledgeResult,
  loadingKnowledge,
  onChangeQuery,
  onSearch,
}) {
  return (
    <div className="tab-panel">
      <Card title="检索输入" className="panel-card">
        <Space direction="vertical" className="full-width">
          <Input
            placeholder="输入要检索的问题或主题"
            value={knowledgeQuery}
            onChange={(event) => onChangeQuery(event.target.value)}
          />
          <Button type="primary" onClick={onSearch} loading={loadingKnowledge}>
            开始检索
          </Button>
        </Space>
      </Card>
      <Card title="检索结果" className="panel-card">
        <Spin spinning={loadingKnowledge}>
          {!knowledgeResult && <div className="empty-tip">暂无检索结果</div>}
          {knowledgeResult && (
            <Space direction="vertical" size="middle" className="full-width">
              <div className="trip-summary">查询：{knowledgeResult.query}</div>
              <Divider />
              <List
                dataSource={knowledgeResult.evidence?.summary?.items || []}
                renderItem={(item, index) => (
                  <List.Item className="poi-item">
                    <div className="poi-title">
                      {index + 1}. {item.title || "无标题"}
                    </div>
                    <div className="poi-meta">{item.text}</div>
                    <div className="poi-meta">来源：{item.source || "未知来源"}</div>
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
