import { useCallback, useEffect, useState } from "react";
import { message } from "antd";
import { deleteSession, listSessions, startSession } from "../api/index.js";
import {
  DEFAULT_DEVICE_ID,
  SESSION_STORAGE_KEY,
} from "../constants/appConfig.js";

export function useSessions({ isAuthenticated }) {
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [loadingSessions, setLoadingSessions] = useState(false);

  const loadSessions = useCallback(async () => {
    if (!isAuthenticated) {
      setSessions([]);
      return;
    }
    try {
      setLoadingSessions(true);
      const data = await listSessions();
      setSessions(Array.isArray(data) ? data : []);
    } catch (error) {
      message.error(`会话列表加载失败：${error.message}`);
    } finally {
      setLoadingSessions(false);
    }
  }, []);

  const startNewSession = useCallback(async () => {
    if (!isAuthenticated) {
      return;
    }
    try {
      const data = await startSession(DEFAULT_DEVICE_ID);
      if (data?.session_id) {
        localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);
        setActiveSessionId(data.session_id);
        await loadSessions();
      }
    } catch (error) {
      message.error(`创建会话失败：${error.message}`);
    }
  }, [loadSessions]);

  const deleteSessionById = useCallback(
    async (sessionId) => {
      if (!sessionId) {
        return;
      }
      try {
        await deleteSession(sessionId);
        const data = await listSessions();
        const nextSessions = Array.isArray(data) ? data : [];
        setSessions(nextSessions);
        if (sessionId === activeSessionId) {
          const nextSessionId = nextSessions[0]?.session_id || null;
          if (nextSessionId) {
            setActiveSessionId(nextSessionId);
            localStorage.setItem(SESSION_STORAGE_KEY, nextSessionId);
          } else {
            localStorage.removeItem(SESSION_STORAGE_KEY);
            setActiveSessionId(null);
          }
        }
        message.success(`会话 ${sessionId} 删除成功`);
      } catch (error) {
        message.error(`删除会话失败：${error.message}`);
      }
    },
    [activeSessionId],
  );

  const ensureSession = useCallback(async () => {
    const cachedSession = localStorage.getItem(SESSION_STORAGE_KEY);
    if (cachedSession) {
      setActiveSessionId(cachedSession);
      return;
    }
    await startNewSession();
  }, [startNewSession]);

  const selectSession = useCallback((sessionId) => {
    if (!sessionId) {
      return;
    }
    setActiveSessionId(sessionId);
    localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
  }, []);

  useEffect(() => {
    if (!isAuthenticated) {
      setSessions([]);
      setActiveSessionId(null);
      return;
    }
    loadSessions();
    ensureSession();
  }, [ensureSession, isAuthenticated, loadSessions]);

  useEffect(() => {
    // 监听 403 “无权访问该会话”事件。
    // 这通常发生在：用户在 A 账号下创建了会话，退出后换 B 账号登录，
    // 但浏览器本地缓存的 session_id 还是 A 的，此时回源请求会报 403。
    const handleForbiddenSession = () => {
      console.warn("Detected stale/forbidden session, clearing local cache...");
      localStorage.removeItem(SESSION_STORAGE_KEY);
      setActiveSessionId(null);
      // 清理后重新尝试初始化（会触发新建会话）
      startNewSession();
    };
    window.addEventListener("tripnexus:forbidden-session", handleForbiddenSession);
    return () => {
      window.removeEventListener(
        "tripnexus:forbidden-session",
        handleForbiddenSession,
      );
    };
  }, [startNewSession]);

  return {
    activeSessionId,
    deleteSessionById,
    loadSessions,
    loadingSessions,
    selectSession,
    sessions,
    setActiveSessionId,
    startNewSession,
  };
}
