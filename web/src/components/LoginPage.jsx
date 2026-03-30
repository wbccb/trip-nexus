import React, { useState } from "react";
import { Button, Card, Form, Input, Segmented, Typography, message } from "antd";

export default function LoginPage({ loading, onLogin, onRegister }) {
  // 登录页把登录和注册合并在一个组件里，
  // 这样用户从“首次注册”到“进入系统”只需要停留在一个界面完成。
  const [mode, setMode] = useState("login");
  const [form] = Form.useForm();

  const handleFinish = async (values) => {
    // 表单提交时根据当前 mode 切分到 login / register，
    // 但成功后的 UI 反馈统一在这里收口处理。
    try {
      if (mode === "login") {
        await onLogin?.({
          email: values.email,
          password: values.password,
        });
        message.success("登录成功");
        return;
      }
      await onRegister?.({
        email: values.email,
        password: values.password,
        nickname: values.nickname,
      });
      message.success("注册成功，已自动登录");
    } catch (error) {
      message.error(error.message || "认证失败");
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        background:
          "radial-gradient(circle at top, rgba(33,150,243,0.18), transparent 36%), linear-gradient(135deg, #f6f3ea 0%, #eef5ff 48%, #dce9f5 100%)",
      }}
    >
      <Card
        style={{
          width: "100%",
          maxWidth: 440,
          borderRadius: 24,
          boxShadow: "0 24px 80px rgba(28, 55, 90, 0.14)",
          body: {
            padding: 32,
          },
        }}
      >
        <Typography.Title level={2} style={{ marginBottom: 8 }}>
          TripNexus
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 24 }}>
          先登录，再开始你的 AI 行程规划。
        </Typography.Paragraph>
        <Segmented
          block
          value={mode}
          onChange={(value) => {
            // 切换模式时清空表单，避免“注册填过的昵称/密码规则状态”残留到登录模式。
            setMode(value);
            form.resetFields();
          }}
          options={[
            { label: "登录", value: "login" },
            { label: "注册", value: "register" },
          ]}
          style={{ marginBottom: 20 }}
        />
        <Form form={form} layout="vertical" onFinish={handleFinish}>
          {mode === "register" ? (
            <Form.Item label="昵称" name="nickname">
              <Input placeholder="例如：阿星" />
            </Form.Item>
          ) : null}
          <Form.Item
            label="邮箱"
            name="email"
            rules={[
              { required: true, message: "请输入邮箱" },
              { type: "email", message: "请输入有效邮箱地址" },
            ]}
          >
            <Input placeholder="you@example.com" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[
              { required: true, message: "请输入密码" },
              { min: 8, message: "密码至少 8 位" },
            ]}
          >
            <Input.Password placeholder="至少 8 位" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large" loading={loading}>
            {mode === "login" ? "登录并进入" : "注册并进入"}
          </Button>
        </Form>
      </Card>
    </div>
  );
}
