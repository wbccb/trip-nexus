import React, { useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  Drawer,
  Input,
  InputNumber,
  Space,
  Statistic,
  Tabs,
  Table,
  Tag,
  Typography,
  message,
  Select,
} from "antd";
import {
  getAdminAuditLogs,
  getAdminDashboard,
  getAdminUserTokenUsage,
  listAdminUsers,
  updateAdminUserQuota,
  updateAdminUserStatus,
} from "../api/adminApi.js";

function getStatusTag(status) {
  // 管理页里状态和角色都用 Tag 做视觉映射，避免表格纯文本难以扫读。
  return status === "active" ? <Tag color="green">active</Tag> : <Tag color="red">banned</Tag>;
}

function getRoleTag(role) {
  return role === "admin" ? <Tag color="gold">admin</Tag> : <Tag>user</Tag>;
}

export default function AdminPage() {
  // quotaDrafts 是一个“表格内临时编辑态”：
  // 用户在 InputNumber 里输入的新配额先只存在前端草稿里，点击保存后再真正提交到后端。
  const [dashboard, setDashboard] = useState(null);
  const [users, setUsers] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [quotaDrafts, setQuotaDrafts] = useState({});
  const [auditLogs, setAuditLogs] = useState([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [loadingAuditLogs, setLoadingAuditLogs] = useState(false);
  const [auditAction, setAuditAction] = useState("");
  // 这组筛选状态共同组成“请求时间线”视图：
  // action 适合运营按动作排查，user/session/message/path 适合开发按链路还原一次完整请求轨迹。
  const [auditUserId, setAuditUserId] = useState("");
  const [auditSessionId, setAuditSessionId] = useState("");
  const [auditMessageId, setAuditMessageId] = useState("");
  const [auditRequestPath, setAuditRequestPath] = useState("");
  const [tokenUsageVisible, setTokenUsageVisible] = useState(false);
  const [tokenUsageLoading, setTokenUsageLoading] = useState(false);
  const [tokenUsageTarget, setTokenUsageTarget] = useState(null);
  const [tokenUsageRows, setTokenUsageRows] = useState([]);
  const [tokenUsageTotal, setTokenUsageTotal] = useState(0);

  const loadDashboard = async () => {
    // Dashboard 和用户列表分开请求，避免只改一个用户状态时必须重新拉整页大表后才能刷新顶部概览。
    const data = await getAdminDashboard();
    setDashboard(data || null);
  };

  const loadUsers = async (nextPage = page, nextPageSize = pageSize, nextKeyword = keyword) => {
    // 列表请求会同时把后端返回的 token_quota 同步进 quotaDrafts，
    // 这样表格里的输入框默认值始终和数据库当前值对齐。
    setLoading(true);
    try {
      const data = await listAdminUsers({
        page: nextPage,
        pageSize: nextPageSize,
        keyword: nextKeyword,
      });
      const items = Array.isArray(data?.items) ? data.items : [];
      setUsers(items);
      setTotal(Number(data?.total || 0));
      setQuotaDrafts((prev) => {
        const nextDrafts = { ...prev };
        items.forEach((item) => {
          nextDrafts[item.user_id] = Number(item.token_quota || 0);
        });
        return nextDrafts;
      });
    } finally {
      setLoading(false);
    }
  };

  const loadAuditLogs = async (overrides = {}) => {
    setLoadingAuditLogs(true);
    try {
      // overrides 让“单个控件变化时立即刷新”和“点击筛选按钮统一提交”两种交互都能复用同一套请求逻辑。
      const mergedFilters = {
        action: auditAction,
        userId: auditUserId,
        sessionId: auditSessionId,
        messageId: auditMessageId,
        requestPath: auditRequestPath,
        ...overrides,
      };
      const data = await getAdminAuditLogs({
        limit: 100,
        action: mergedFilters.action || undefined,
        userId: mergedFilters.userId || undefined,
        sessionId: mergedFilters.sessionId || undefined,
        messageId: mergedFilters.messageId || undefined,
        requestPath: mergedFilters.requestPath || undefined,
      });
      setAuditLogs(Array.isArray(data?.items) ? data.items : []);
      setAuditTotal(Number(data?.total || 0));
    } finally {
      setLoadingAuditLogs(false);
    }
  };

  const loadUserTokenUsage = async (user) => {
    if (!user?.user_id) {
      return;
    }
    setTokenUsageVisible(true);
    setTokenUsageTarget(user);
    setTokenUsageLoading(true);
    try {
      const data = await getAdminUserTokenUsage(user.user_id, { limit: 100 });
      setTokenUsageRows(Array.isArray(data?.items) ? data.items : []);
      setTokenUsageTotal(Number(data?.total || 0));
    } finally {
      setTokenUsageLoading(false);
    }
  };

  useEffect(() => {
    // 管理页初次进入时先把统计卡片和用户列表一起拉起来，用户会更快看到完整后台状态。
    loadDashboard();
    loadUsers(1, pageSize, "");
    loadAuditLogs();
  }, []);

  const columns = useMemo(
    () => [
      {
        title: "用户",
        key: "identity",
        render: (_, record) => (
          <div>
            <div>{record.nickname || "未命名用户"}</div>
            <Typography.Text type="secondary">{record.email}</Typography.Text>
          </div>
        ),
      },
      {
        title: "角色",
        dataIndex: "role",
        key: "role",
        render: (value) => getRoleTag(value),
      },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        render: (value) => getStatusTag(value),
      },
      {
        title: "额度",
        key: "quota",
        render: (_, record) => (
          <Space>
            <InputNumber
              min={0}
              value={quotaDrafts[record.user_id]}
              onChange={(value) =>
                setQuotaDrafts((prev) => ({
                  ...prev,
                  [record.user_id]: Number(value || 0),
                }))
              }
            />
            <Button
              size="small"
              onClick={async () => {
                // 配额更新成功后，同时刷新 dashboard 和列表，
                // 保证顶部汇总数字和表格行内数字都即时一致。
                try {
                  await updateAdminUserQuota(record.user_id, quotaDrafts[record.user_id] || 0);
                  message.success("额度已更新");
                  await Promise.all([loadDashboard(), loadUsers(page, pageSize, keyword)]);
                } catch (error) {
                  message.error(error.message || "额度更新失败");
                }
              }}
            >
              保存
            </Button>
          </Space>
        ),
      },
      {
        title: "已用",
        key: "used",
        render: (_, record) => `${record.token_used || 0} / ${record.token_quota || 0}`,
      },
      {
        title: "注册时间",
        dataIndex: "created_at",
        key: "created_at",
      },
      {
        title: "操作",
        key: "actions",
        render: (_, record) => (
          <Space>
            <Button size="small" onClick={() => loadUserTokenUsage(record)}>
              Token 日志
            </Button>
            <Button
              danger={record.status === "active"}
              type={record.status === "active" ? "primary" : "default"}
              onClick={async () => {
                // 封禁/解封是立即生效的管理动作。
                // 后端写库成功后，这里也会马上刷新页面状态，避免用户误以为没生效。
                try {
                  await updateAdminUserStatus(
                    record.user_id,
                    record.status === "active" ? "banned" : "active",
                  );
                  message.success("用户状态已更新");
                  await Promise.all([loadDashboard(), loadUsers(page, pageSize, keyword), loadAuditLogs()]);
                } catch (error) {
                  message.error(error.message || "状态更新失败");
                }
              }}
            >
              {record.status === "active" ? "封禁" : "解封"}
            </Button>
          </Space>
        ),
      },
    ],
    [auditAction, keyword, page, pageSize, quotaDrafts],
  );

  const auditColumns = useMemo(
    () => [
      { title: "时间", dataIndex: "created_at", key: "created_at", width: 180 },
      { title: "动作", dataIndex: "action", key: "action", width: 180 },
      { title: "状态", dataIndex: "status", key: "status", width: 120 },
      {
        title: "用户",
        key: "user",
        render: (_, record) => record.user_email || `user#${record.user_id || "-"}`,
      },
      { title: "会话", dataIndex: "session_id", key: "session_id", width: 220 },
      { title: "消息", dataIndex: "message_id", key: "message_id", width: 220 },
      { title: "路径", dataIndex: "request_path", key: "request_path", width: 220 },
      {
        title: "详情",
        key: "detail_json",
        render: (_, record) => (
          <Typography.Text ellipsis={{ tooltip: record.detail_json }}>
            {record.detail_json}
          </Typography.Text>
        ),
      },
    ],
    [],
  );

  const tokenUsageColumns = useMemo(
    () => [
      { title: "时间", dataIndex: "created_at", key: "created_at", width: 180 },
      { title: "阶段", dataIndex: "stage", key: "stage", width: 180 },
      { title: "模型", dataIndex: "model_name", key: "model_name", width: 180 },
      { title: "接口", dataIndex: "request_path", key: "request_path", width: 220 },
      { title: "Prompt", dataIndex: "prompt_tokens", key: "prompt_tokens", width: 100 },
      { title: "Completion", dataIndex: "completion_tokens", key: "completion_tokens", width: 120 },
      { title: "Total", dataIndex: "total_tokens", key: "total_tokens", width: 100 },
      { title: "Message", dataIndex: "message_id", key: "message_id" },
    ],
    [],
  );

  return (
    <div style={{ display: "grid", gap: 16, overflow: "auto" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 16,
        }}
      >
        <Card><Statistic title="总用户" value={dashboard?.total_users || 0} /></Card>
        <Card><Statistic title="活跃用户" value={dashboard?.active_users || 0} /></Card>
        <Card><Statistic title="封禁用户" value={dashboard?.banned_users || 0} /></Card>
        <Card><Statistic title="剩余额度" value={dashboard?.quota_remaining || 0} /></Card>
      </div>
      <Tabs
        items={[
          {
            key: "users",
            label: "用户管理",
            children: (
              <Card
                extra={
                  <Space>
                    <Input.Search
                      // 搜索行为直接回源，让分页总数、过滤后的用户集都以服务端结果为准。
                      placeholder="搜索邮箱或昵称"
                      allowClear
                      onSearch={(value) => {
                        setKeyword(value || "");
                        setPage(1);
                        loadUsers(1, pageSize, value || "");
                      }}
                      style={{ width: 260 }}
                    />
                    <Button
                      onClick={async () => {
                        await Promise.all([
                          loadDashboard(),
                          loadUsers(page, pageSize, keyword),
                          loadAuditLogs(),
                        ]);
                      }}
                    >
                      刷新
                    </Button>
                  </Space>
                }
              >
                <Table
                  rowKey="user_id"
                  loading={loading}
                  columns={columns}
                  dataSource={users}
                  pagination={{
                    current: page,
                    pageSize,
                    total,
                    showSizeChanger: true,
                    onChange: (nextPage, nextPageSize) => {
                      setPage(nextPage);
                      setPageSize(nextPageSize);
                      loadUsers(nextPage, nextPageSize, keyword);
                    },
                  }}
                />
              </Card>
            ),
          },
          {
            key: "audit",
            label: "请求时间线",
            children: (
              <Card
                extra={
                  <Space>
                    <Select
                      allowClear
                      placeholder="筛选动作"
                      value={auditAction || undefined}
                      onChange={(value) => {
                        const nextAction = value || "";
                        setAuditAction(nextAction);
                        loadAuditLogs({ action: nextAction });
                      }}
                      style={{ width: 220 }}
                      options={[
                        { label: "登录", value: "auth_login" },
                        { label: "注册", value: "auth_register" },
                        { label: "会话创建", value: "session_start" },
                        { label: "会话列表", value: "session_list" },
                        { label: "会话历史", value: "session_history" },
                        { label: "会话行程", value: "session_trip" },
                        { label: "行程更新", value: "trip_update" },
                        { label: "局部重排", value: "trip_replan_day" },
                        { label: "知识库列表", value: "knowledge_bases_list" },
                        { label: "知识库创建", value: "knowledge_base_create" },
                        { label: "知识库删除", value: "knowledge_base_delete" },
                        { label: "知识库上传", value: "knowledge_upload" },
                        { label: "链接预处理", value: "knowledge_preprocess_url" },
                        { label: "链接导入", value: "knowledge_ingest_url" },
                        { label: "聊天发送", value: "chat_send" },
                        { label: "主流程开始", value: "flow_stream_started" },
                        { label: "主流程完成", value: "flow_stream_completed" },
                        { label: "限流拦截", value: "rate_limit_blocked" },
                        { label: "额度拦截", value: "quota_blocked" },
                        { label: "管理员改状态", value: "admin_update_user_status" },
                        { label: "管理员改额度", value: "admin_update_user_quota" },
                      ]}
                    />
                    <Input
                      placeholder="用户 ID"
                      value={auditUserId}
                      onChange={(event) => setAuditUserId(event.target.value)}
                      style={{ width: 120 }}
                    />
                    <Input
                      placeholder="Session ID"
                      value={auditSessionId}
                      onChange={(event) => setAuditSessionId(event.target.value)}
                      style={{ width: 180 }}
                    />
                    <Input
                      placeholder="Message ID"
                      value={auditMessageId}
                      onChange={(event) => setAuditMessageId(event.target.value)}
                      style={{ width: 180 }}
                    />
                    <Input
                      placeholder="请求路径"
                      value={auditRequestPath}
                      onChange={(event) => setAuditRequestPath(event.target.value)}
                      style={{ width: 220 }}
                    />
                    <Button onClick={() => loadAuditLogs()}>筛选</Button>
                    <Button
                      onClick={() => {
                        // 时间线筛选项比较多，这里提供一个一键清空，方便运营快速回到全局视角。
                        setAuditAction("");
                        setAuditUserId("");
                        setAuditSessionId("");
                        setAuditMessageId("");
                        setAuditRequestPath("");
                        loadAuditLogs({
                          action: "",
                          userId: "",
                          sessionId: "",
                          messageId: "",
                          requestPath: "",
                        });
                      }}
                    >
                      清空
                    </Button>
                    <Button onClick={() => loadAuditLogs()}>刷新日志</Button>
                  </Space>
                }
              >
                <Table
                  rowKey="id"
                  loading={loadingAuditLogs}
                  columns={auditColumns}
                  dataSource={auditLogs}
                  pagination={{
                    pageSize: 20,
                    total: auditTotal,
                    showSizeChanger: false,
                  }}
                  scroll={{ x: 1100 }}
                />
              </Card>
            ),
          },
        ]}
      />
      <Drawer
        title={tokenUsageTarget ? `${tokenUsageTarget.nickname || tokenUsageTarget.email} 的 Token 使用日志` : "Token 使用日志"}
        placement="right"
        width={900}
        open={tokenUsageVisible}
        onClose={() => setTokenUsageVisible(false)}
      >
        <div style={{ marginBottom: 12 }}>
          <Typography.Text type="secondary">
            最近 {tokenUsageRows.length} 条，累计总记录 {tokenUsageTotal} 条
          </Typography.Text>
        </div>
        <Table
          rowKey="id"
          loading={tokenUsageLoading}
          columns={tokenUsageColumns}
          dataSource={tokenUsageRows}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          scroll={{ x: 1200 }}
        />
      </Drawer>
    </div>
  );
}
