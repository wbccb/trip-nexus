import { useCallback, useEffect, useState } from "react";
import { message } from "antd";
import {
  getUserProfile,
  loginUser,
  registerUser,
} from "../api/authApi.js";
import { clearAuthToken, getAuthToken, setAuthToken } from "../api/httpClient.js";
import { AGENT_SEQUENCE_STORAGE_KEY, AGENT_THREAD_STORAGE_KEY, SESSION_STORAGE_KEY } from "../constants/appConfig.js";

export function useAuth() {
  // useAuth 是前端登录态的唯一事实来源：
  // App、登录页、Header、Admin 权限判断都依赖这里返回的 authUser / isAuthenticated。
  const [authUser, setAuthUser] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [authLoading, setAuthLoading] = useState(false);

  const clearClientState = useCallback(() => {
    // 退出登录或 token 失效时，不只清 token，
    // 还要把和当前用户强绑定的 session / agent 续传状态一起清掉，避免串会话。
    clearAuthToken();
    localStorage.removeItem(SESSION_STORAGE_KEY);
    localStorage.removeItem(AGENT_THREAD_STORAGE_KEY);
    localStorage.removeItem(AGENT_SEQUENCE_STORAGE_KEY);
    setAuthUser(null);
  }, []);

  const bootstrapAuth = useCallback(async () => {
    // 页面首次启动时，如果本地已有 token，就立即回源拉 profile 恢复登录态；
    // 这样刷新页面后用户不需要重新登录。
    const token = getAuthToken();
    if (!token) {
      setAuthReady(true);
      setAuthUser(null);
      return;
    }
    try {
      const profile = await getUserProfile();
      setAuthUser(profile || null);
    } catch (error) {
      clearClientState();
    } finally {
      setAuthReady(true);
    }
  }, [clearClientState]);

  const handleAuthSuccess = useCallback(async (authPayload) => {
    // register / login 两条链路最终都统一走这里收口，
    // 保证“写 token + 写 authUser”的动作不会分散在多个地方。
    if (!authPayload?.token) {
      throw new Error("认证响应缺少 token");
    }
    setAuthToken(authPayload.token);
    if (authPayload?.profile) {
      setAuthUser(authPayload.profile);
      return authPayload.profile;
    }
    const profile = await getUserProfile();
    setAuthUser(profile || null);
    return profile || null;
  }, []);

  const login = useCallback(async (payload) => {
    setAuthLoading(true);
    try {
      const data = await loginUser(payload);
      return await handleAuthSuccess(data);
    } finally {
      setAuthLoading(false);
    }
  }, [handleAuthSuccess]);

  const register = useCallback(async (payload) => {
    setAuthLoading(true);
    try {
      const data = await registerUser(payload);
      return await handleAuthSuccess(data);
    } finally {
      setAuthLoading(false);
    }
  }, [handleAuthSuccess]);

  const logout = useCallback(() => {
    clearClientState();
  }, [clearClientState]);

  const refreshProfile = useCallback(async () => {
    // 某些页面操作后如果用户角色/昵称变化，可以复用这条方法做一次轻量刷新。
    const profile = await getUserProfile();
    setAuthUser(profile || null);
    return profile || null;
  }, []);

  useEffect(() => {
    bootstrapAuth();
  }, [bootstrapAuth]);

  useEffect(() => {
    // 监听 httpClient 广播出的 401 事件，集中处理“登录失效”这类横切逻辑。
    const handleUnauthorized = async () => {
      message.warning("登录状态已失效，请重新登录");
      clearClientState();
    };
    window.addEventListener("tripnexus:unauthorized", handleUnauthorized);
    return () => {
      window.removeEventListener("tripnexus:unauthorized", handleUnauthorized);
    };
  }, [clearClientState]);

  return {
    authLoading,
    authReady,
    authUser,
    isAuthenticated: Boolean(authUser && getAuthToken()),
    login,
    logout,
    refreshProfile,
    register,
  };
}
