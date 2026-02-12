import { useCallback, useEffect, useState } from "react"
import { message } from "antd"
import { listSessions, startSession } from "../api/index.js"
import { DEFAULT_DEVICE_ID, DEFAULT_USER_ID, SESSION_STORAGE_KEY } from "../constants/appConfig.js"

export function useSessions() {
  const [sessions, setSessions] = useState([])
  const [activeSessionId, setActiveSessionId] = useState(null)
  const [loadingSessions, setLoadingSessions] = useState(false)

  const loadSessions = useCallback(async () => {
    try {
      setLoadingSessions(true)
      const data = await listSessions(DEFAULT_USER_ID)
      setSessions(Array.isArray(data) ? data : [])
    } catch (error) {
      message.error(`会话列表加载失败：${error.message}`)
    } finally {
      setLoadingSessions(false)
    }
  }, [])

  const startNewSession = useCallback(async () => {
    try {
      const data = await startSession(DEFAULT_USER_ID, DEFAULT_DEVICE_ID)
      if (data?.session_id) {
        localStorage.setItem(SESSION_STORAGE_KEY, data.session_id)
        setActiveSessionId(data.session_id)
        await loadSessions()
      }
    } catch (error) {
      message.error(`创建会话失败：${error.message}`)
    }
  }, [loadSessions])

  const ensureSession = useCallback(async () => {
    const cachedSession = localStorage.getItem(SESSION_STORAGE_KEY)
    if (cachedSession) {
      setActiveSessionId(cachedSession)
      return
    }
    await startNewSession()
  }, [startNewSession])

  const selectSession = useCallback((sessionId) => {
    if (!sessionId) {
      return
    }
    setActiveSessionId(sessionId)
    localStorage.setItem(SESSION_STORAGE_KEY, sessionId)
  }, [])

  useEffect(() => {
    loadSessions()
    ensureSession()
  }, [loadSessions, ensureSession])

  return {
    activeSessionId,
    loadSessions,
    loadingSessions,
    selectSession,
    sessions,
    setActiveSessionId,
    startNewSession,
  }
}
